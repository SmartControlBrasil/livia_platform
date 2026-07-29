from __future__ import annotations

import json

from assistant_core.discovery import analyze_message

SCHEMA_VERSION = "2026-07-19"


def short_text(value, limit=500):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def ensure_json_serializable(payload: dict) -> dict:
    json.dumps(payload, ensure_ascii=False)
    return payload


def build_event_envelope(*, event_id, event_type: str, tenant, data: dict) -> dict:
    payload = {
        "event_id": str(event_id),
        "event_type": event_type,
        "occurred_at": None,
        "tenant": {"id": tenant.id, "slug": tenant.slug},
        "tenant_slug": tenant.slug,
        "schema_version": SCHEMA_VERSION,
        "data": data,
    }
    return ensure_json_serializable(payload)


def build_lead_qualified_data(lead_draft) -> dict:
    conversation = lead_draft.conversation
    return {
        "lead_id": lead_draft.id,
        "conversation_id": conversation.id if conversation else None,
        "conversation_session_id": conversation.session_id if conversation else "",
        "status": lead_draft.status,
        "service_area": analyze_message(lead_draft.need_summary).service_area,
        "source_page": conversation.source_page if conversation else "",
        "snapshot": {
            "name": short_text(lead_draft.name, 120),
            "company": short_text(lead_draft.company, 160),
            "email": short_text(lead_draft.email, 160),
            "phone": short_text(lead_draft.phone, 40),
            "city": short_text(lead_draft.city, 120),
            "need_summary": short_text(lead_draft.need_summary, 500),
        },
    }


def build_handoff_created_data(handoff) -> dict:
    return {
        "handoff_id": handoff.id,
        "conversation_id": handoff.conversation_id,
        "lead_draft_id": handoff.lead_draft_id,
        "status": handoff.status,
        "reason": handoff.reason,
        "priority": handoff.priority,
        "source_page": handoff.source_page,
        "snapshot": {
            "visitor_name": short_text(handoff.visitor_name, 120),
            "visitor_company": short_text(handoff.visitor_company, 160),
            "visitor_phone": short_text(handoff.visitor_phone, 40),
            "visitor_email": short_text(handoff.visitor_email, 160),
            "summary": short_text(handoff.summary, 500),
        },
    }
