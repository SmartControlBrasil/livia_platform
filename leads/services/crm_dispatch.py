from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

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
        self.client = client or Smart360GrowthClient(dry_run=True)

    def dispatch_if_qualified(self, lead_draft: LeadDraft) -> CRMDispatchResult:
        if lead_draft.status == LeadDraft.Status.SENT_TO_CRM:
            self._log_event(
                "crm_dispatch_ignored_already_sent",
                lead_draft=lead_draft,
            )
            return CRMDispatchResult(
                attempted=False,
                success=True,
                dry_run=self.client.dry_run,
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
                dry_run=self.client.dry_run,
                lead_draft=lead_draft,
                message="Lead ainda não qualificado.",
            )

        payload = self.build_payload(lead_draft)
        self._log_event("crm_dispatch_attempt", lead_draft=lead_draft, external_id=self._build_mock_external_id(lead_draft))
        response = self.client.ingest_lead(payload)

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
                "crm_dispatch_success_dry_run",
                lead_draft=lead_draft,
                external_id=external_id,
            )
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
            "crm_dispatch_failure_dry_run",
            lead_draft=lead_draft,
        )
        return CRMDispatchResult(
            attempted=True,
            success=False,
            dry_run=response.dry_run,
            lead_draft=lead_draft,
            message=response.message,
        )

    def build_payload(self, lead_draft: LeadDraft) -> LeadIngestPayload:
        conversation = lead_draft.conversation
        return LeadIngestPayload(
            tenant_slug=lead_draft.tenant.slug,
            name=lead_draft.name,
            company=lead_draft.company,
            email=lead_draft.email,
            phone=lead_draft.phone,
            city=lead_draft.city,
            need_summary=lead_draft.need_summary,
            source_page=conversation.source_page if conversation else "",
            conversation_id=str(conversation.session_id if conversation else lead_draft.id),
        )

    def _build_mock_external_id(self, lead_draft: LeadDraft) -> str:
        conversation = lead_draft.conversation
        conversation_id = conversation.session_id if conversation else lead_draft.id
        return f"dry-run-{lead_draft.tenant.slug}-{conversation_id}"

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
