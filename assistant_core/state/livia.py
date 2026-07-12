from __future__ import annotations

from dataclasses import dataclass


class LeadState:
    DISCOVERY = "discovery"
    OFFER_HANDOFF = "offer_handoff"
    COLLECT_NEED = "collect_need"
    COLLECT_NAME_COMPANY = "collect_name_company"
    COLLECT_CONTACT = "collect_contact"
    QUALIFIED = "qualified"
    CLOSED = "closed"


STATE_ORDER = (
    LeadState.DISCOVERY,
    LeadState.OFFER_HANDOFF,
    LeadState.COLLECT_NEED,
    LeadState.COLLECT_NAME_COMPANY,
    LeadState.COLLECT_CONTACT,
    LeadState.QUALIFIED,
    LeadState.CLOSED,
)

NEW_CYCLE_MARKERS = (
    "novo orçamento",
    "novo orcamento",
    "outra solicitação",
    "outra solicitacao",
    "novo pedido",
    "outro projeto",
    "nova demanda",
)


@dataclass(frozen=True)
class LeadStateSnapshot:
    state: str
    next_field: str = ""
    is_terminal: bool = False


def normalize_state(raw_state: str) -> str:
    if raw_state in STATE_ORDER:
        return raw_state
    return LeadState.DISCOVERY


def get_current_state(conversation) -> str:
    return normalize_state(getattr(conversation, "lead_state", LeadState.DISCOVERY))


def set_state(conversation, state: str) -> str:
    next_state = normalize_state(state)
    if getattr(conversation, "lead_state", None) == next_state:
        return next_state
    conversation.lead_state = next_state
    conversation.save(update_fields=["lead_state", "updated_at"])
    return next_state


def should_lock_lead(conversation) -> bool:
    return bool(getattr(conversation, "is_qualified", False)) or get_current_state(conversation) in {
        LeadState.QUALIFIED,
        LeadState.CLOSED,
    }


def can_start_new_cycle(conversation, message: str) -> bool:
    if not should_lock_lead(conversation):
        return True
    normalized = str(message or "").strip().lower()
    return any(marker in normalized for marker in NEW_CYCLE_MARKERS)


def next_state_after_message(conversation, lead_draft, intent: str = "", extracted_data=None) -> LeadStateSnapshot:
    if lead_draft is None:
        return LeadStateSnapshot(state=get_current_state(conversation))
    if getattr(lead_draft, "status", "") in {"sent_to_crm", "qualified"} or getattr(conversation, "is_qualified", False):
        return LeadStateSnapshot(state=LeadState.QUALIFIED, is_terminal=True)
    if not str(getattr(lead_draft, "need_summary", "") or "").strip():
        return LeadStateSnapshot(state=LeadState.COLLECT_NEED, next_field="need_summary")
    if not (
        str(getattr(lead_draft, "name", "") or "").strip()
        or str(getattr(lead_draft, "company", "") or "").strip()
    ):
        return LeadStateSnapshot(state=LeadState.COLLECT_NAME_COMPANY, next_field="name_or_company")
    if not (
        str(getattr(lead_draft, "phone", "") or "").strip()
        or str(getattr(lead_draft, "email", "") or "").strip()
    ):
        return LeadStateSnapshot(state=LeadState.COLLECT_CONTACT, next_field="phone_or_email")
    if intent in {"quote_request", "commercial_interest", "contact_data"}:
        return LeadStateSnapshot(state=LeadState.OFFER_HANDOFF)
    return LeadStateSnapshot(state=LeadState.DISCOVERY)
