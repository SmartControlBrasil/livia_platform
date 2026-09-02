"""Operações comerciais humanas sobre leads e handoffs."""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from audit.models import (
    ACTION_HANDOFF_ASSIGNED,
    ACTION_HANDOFF_NOTE_ADDED,
    ACTION_LEAD_ASSIGNED,
    ACTION_LEAD_COMMERCIAL_STATUS_CHANGED,
    ACTION_LEAD_NOTE_ADDED,
)
from audit.services import record_audit_event
from conversations.models import HandoffRequest
from leads.models import CommercialNote, LeadDraft

LOST_REASONS = (
    ("sem_interesse", "Sem interesse"),
    ("preco", "Preço"),
    ("prazo", "Prazo"),
    ("sem_retorno", "Sem retorno"),
    ("outro", "Outro"),
)

COMMERCIAL_TRANSITIONS = {
    LeadDraft.CommercialStatus.NEW: {
        LeadDraft.CommercialStatus.CONTACT_PENDING,
        LeadDraft.CommercialStatus.IN_PROGRESS,
        LeadDraft.CommercialStatus.QUALIFIED,
        LeadDraft.CommercialStatus.LOST,
        LeadDraft.CommercialStatus.CLOSED,
    },
    LeadDraft.CommercialStatus.CONTACT_PENDING: {
        LeadDraft.CommercialStatus.IN_PROGRESS,
        LeadDraft.CommercialStatus.QUALIFIED,
        LeadDraft.CommercialStatus.LOST,
        LeadDraft.CommercialStatus.CLOSED,
    },
    LeadDraft.CommercialStatus.IN_PROGRESS: {
        LeadDraft.CommercialStatus.CONTACT_PENDING,
        LeadDraft.CommercialStatus.QUALIFIED,
        LeadDraft.CommercialStatus.WON,
        LeadDraft.CommercialStatus.LOST,
        LeadDraft.CommercialStatus.CLOSED,
    },
    LeadDraft.CommercialStatus.QUALIFIED: {
        LeadDraft.CommercialStatus.IN_PROGRESS,
        LeadDraft.CommercialStatus.WON,
        LeadDraft.CommercialStatus.LOST,
        LeadDraft.CommercialStatus.CLOSED,
    },
    LeadDraft.CommercialStatus.WON: {LeadDraft.CommercialStatus.CLOSED},
    LeadDraft.CommercialStatus.LOST: {LeadDraft.CommercialStatus.CLOSED, LeadDraft.CommercialStatus.IN_PROGRESS},
    LeadDraft.CommercialStatus.CLOSED: set(),
}


def normalize_phone_for_whatsapp(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if not digits:
        return ""
    if digits.startswith("55"):
        return digits
    if len(digits) in {10, 11}:
        return f"55{digits}"
    return digits


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_email(email: str) -> str:
    value = str(email or "").strip()
    if "@" not in value:
        return "***"
    local, _, domain = value.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _touch_human_action(obj) -> None:
    if getattr(obj, "first_human_action_at", None) is None:
        obj.first_human_action_at = timezone.now()


@transaction.atomic
def assign_lead(*, lead: LeadDraft, user, actor) -> LeadDraft:
    before = {"assigned_to_id": lead.assigned_to_id, "commercial_status": lead.commercial_status}
    lead.assigned_to = user
    lead.assigned_at = timezone.now()
    _touch_human_action(lead)
    if lead.commercial_status == LeadDraft.CommercialStatus.NEW:
        lead.commercial_status = LeadDraft.CommercialStatus.IN_PROGRESS
    lead.save(update_fields=["assigned_to", "assigned_at", "first_human_action_at", "commercial_status", "updated_at"])
    record_audit_event(
        action=ACTION_LEAD_ASSIGNED,
        actor=actor,
        tenant=lead.tenant,
        obj=lead,
        before_data=before,
        after_data={"assigned_to_id": lead.assigned_to_id, "commercial_status": lead.commercial_status},
        metadata={"assigned_to_id": getattr(user, "pk", None)},
    )
    return lead


@transaction.atomic
def assign_handoff(*, handoff: HandoffRequest, user, actor) -> HandoffRequest:
    before = {"assigned_to_id": handoff.assigned_to_id}
    handoff.assigned_to = user
    handoff.assigned_at = timezone.now()
    _touch_human_action(handoff)
    handoff.save(update_fields=["assigned_to", "assigned_at", "first_human_action_at", "updated_at"])
    if handoff.lead_draft_id:
        assign_lead(lead=handoff.lead_draft, user=user, actor=actor)
    record_audit_event(
        action=ACTION_HANDOFF_ASSIGNED,
        actor=actor,
        tenant=handoff.tenant,
        obj=handoff,
        before_data=before,
        after_data={"assigned_to_id": handoff.assigned_to_id},
        metadata={"assigned_to_id": getattr(user, "pk", None)},
    )
    return handoff


@transaction.atomic
def change_lead_commercial_status(
    *,
    lead: LeadDraft,
    new_status: str,
    actor,
    note: str = "",
    lost_reason: str = "",
) -> LeadDraft:
    current = lead.commercial_status
    allowed = COMMERCIAL_TRANSITIONS.get(current, set())
    if new_status not in allowed and new_status != current:
        raise ValueError(f"Transição inválida: {current} → {new_status}")
    before = {"commercial_status": current, "lost_reason": lead.lost_reason}
    lead.commercial_status = new_status
    _touch_human_action(lead)
    update_fields = ["commercial_status", "first_human_action_at", "updated_at"]
    if new_status == LeadDraft.CommercialStatus.LOST:
        lead.lost_reason = (lost_reason or note or "")[:120]
        update_fields.append("lost_reason")
    if new_status in {LeadDraft.CommercialStatus.WON, LeadDraft.CommercialStatus.LOST, LeadDraft.CommercialStatus.CLOSED}:
        lead.closed_at = timezone.now()
        update_fields.append("closed_at")
    lead.save(update_fields=update_fields)
    record_audit_event(
        action=ACTION_LEAD_COMMERCIAL_STATUS_CHANGED,
        actor=actor,
        tenant=lead.tenant,
        obj=lead,
        before_data=before,
        after_data={"commercial_status": lead.commercial_status, "lost_reason": lead.lost_reason},
        metadata={"note": (note or "")[:240], "lost_reason": lead.lost_reason},
    )
    if note.strip():
        add_commercial_note(lead=lead, author=actor, body=note.strip())
    return lead


@transaction.atomic
def add_commercial_note(*, lead: LeadDraft | None = None, handoff: HandoffRequest | None = None, author, body: str) -> CommercialNote:
    body = str(body or "").strip()
    if not body:
        raise ValueError("Nota vazia")
    if lead is None and handoff is None:
        raise ValueError("Lead ou handoff obrigatório")
    tenant = lead.tenant if lead is not None else handoff.tenant
    note = CommercialNote.objects.create(
        tenant=tenant,
        lead_draft=lead,
        handoff=handoff,
        author=author if getattr(author, "is_authenticated", False) else None,
        body=body[:4000],
    )
    if lead is not None:
        _touch_human_action(lead)
        lead.save(update_fields=["first_human_action_at", "updated_at"])
        action = ACTION_LEAD_NOTE_ADDED
        obj = lead
    else:
        _touch_human_action(handoff)
        handoff.save(update_fields=["first_human_action_at", "updated_at"])
        action = ACTION_HANDOFF_NOTE_ADDED
        obj = handoff
    record_audit_event(
        action=action,
        actor=author,
        tenant=tenant,
        obj=obj,
        metadata={"note_id": note.pk, "body_preview": body[:120]},
    )
    return note
