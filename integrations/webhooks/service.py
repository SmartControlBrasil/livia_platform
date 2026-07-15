from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from assistant_core.discovery import analyze_message
from integrations.models import TenantWebhookConfig, WebhookDeliveryLog

logger = logging.getLogger(__name__)

SUCCESS_STATUSES = {WebhookDeliveryLog.Status.DRY_RUN, WebhookDeliveryLog.Status.SENT}


def _iso(value):
    return value.isoformat() if value else None


def _short_text(value, limit=500):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


class WebhookDispatchService:
    def build_handoff_payload(self, handoff):
        return {
            "tenant_slug": handoff.tenant.slug,
            "event_type": TenantWebhookConfig.EventType.HANDOFF_CREATED,
            "handoff_id": handoff.id,
            "status": handoff.status,
            "reason": handoff.reason,
            "priority": handoff.priority,
            "visitor_name": handoff.visitor_name,
            "visitor_company": handoff.visitor_company,
            "visitor_phone": handoff.visitor_phone,
            "visitor_email": handoff.visitor_email,
            "summary": _short_text(handoff.summary, 1200),
            "source_page": handoff.source_page,
            "created_at": _iso(handoff.created_at),
        }

    def build_lead_payload(self, lead_draft):
        conversation = lead_draft.conversation
        service_area = analyze_message(lead_draft.need_summary).service_area
        return {
            "tenant_slug": lead_draft.tenant.slug,
            "event_type": TenantWebhookConfig.EventType.LEAD_QUALIFIED,
            "lead_id": lead_draft.id,
            "name": lead_draft.name,
            "company": lead_draft.company,
            "phone": lead_draft.phone,
            "email": lead_draft.email,
            "service_area": service_area,
            "status": lead_draft.status,
            "need_summary": _short_text(lead_draft.need_summary, 1200),
            "source_page": conversation.source_page if conversation else "",
            "created_at": _iso(lead_draft.created_at),
        }

    def dispatch_handoff_created(self, handoff):
        return self.dispatch_event(
            handoff.tenant,
            TenantWebhookConfig.EventType.HANDOFF_CREATED,
            self.build_handoff_payload(handoff),
            related_handoff=handoff,
        )

    def dispatch_lead_qualified(self, lead_draft):
        return self.dispatch_event(
            lead_draft.tenant,
            TenantWebhookConfig.EventType.LEAD_QUALIFIED,
            self.build_lead_payload(lead_draft),
            related_lead=lead_draft,
        )

    def dispatch_event(self, tenant, event_type, payload, related_handoff=None, related_lead=None):
        configs = list(
            TenantWebhookConfig.objects.filter(tenant=tenant, is_active=True).filter(
                event_type__in=[event_type, TenantWebhookConfig.EventType.ALL]
            )
        )
        if not configs:
            return []

        logs = []
        for config in configs:
            logs.append(
                self._dispatch_to_config(
                    config,
                    event_type,
                    payload,
                    related_handoff=related_handoff,
                    related_lead=related_lead,
                )
            )
        return logs

    def _dispatch_to_config(self, config, event_type, payload, related_handoff=None, related_lead=None):
        if self._already_delivered(config, event_type, related_handoff=related_handoff, related_lead=related_lead):
            return self._create_log(
                config,
                event_type,
                WebhookDeliveryLog.Status.SKIPPED,
                payload,
                error_message="Evento já entregue para esta configuração.",
                related_handoff=related_handoff,
                related_lead=related_lead,
            )

        if not getattr(settings, "LIVIA_WEBHOOKS_ENABLED", False):
            return self._create_log(
                config,
                event_type,
                WebhookDeliveryLog.Status.SKIPPED,
                payload,
                error_message="Webhooks desabilitados globalmente.",
                related_handoff=related_handoff,
                related_lead=related_lead,
            )

        if getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True) or config.dry_run:
            return self._create_log(
                config,
                event_type,
                WebhookDeliveryLog.Status.DRY_RUN,
                payload,
                status_code=202,
                related_handoff=related_handoff,
                related_lead=related_lead,
            )

        headers = {
            "Content-Type": "application/json",
            "X-Livia-Event": event_type,
            "X-Livia-Tenant": config.tenant.slug,
        }
        if config.secret_token:
            headers["Authorization"] = f"Bearer {config.secret_token}"
            headers["X-Livia-Signature"] = config.secret_token

        try:
            response = requests.post(
                config.target_url,
                json=payload,
                headers=headers,
                timeout=int(getattr(settings, "LIVIA_WEBHOOK_TIMEOUT_SECONDS", 6)),
            )
        except requests.RequestException as exc:
            logger.info(
                "livia_webhook_failed tenant_slug=%s event_type=%s config_id=%s error_type=%s",
                config.tenant.slug,
                event_type,
                config.id,
                type(exc).__name__,
            )
            return self._create_log(
                config,
                event_type,
                WebhookDeliveryLog.Status.FAILED,
                payload,
                error_message=_short_text(str(exc), 500),
                related_handoff=related_handoff,
                related_lead=related_lead,
            )

        if 200 <= response.status_code < 300:
            return self._create_log(
                config,
                event_type,
                WebhookDeliveryLog.Status.SENT,
                payload,
                status_code=response.status_code,
                related_handoff=related_handoff,
                related_lead=related_lead,
            )

        return self._create_log(
            config,
            event_type,
            WebhookDeliveryLog.Status.FAILED,
            payload,
            status_code=response.status_code,
            error_message=_short_text(getattr(response, "text", ""), 500),
            related_handoff=related_handoff,
            related_lead=related_lead,
        )

    def _already_delivered(self, config, event_type, related_handoff=None, related_lead=None):
        queryset = WebhookDeliveryLog.objects.filter(
            webhook_config=config,
            event_type=event_type,
            status__in=SUCCESS_STATUSES,
        )
        if related_handoff is not None:
            return queryset.filter(related_handoff=related_handoff).exists()
        if related_lead is not None:
            return queryset.filter(related_lead=related_lead).exists()
        return False

    def _create_log(
        self,
        config,
        event_type,
        status,
        payload,
        *,
        status_code=None,
        error_message="",
        related_handoff=None,
        related_lead=None,
    ):
        return WebhookDeliveryLog.objects.create(
            tenant=config.tenant,
            webhook_config=config,
            event_type=event_type,
            status=status,
            status_code=status_code,
            error_message=_short_text(error_message, 500),
            payload_preview=self._payload_preview(payload),
            related_handoff=related_handoff,
            related_lead=related_lead,
        )

    def _payload_preview(self, payload: dict[str, Any]):
        safe_payload = {}
        for key, value in payload.items():
            if "token" in key.lower() or "secret" in key.lower():
                continue
            if isinstance(value, str):
                safe_payload[key] = _short_text(value, 500)
            else:
                safe_payload[key] = value
        return safe_payload
