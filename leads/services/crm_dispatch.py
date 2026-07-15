from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from assistant_core.summary import build_conversation_summary, format_conversation_summary_notes
from integrations.smart360.client import Smart360GrowthClient
from integrations.smart360.contracts import LeadIngestPayload

from ..models import LeadDraft

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CRMDispatchResult:
    attempted: bool
    success: bool
    dry_run: bool
    lead_draft: LeadDraft
    external_id: str = ""
    message: str = ""


class CRMDispatchService:
    def __init__(self, client: Smart360GrowthClient | None = None):
        self.client = client

    def _current_dry_run(self) -> bool:
        if self.client is not None:
            return bool(getattr(self.client, "dry_run", True))
        return bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True))

    def dispatch_if_qualified(self, lead_draft: LeadDraft) -> CRMDispatchResult:
        if lead_draft.status == LeadDraft.Status.SENT_TO_CRM:
            self._log_event(
                "crm_dispatch_ignored_already_sent",
                lead_draft=lead_draft,
            )
            return CRMDispatchResult(
                attempted=False,
                success=True,
                dry_run=self._current_dry_run(),
                lead_draft=lead_draft,
                external_id=lead_draft.crm_external_id,
                message="Lead já enviado ao CRM.",
            )

        if lead_draft.status != LeadDraft.Status.QUALIFIED:
            self._log_event(
                "crm_dispatch_ignored_not_qualified",
                lead_draft=lead_draft,
            )
            return CRMDispatchResult(
                attempted=False,
                success=False,
                dry_run=self._current_dry_run(),
                lead_draft=lead_draft,
                message="Lead ainda não qualificado.",
            )

        payload = self.build_payload(lead_draft)
        if settings.SMART360_LEAD_DISPATCH_DRY_RUN:
            client = self._get_client(dry_run=True)
            self._log_event(
                "crm_dispatch_attempt",
                lead_draft=lead_draft,
                external_id=self._build_mock_external_id(lead_draft),
            )
            response = client.ingest_lead(payload)
            return self._finalize_response(lead_draft, response)

        if not settings.SMART360_LEAD_DISPATCH_ENABLED:
            self._log_event(
                "crm_dispatch_ignored_disabled",
                lead_draft=lead_draft,
            )
            return CRMDispatchResult(
                attempted=False,
                success=False,
                dry_run=False,
                lead_draft=lead_draft,
                message="Despacho Smart360 desabilitado por configuração.",
            )

        if not self._has_real_dispatch_config():
            error_message = "Configuração Smart360 incompleta para despacho real."
            lead_draft.status = LeadDraft.Status.FAILED
            lead_draft.crm_error = error_message
            lead_draft.save(update_fields=["status", "crm_error", "updated_at"])
            self._log_event(
                "crm_dispatch_failure_missing_config",
                lead_draft=lead_draft,
            )
            logger.error(
                "event=crm_dispatch_failure_missing_config lead_draft_id=%s tenant_slug=%s status=%s",
                lead_draft.id,
                lead_draft.tenant.slug,
                lead_draft.status,
            )
            return CRMDispatchResult(
                attempted=False,
                success=False,
                dry_run=False,
                lead_draft=lead_draft,
                message=error_message,
            )

        client = self._get_client(dry_run=False)
        self._log_event("crm_dispatch_attempt", lead_draft=lead_draft, external_id="")
        response = client.ingest_lead(payload)
        return self._finalize_response(lead_draft, response)

    def build_payload(self, lead_draft: LeadDraft) -> LeadIngestPayload:
        conversation = lead_draft.conversation
        summary_notes = ""
        if conversation is not None:
            summary_notes = format_conversation_summary_notes(
                build_conversation_summary(conversation, lead_draft=lead_draft)
            )
        return LeadIngestPayload(
            tenant_slug=lead_draft.tenant.slug,
            name=lead_draft.name,
            company=lead_draft.company,
            email=lead_draft.email,
            phone=lead_draft.phone,
            city=lead_draft.city,
            need_summary=lead_draft.need_summary,
            notes=summary_notes,
            source_page=conversation.source_page if conversation else "",
            conversation_id=str(conversation.session_id if conversation else lead_draft.id),
        )

    def _build_mock_external_id(self, lead_draft: LeadDraft) -> str:
        conversation = lead_draft.conversation
        conversation_id = conversation.session_id if conversation else lead_draft.id
        return f"dry-run-{lead_draft.tenant.slug}-{conversation_id}"

    def _get_client(self, *, dry_run: bool) -> Smart360GrowthClient:
        if self.client is not None:
            return self.client
        return Smart360GrowthClient(
            base_url=str(getattr(settings, "SMART360_BASE_URL", "") or "").strip(),
            token=str(getattr(settings, "SMART360_M2M_TOKEN", "") or "").strip(),
            dry_run=dry_run,
        )

    def _has_real_dispatch_config(self) -> bool:
        base_url = str(getattr(settings, "SMART360_BASE_URL", "") or "").strip()
        token = str(getattr(settings, "SMART360_M2M_TOKEN", "") or "").strip()
        return bool(base_url and token)

    def _finalize_response(self, lead_draft: LeadDraft, response):
        if response.success:
            external_id = response.external_id or self._build_mock_external_id(lead_draft)
            lead_draft.status = LeadDraft.Status.SENT_TO_CRM
            lead_draft.crm_external_id = external_id
            lead_draft.crm_error = ""
            lead_draft.sent_to_crm_at = timezone.now()
            lead_draft.save(
                update_fields=[
                    "status",
                    "crm_external_id",
                    "crm_error",
                    "sent_to_crm_at",
                    "updated_at",
                ]
            )
            self._log_event(
                "crm_dispatch_success_dry_run" if response.dry_run else "crm_dispatch_success_real",
                lead_draft=lead_draft,
                external_id=external_id,
            )
            self._dispatch_webhook_lead_qualified(lead_draft)
            return CRMDispatchResult(
                attempted=True,
                success=True,
                dry_run=response.dry_run,
                lead_draft=lead_draft,
                external_id=external_id,
                message=response.message,
            )

        lead_draft.status = LeadDraft.Status.FAILED
        lead_draft.crm_error = response.message
        lead_draft.save(update_fields=["status", "crm_error", "updated_at"])
        self._log_event(
            "crm_dispatch_failure_dry_run" if response.dry_run else "crm_dispatch_failure_real",
            lead_draft=lead_draft,
        )
        return CRMDispatchResult(
            attempted=True,
            success=False,
            dry_run=response.dry_run,
            lead_draft=lead_draft,
            message=response.message,
        )

    def _dispatch_webhook_lead_qualified(self, lead_draft: LeadDraft) -> None:
        try:
            from integrations.webhooks.service import WebhookDispatchService

            WebhookDispatchService().dispatch_lead_qualified(lead_draft)
        except Exception as exc:
            logger.info(
                "livia_webhook_lead_dispatch_ignored lead_draft_id=%s tenant_slug=%s error_type=%s",
                lead_draft.id,
                lead_draft.tenant.slug,
                type(exc).__name__,
            )

    def _log_event(self, event_type: str, *, lead_draft: LeadDraft, external_id: str = "") -> None:
        parts = [
            f"event={event_type}",
            f"lead_draft_id={lead_draft.id}",
            f"tenant_slug={lead_draft.tenant.slug}",
            f"status={lead_draft.status}",
        ]
        if external_id:
            parts.append(f"crm_external_id={external_id}")
        logger.info(" ".join(parts))
