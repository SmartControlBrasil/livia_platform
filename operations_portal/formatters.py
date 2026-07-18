from __future__ import annotations

from conversations.models import HandoffRequest
from leads.models import LeadDraft

LEAD_STATUS_LABELS = {
    LeadDraft.Status.DRAFT: "Rascunho",
    LeadDraft.Status.QUALIFIED: "Qualificado",
    LeadDraft.Status.SENT_TO_CRM: "Enviado ao CRM",
    LeadDraft.Status.FAILED: "Falha",
}

LEAD_STATUS_TONES = {
    LeadDraft.Status.DRAFT: "secondary",
    LeadDraft.Status.QUALIFIED: "warning",
    LeadDraft.Status.SENT_TO_CRM: "success",
    LeadDraft.Status.FAILED: "danger",
}

HANDOFF_STATUS_LABELS = {
    HandoffRequest.Status.PENDING: "Pendente",
    HandoffRequest.Status.SENT: "Notificado",
    HandoffRequest.Status.RESOLVED: "Resolvido",
    HandoffRequest.Status.CANCELLED: "Cancelado",
}

PRIORITY_LABELS = {
    HandoffRequest.Priority.LOW: "Baixa",
    HandoffRequest.Priority.NORMAL: "Normal",
    HandoffRequest.Priority.HIGH: "Alta",
    HandoffRequest.Priority.URGENT: "Urgente",
}

HANDOFF_REASON_LABELS = {
    HandoffRequest.Reason.EXPLICIT_REQUEST: "Pedido explícito",
    HandoffRequest.Reason.QUALIFIED_LEAD: "Lead qualificado",
    HandoffRequest.Reason.TECHNICAL_COMPLEXITY: "Complexidade técnica",
    HandoffRequest.Reason.SUPPORT_REQUEST: "Suporte",
    HandoffRequest.Reason.EMERGENCY_OR_URGENT: "Emergência ou urgência",
    HandoffRequest.Reason.MANUAL: "Manual",
}

HANDOFF_STATUS_TONES = {
    HandoffRequest.Status.PENDING: "warning",
    HandoffRequest.Status.SENT: "info",
    HandoffRequest.Status.RESOLVED: "success",
    HandoffRequest.Status.CANCELLED: "secondary",
}

HANDOFF_PRIORITY_TONES = {
    HandoffRequest.Priority.LOW: "secondary",
    HandoffRequest.Priority.NORMAL: "primary",
    HandoffRequest.Priority.HIGH: "warning",
    HandoffRequest.Priority.URGENT: "danger",
}


def lead_status_label(status):
    return LEAD_STATUS_LABELS.get(status, status or "-")


def lead_status_tone(status):
    return LEAD_STATUS_TONES.get(status, "secondary")


def mask_email(value):
    value = str(value or "").strip()
    if "@" not in value:
        return value
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def mask_phone(value):
    value = str(value or "").strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return value
    return f"***{digits[-4:]}"


def compact_external_id(value):
    value = str(value or "").strip()
    if len(value) <= 18:
        return value
    return f"{value[:8]}...{value[-6:]}"


def short_text(value, limit=90):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def contact_summary(lead):
    name = lead.name or "Sem nome"
    parts = []
    if lead.email:
        parts.append(mask_email(lead.email))
    if lead.phone:
        parts.append(mask_phone(lead.phone))
    return {"name": name, "details": " / ".join(parts)}


def lead_crm_state(lead):
    external_id = str(lead.crm_external_id or "")
    if external_id.startswith("dry-run-"):
        return {"label": "Dry-run", "tone": "warning"}
    if external_id or lead.sent_to_crm_at or lead.status == LeadDraft.Status.SENT_TO_CRM:
        return {"label": "Enviado", "tone": "success"}
    if lead.status == LeadDraft.Status.FAILED or lead.crm_error:
        return {"label": "Falha", "tone": "danger"}
    return {"label": "Não enviado", "tone": "secondary"}


def can_retry_crm_dispatch(lead):
    return bool(
        lead.status == LeadDraft.Status.FAILED
        and not lead.crm_external_id
        and not lead.sent_to_crm_at
    )


def handoff_status_label(status):
    return HANDOFF_STATUS_LABELS.get(status, status or "-")


def handoff_status_tone(status):
    return HANDOFF_STATUS_TONES.get(status, "secondary")


def handoff_priority_label(priority):
    return PRIORITY_LABELS.get(priority, priority or "-")


def handoff_priority_tone(priority):
    return HANDOFF_PRIORITY_TONES.get(priority, "secondary")


def handoff_reason_label(reason):
    return HANDOFF_REASON_LABELS.get(reason, str(reason or "-").replace("_", " ").capitalize())


def handoff_contact_summary(handoff, *, masked=True):
    name = handoff.visitor_name or handoff.visitor_company or "Sem identificação"
    email = mask_email(handoff.visitor_email) if masked else str(handoff.visitor_email or "").strip()
    phone = mask_phone(handoff.visitor_phone) if masked else str(handoff.visitor_phone or "").strip()
    parts = [part for part in (email, phone) if part]
    return {"name": name, "details": " / ".join(parts)}
