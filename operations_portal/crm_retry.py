from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from integrations.models import OutboxEvent
from integrations.outbox.service import enqueue_lead_qualified
from leads.models import LeadDraft


@dataclass(frozen=True)
class CrmRetryOutcome:
    """Resultado transacional do reprocessamento CRM a partir do portal."""

    code: str
    lead: LeadDraft
    event: OutboxEvent | None = None
    created: bool = False


def execute_portal_crm_retry(*, lead: LeadDraft) -> CrmRetryOutcome:
    """
    Reenfileira lead failed para CRM com lock na entidade transacional.

    Ordem de lock:
    1. LeadDraft (escopo tenant + pk)
    2. OutboxEvent mais recente do aggregate (quando existir)

    Não usa select_related em FKs nullable (ex.: conversation) junto com
    select_for_update — PostgreSQL rejeita FOR UPDATE no lado nullable de OUTER JOIN.
    """
    with transaction.atomic():
        # LOCK da entidade principal (tenant obrigatório → INNER JOIN seguro se select_related).
        locked = (
            LeadDraft.objects.select_for_update()
            .select_related("tenant")
            .get(pk=lead.pk, tenant_id=lead.tenant_id)
        )

        event = (
            OutboxEvent.objects.select_for_update()
            .filter(
                event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
                aggregate_id=str(locked.pk),
                tenant_id=locked.tenant_id,
            )
            .order_by("-created_at")
            .first()
        )

        if event is not None and event.status in {OutboxEvent.Status.PROCESSING, OutboxEvent.Status.PENDING}:
            return CrmRetryOutcome(code="blocked_active", lead=locked, event=event)

        if event is not None and event.status == OutboxEvent.Status.RETRY:
            return CrmRetryOutcome(code="already_retry_scheduled", lead=locked, event=event)

        if event is not None and event.status == OutboxEvent.Status.SUCCEEDED:
            return CrmRetryOutcome(code="blocked_succeeded", lead=locked, event=event)

        locked.status = LeadDraft.Status.QUALIFIED
        locked.crm_error = ""
        locked.save(update_fields=["status", "crm_error", "updated_at"])

        if event is not None and event.status in {OutboxEvent.Status.DEAD_LETTER, OutboxEvent.Status.SKIPPED}:
            event.status = OutboxEvent.Status.PENDING
            event.available_at = timezone.now()
            event.locked_at = None
            event.locked_by = ""
            event.last_error_code = "manual_requeue_from_portal"
            event.last_error_message = "Lead reenfileirado manualmente pelo portal."
            event.save(
                update_fields=[
                    "status",
                    "available_at",
                    "locked_at",
                    "locked_by",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )
            locked.refresh_from_db()
            return CrmRetryOutcome(code="requeued", lead=locked, event=event, created=False)

        event, created = enqueue_lead_qualified(locked)
        locked.refresh_from_db()
        return CrmRetryOutcome(
            code="enqueued" if created else "reused_or_requeued",
            lead=locked,
            event=event,
            created=created,
        )
