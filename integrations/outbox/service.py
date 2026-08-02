from __future__ import annotations

import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from conversations.models import HandoffRequest
from integrations.models import OutboxEvent
from leads.models import LeadDraft

from .payloads import build_event_envelope, build_handoff_created_data, build_lead_qualified_data


def enqueue_outbox_event(*, tenant, event_type: str, aggregate_type: str, aggregate_id, data: dict, deduplication_key: str | None = None, max_attempts: int | None = None) -> tuple[OutboxEvent, bool]:
    event_id = uuid.uuid4()
    dedupe = deduplication_key or f"{tenant.id}:{event_type}:{aggregate_type}:{aggregate_id}:v1"
    payload = build_event_envelope(event_id=event_id, event_type=event_type, tenant=tenant, data=data)
    payload["occurred_at"] = timezone.now().isoformat()
    try:
        with transaction.atomic():
            return OutboxEvent.objects.create(
                event_id=event_id,
                tenant=tenant,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                deduplication_key=dedupe,
                payload=payload,
                max_attempts=max_attempts or int(getattr(settings, "LIVIA_OUTBOX_MAX_ATTEMPTS", 3)),
                available_at=timezone.now(),
            ), True
    except IntegrityError:
        return OutboxEvent.objects.get(deduplication_key=dedupe), False


def enqueue_lead_qualified(lead_draft: LeadDraft) -> tuple[OutboxEvent, bool]:
    if lead_draft.tenant_id != getattr(lead_draft.conversation, "tenant_id", lead_draft.tenant_id):
        raise ValueError("LeadDraft tenant does not match its conversation tenant.")
    return enqueue_outbox_event(
        tenant=lead_draft.tenant,
        event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
        aggregate_type="LeadDraft",
        aggregate_id=lead_draft.pk,
        data=build_lead_qualified_data(lead_draft),
    )


def enqueue_handoff_created(handoff: HandoffRequest) -> tuple[OutboxEvent, bool]:
    if handoff.tenant_id != handoff.conversation.tenant_id:
        raise ValueError("Handoff tenant does not match its conversation tenant.")
    if handoff.lead_draft_id and handoff.lead_draft.tenant_id != handoff.tenant_id:
        raise ValueError("Handoff lead tenant does not match handoff tenant.")
    return enqueue_outbox_event(
        tenant=handoff.tenant,
        event_type=OutboxEvent.EventType.HANDOFF_CREATED,
        aggregate_type="HandoffRequest",
        aggregate_id=handoff.pk,
        data=build_handoff_created_data(handoff),
    )
