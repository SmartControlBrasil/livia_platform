from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from tenants.services.human_handoff import public_human_handoff_config
from tenants.models import (
    DEFAULT_WIDGET_LAUNCHER_LABEL,
    DEFAULT_WIDGET_PLACEHOLDER_TEXT,
    DEFAULT_WIDGET_PRIMARY_COLOR,
    WIDGET_POSITION_BOTTOM_RIGHT,
    AssistantProfile,
    Tenant,
)

PUBLIC_WIDGET_VERSION = "1.0"
PUBLIC_API_CONTRACT_VERSION = "2026-08-23"


def build_widget_config_for_tenant_slug(tenant_slug):
    tenant = Tenant.objects.filter(slug=tenant_slug).first()
    if tenant is None or not tenant.is_active:
        return build_disabled_widget_config(tenant_slug)
    return build_widget_config_for_tenant(tenant)


def build_widget_config_for_tenant(tenant):
    try:
        profile = tenant.assistant_profile
    except ObjectDoesNotExist:
        return build_disabled_widget_config(tenant.slug)

    return build_widget_config_payload(tenant, profile)


def build_widget_config_payload(tenant, profile):
    widget_title = profile.effective_widget_title
    enabled = bool(tenant.is_active and profile.is_active and profile.is_widget_enabled)
    payload = {
        "tenant": tenant.slug,
        "widget_version": PUBLIC_WIDGET_VERSION,
        "api_contract_version": PUBLIC_API_CONTRACT_VERSION,
        "assistant_name": profile.name,
        "widget_title": widget_title,
        "launcher_label": profile.launcher_label or DEFAULT_WIDGET_LAUNCHER_LABEL,
        "initial_message": profile.initial_message,
        "primary_color": profile.primary_color or DEFAULT_WIDGET_PRIMARY_COLOR,
        "position": profile.position or WIDGET_POSITION_BOTTOM_RIGHT,
        "placeholder_text": profile.placeholder_text or DEFAULT_WIDGET_PLACEHOLDER_TEXT,
        "show_branding": bool(profile.show_branding),
        "is_widget_enabled": enabled,
    }
    payload.update(public_human_handoff_config(profile if enabled else None))
    return payload


def build_disabled_widget_config(tenant_slug):
    payload = {
        "tenant": tenant_slug or "",
        "widget_version": PUBLIC_WIDGET_VERSION,
        "api_contract_version": PUBLIC_API_CONTRACT_VERSION,
        "assistant_name": "Lívia",
        "widget_title": "Lívia",
        "launcher_label": DEFAULT_WIDGET_LAUNCHER_LABEL,
        "initial_message": "Olá! No momento este atendimento está indisponível.",
        "primary_color": DEFAULT_WIDGET_PRIMARY_COLOR,
        "position": WIDGET_POSITION_BOTTOM_RIGHT,
        "placeholder_text": DEFAULT_WIDGET_PLACEHOLDER_TEXT,
        "show_branding": True,
        "is_widget_enabled": False,
    }
    payload.update(public_human_handoff_config(None))
    return payload
