from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from assistant_core.qualification import extract_contact_snapshot, normalize_text
from assistant_core.summary import build_conversation_summary, format_conversation_summary_notes
from conversations.models import HandoffRequest
from leads.models import LeadDraft

from .handoff_notification import HandoffNotificationService

logger = logging.getLogger(__name__)


EXPLICIT_HANDOFF_PATTERNS = (
    "falar com alguem",
    "falar com alguém",
    "falar com humano",
    "quero atendimento humano",
    "atendimento humano",
    "falar com uma pessoa",
    "quero falar com uma pessoa",
    "falar com atendente",
    "atendente",
    "me passa para um especialista",
    "chama um vendedor",
    "chamar um vendedor",
    "vendedor",
    "consultor",
    "especialista",
    "me liga",
    "me ligue",
    "liga pra mim",
    "entrar em contato",
    "quero contato",
    "pessoa de verdade",
)
URGENT_PATTERNS = (
    "urgente",
    "emergencia",
    "emergência",
    "parou",
    "parada",
    "sem funcionar",
    "fora do ar",
    "risco",
    "queimado",
    "cheiro de queimado",
    "vazamento",
    "agora",
)
COMPLEX_TECHNICAL_PATTERNS = (
    "sem comunicacao",
    "sem comunicação",
    "falha intermitente",
    "linha parada",
    "maquina parada",
    "máquina parada",
    "inversor falhando",
    "clp parou",
)


@dataclass(frozen=True)
class HandoffDecision:
    should_create: bool
    reason: str = ""
    priority: str = HandoffRequest.Priority.NORMAL


@dataclass(frozen=True)
class HandoffServiceResult:
    handoff: HandoffRequest | None
    created: bool = False
    notification_result: object | None = None


class HandoffService:
    active_statuses = {HandoffRequest.Status.PENDING, HandoffRequest.Status.SENT}

    def __init__(self, notification_service: HandoffNotificationService | None = None):
        self.notification_service = notification_service or HandoffNotificationService()

    def should_create_handoff(self, conversation, lead_draft, discovery_result, message) -> HandoffDecision:
        normalized = normalize_text(message)
        service_area = getattr(discovery_result, "service_area", "unknown") if discovery_result is not None else "unknown"
        intent = getattr(discovery_result, "intent", "unknown") if discovery_result is not None else "unknown"

        if self._has_explicit_request(normalized):
            priority = HandoffRequest.Priority.HIGH if self._is_urgent(normalized) else HandoffRequest.Priority.NORMAL
            return HandoffDecision(True, HandoffRequest.Reason.EXPLICIT_REQUEST, priority)

        if lead_draft is not None and lead_draft.status in {LeadDraft.Status.QUALIFIED, LeadDraft.Status.SENT_TO_CRM}:
            return HandoffDecision(True, HandoffRequest.Reason.QUALIFIED_LEAD, HandoffRequest.Priority.NORMAL)

        if service_area in {"maintenance", "automation"} and self._is_urgent(normalized):
            return HandoffDecision(True, HandoffRequest.Reason.EMERGENCY_OR_URGENT, HandoffRequest.Priority.HIGH)

        if service_area in {"maintenance", "automation"} and self._is_complex_technical(normalized):
            return HandoffDecision(True, HandoffRequest.Reason.TECHNICAL_COMPLEXITY, HandoffRequest.Priority.HIGH)

        if intent == "support_request" and self._is_urgent(normalized):
            return HandoffDecision(True, HandoffRequest.Reason.SUPPORT_REQUEST, HandoffRequest.Priority.HIGH)

        return HandoffDecision(False)

    @transaction.atomic
    def create_or_update_handoff(self, conversation, lead_draft=None, discovery_result=None, message="") -> HandoffServiceResult:
        if conversation is None:
            return HandoffServiceResult(handoff=None)

        decision = self.should_create_handoff(conversation, lead_draft, discovery_result, message)
        if not decision.should_create:
            return HandoffServiceResult(handoff=None)

        handoff = conversation.handoff_requests.filter(status__in=self.active_statuses).order_by("-created_at").first()
        created = handoff is None
        if created:
            handoff = HandoffRequest(tenant=conversation.tenant, conversation=conversation)

        snapshot = extract_contact_snapshot(message)
        if lead_draft is None:
            lead_draft = getattr(conversation, "lead_draft", None)

        handoff.lead_draft = lead_draft
        handoff.reason = decision.reason
        handoff.priority = self._max_priority(getattr(handoff, "priority", ""), decision.priority)
        handoff.visitor_name = self._first(
            getattr(lead_draft, "name", ""),
            snapshot.name,
            getattr(conversation, "visitor_name", ""),
        )
        handoff.visitor_company = self._first(getattr(lead_draft, "company", ""), snapshot.company)
        handoff.visitor_phone = self._first(getattr(lead_draft, "phone", ""), snapshot.phone, getattr(conversation, "visitor_phone", ""))
        handoff.visitor_email = self._first(getattr(lead_draft, "email", ""), snapshot.email, getattr(conversation, "visitor_email", ""))
        handoff.source_page = str(getattr(conversation, "source_page", "") or "")
        handoff.summary = self._build_summary(conversation, lead_draft, message=message)
        handoff.metadata = {
            **(handoff.metadata or {}),
            "last_message": str(message or "")[:500],
            "service_area": getattr(discovery_result, "service_area", "unknown") if discovery_result is not None else "unknown",
            "intent": getattr(discovery_result, "intent", "unknown") if discovery_result is not None else "unknown",
        }
        handoff.save()

        notification_result = self.notification_service.notify(handoff) if created else None
        if created:
            try:
                from integrations.webhooks.service import WebhookDispatchService

                WebhookDispatchService().dispatch_handoff_created(handoff)
            except Exception as exc:
                logger.info(
                    "livia_webhook_handoff_dispatch_ignored handoff_id=%s tenant_slug=%s error_type=%s",
                    handoff.id,
                    handoff.tenant.slug,
                    type(exc).__name__,
                )
        return HandoffServiceResult(handoff=handoff, created=created, notification_result=notification_result)

    def mark_sent(self, handoff):
        handoff.status = HandoffRequest.Status.SENT
        handoff.save(update_fields=["status", "updated_at"])
        return handoff

    def mark_resolved(self, handoff):
        handoff.status = HandoffRequest.Status.RESOLVED
        handoff.resolved_at = timezone.now()
        handoff.save(update_fields=["status", "resolved_at", "updated_at"])
        return handoff

    def _build_summary(self, conversation, lead_draft, message="") -> str:
        summary = format_conversation_summary_notes(build_conversation_summary(conversation, lead_draft=lead_draft))
        current_message = str(message or "").strip()
        if current_message and current_message not in summary:
            summary = f"{summary}\n- Última mensagem: {current_message[:500]}"
        return summary

    def _has_explicit_request(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in EXPLICIT_HANDOFF_PATTERNS)

    def _is_urgent(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in URGENT_PATTERNS)

    def _is_complex_technical(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in COMPLEX_TECHNICAL_PATTERNS)

    def _first(self, *values) -> str:
        for value in values:
            if str(value or "").strip():
                return str(value).strip()
        return ""

    def _max_priority(self, current: str, new: str) -> str:
        order = {
            HandoffRequest.Priority.LOW: 1,
            HandoffRequest.Priority.NORMAL: 2,
            HandoffRequest.Priority.HIGH: 3,
            HandoffRequest.Priority.URGENT: 4,
        }
        return new if order.get(new, 0) >= order.get(current, 0) else current
