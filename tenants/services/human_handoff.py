from __future__ import annotations

from urllib.parse import urlencode

from tenants.models import (
    DEFAULT_HANDOFF_WHATSAPP_LABEL,
    HUMAN_HANDOFF_CHANNEL_DISABLED,
    HUMAN_HANDOFF_CHANNEL_WHATSAPP,
    normalize_whatsapp_number,
)

WHATSAPP_BASE_URL = "https://wa.me/"


def public_human_handoff_config(profile):
    if profile is None or not getattr(profile, "is_active", False):
        return _disabled_config()
    channel = getattr(profile, "human_handoff_channel", HUMAN_HANDOFF_CHANNEL_DISABLED)
    enabled = bool(getattr(profile, "human_handoff_enabled", False))
    return {
        "human_handoff_enabled": enabled,
        "human_handoff_channel": channel if channel == HUMAN_HANDOFF_CHANNEL_WHATSAPP else HUMAN_HANDOFF_CHANNEL_DISABLED,
        "handoff_whatsapp_label": _label(profile),
    }


def build_whatsapp_handoff_url(profile):
    if not is_whatsapp_handoff_available(profile):
        return ""
    number = normalize_whatsapp_number(getattr(profile, "handoff_whatsapp_number", ""))
    url = f"{WHATSAPP_BASE_URL}{number}"
    message = str(getattr(profile, "handoff_whatsapp_message", "") or "").strip()
    if message:
        query = urlencode({"text": message})
        url = f"{url}?{query}"
    return url


def build_human_handoff_payload(profile, handoff):
    url = build_whatsapp_handoff_url(profile)
    if not handoff or not url:
        return {"active": False}
    return {
        "active": True,
        "channel": HUMAN_HANDOFF_CHANNEL_WHATSAPP,
        "label": _label(profile),
        "url": url,
        "handoff_id": handoff.pk,
    }


def is_whatsapp_handoff_available(profile):
    return bool(getattr(profile, "has_valid_whatsapp_handoff", False))


def _label(profile):
    return str(getattr(profile, "handoff_whatsapp_label", "") or DEFAULT_HANDOFF_WHATSAPP_LABEL).strip() or DEFAULT_HANDOFF_WHATSAPP_LABEL


def _disabled_config():
    return {
        "human_handoff_enabled": False,
        "human_handoff_channel": HUMAN_HANDOFF_CHANNEL_DISABLED,
        "handoff_whatsapp_label": DEFAULT_HANDOFF_WHATSAPP_LABEL,
    }
