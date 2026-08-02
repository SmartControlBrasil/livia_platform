from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from integrations.models import OutboxEvent, TenantWebhookConfig, WebhookDeliveryLog
from integrations.smart360.client import Smart360GrowthClient, requests as smart360_requests
from integrations.webhooks.service import WebhookDispatchService, requests as webhook_requests
from leads.models import LeadDraft
from leads.services.crm_dispatch import CRMDispatchService
from leads.services.handoff_notification import HandoffNotificationService
from conversations.models import HandoffRequest

from .payloads import SCHEMA_VERSION, short_text


@dataclass(frozen=True)
class HandlerResult:
    status: str
    code: str = ""
    message: str = ""
    retryable: bool = False
    metadata: dict = field(default_factory=dict)


def succeeded(code="succeeded", message="", metadata=None):
    return HandlerResult("succeeded", code=code, message=message, metadata=metadata or {})


def skipped(code="skipped", message="", metadata=None):
    return HandlerResult("skipped", code=code, message=message, metadata=metadata or {})


def retryable_failure(code="retryable_failure", message="", metadata=None):
    return HandlerResult("retryable_failure", code=code, message=message, retryable=True, metadata=metadata or {})


def permanent_failure(code="permanent_failure", message="", metadata=None):
    return HandlerResult("permanent_failure", code=code, message=message, metadata=metadata or {})


class BaseOutboxHandler:
    def handle(self, event: OutboxEvent) -> HandlerResult:
        self._validate_schema(event)
        return self.process(event)

    def process(self, event: OutboxEvent) -> HandlerResult:  # pragma: no cover - abstract convention
        raise NotImplementedError

    def _validate_schema(self, event: OutboxEvent):
        if event.payload.get("schema_version") != SCHEMA_VERSION:
            raise PermanentOutboxError("invalid_schema_version", "Unsupported outbox schema_version.")


class PermanentOutboxError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LeadQualifiedHandler(BaseOutboxHandler):
    def process(self, event: OutboxEvent) -> HandlerResult:
        lead = LeadDraft.objects.select_related("tenant", "conversation").filter(pk=event.aggregate_id, tenant=event.tenant).first()
        if lead is None:
            return permanent_failure("aggregate_not_found", "LeadDraft not found for tenant.")
        crm_result = self._dispatch_smart360(event, lead)
        webhook_result = self._dispatch_webhooks(event, lead)
        if crm_result.retryable or webhook_result.retryable:
            return retryable_failure("delivery_retryable", "One or more lead deliveries failed temporarily.", {"smart360": crm_result.metadata, "webhooks": webhook_result.metadata})
        if crm_result.status == "permanent_failure" or webhook_result.status == "permanent_failure":
            return permanent_failure("delivery_permanent", "One or more lead deliveries failed permanently.", {"smart360": crm_result.metadata, "webhooks": webhook_result.metadata})
        if crm_result.status == "skipped" and webhook_result.status == "skipped":
            return skipped("all_deliveries_skipped", "All lead deliveries were skipped.", {"smart360": crm_result.metadata, "webhooks": webhook_result.metadata})
        return succeeded("lead_delivered", metadata={"smart360": crm_result.metadata, "webhooks": webhook_result.metadata})

    def _dispatch_smart360(self, event, lead):
        if not getattr(settings, "SMART360_LEAD_DISPATCH_ENABLED", False) and not getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True):
            return skipped("smart360_disabled", "Smart360 disabled.", {"enabled": False})
        if getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True):
            client = Smart360GrowthClient(base_url=str(getattr(settings, "SMART360_BASE_URL", "") or ""), token="", dry_run=True)
        else:
            if not getattr(settings, "SMART360_BASE_URL", "") or not getattr(settings, "SMART360_M2M_TOKEN", ""):
                return permanent_failure("smart360_missing_config", "Smart360 config missing.")
            client = Smart360GrowthClient(settings.SMART360_BASE_URL, settings.SMART360_M2M_TOKEN, dry_run=False)
        payload = CRMDispatchService(client=client).build_payload(lead)
        payload_dict = payload.__dict__ if hasattr(payload, "__dict__") else dict(payload)
        payload_dict["event_id"] = str(event.event_id)
        try:
            result = client.ingest_lead(payload_dict, idempotency_key=str(event.event_id))
        except Exception as exc:
            return retryable_failure(exc.__class__.__name__, "Smart360 request failed.")
        metadata = {"dry_run": result.dry_run, "status_code": result.status_code, "external_id": result.external_id or ""}
        if result.success:
            if lead.status != LeadDraft.Status.SENT_TO_CRM:
                lead.status = LeadDraft.Status.SENT_TO_CRM
                lead.crm_external_id = result.external_id or lead.crm_external_id
                lead.crm_error = ""
                from django.utils import timezone
                lead.sent_to_crm_at = timezone.now()
                lead.save(update_fields=["status", "crm_external_id", "crm_error", "sent_to_crm_at", "updated_at"])
            return succeeded("smart360_succeeded", result.message, metadata)
        if _is_retryable_status(result.status_code):
            return retryable_failure("smart360_retryable", result.message, metadata)
        return permanent_failure("smart360_permanent", result.message, metadata)

    def _dispatch_webhooks(self, event, lead):
        logs = WebhookDispatchService().dispatch_lead_qualified(lead, event_id=str(event.event_id), envelope=event.payload)
        return _classify_webhook_logs(logs)


class HandoffCreatedHandler(BaseOutboxHandler):
    def process(self, event: OutboxEvent) -> HandlerResult:
        handoff = HandoffRequest.objects.select_related("tenant", "conversation", "lead_draft").filter(pk=event.aggregate_id, tenant=event.tenant).first()
        if handoff is None:
            return permanent_failure("aggregate_not_found", "HandoffRequest not found for tenant.")
        notification_result = HandoffNotificationService().notify(handoff)
        webhook_logs = WebhookDispatchService().dispatch_handoff_created(handoff, event_id=str(event.event_id), envelope=event.payload)
        webhook_result = _classify_webhook_logs(webhook_logs)
        notification_meta = {"success": notification_result.success, "dry_run": notification_result.dry_run}
        if webhook_result.retryable:
            return retryable_failure("delivery_retryable", metadata={"notification": notification_meta, "webhooks": webhook_result.metadata})
        if webhook_result.status == "permanent_failure":
            return permanent_failure("delivery_permanent", metadata={"notification": notification_meta, "webhooks": webhook_result.metadata})
        if notification_result.success or webhook_result.status == "succeeded":
            return succeeded("handoff_delivered", metadata={"notification": notification_meta, "webhooks": webhook_result.metadata})
        return skipped("handoff_skipped", metadata={"notification": notification_meta, "webhooks": webhook_result.metadata})


class ConversationSummaryReadyHandler(BaseOutboxHandler):
    def process(self, event: OutboxEvent) -> HandlerResult:
        logs = WebhookDispatchService().dispatch_event(event.tenant, TenantWebhookConfig.EventType.CONVERSATION_SUMMARY, event.payload, event_id=str(event.event_id), envelope=event.payload)
        return _classify_webhook_logs(logs)


def _classify_webhook_logs(logs):
    if not logs:
        return skipped("webhooks_not_configured", "No webhook configs matched.", {"count": 0})
    statuses = [log.status for log in logs]
    metadata = {"count": len(logs), "statuses": statuses, "status_codes": [log.status_code for log in logs if log.status_code]}
    if any(log.status == WebhookDeliveryLog.Status.FAILED and _is_retryable_status(log.status_code) for log in logs):
        return retryable_failure("webhook_retryable", metadata=metadata)
    if any(log.status == WebhookDeliveryLog.Status.FAILED for log in logs):
        return permanent_failure("webhook_permanent", metadata=metadata)
    if any(log.status == WebhookDeliveryLog.Status.SENT for log in logs):
        return succeeded("webhook_succeeded", metadata=metadata)
    return skipped("webhook_skipped", metadata=metadata)


def _is_retryable_status(status_code):
    if status_code in {408, 425, 429}:
        return True
    if status_code is not None and 500 <= int(status_code) <= 599:
        return True
    if status_code in {None, 0, 503}:
        return True
    return False
