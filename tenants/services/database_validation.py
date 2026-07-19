from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count

from conversations.models import Conversation, HandoffRequest, Message
from integrations.models import TenantWebhookConfig, WebhookDeliveryLog
from knowledge_base.models import KnowledgeDocument
from leads.models import LeadDraft
from operations_portal.analytics import get_dashboard_analytics
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.origins import normalize_origin


@dataclass(frozen=True)
class ValidationReport:
    totals: dict
    by_tenant: list[dict]
    tenant_integrity: dict
    kpis: dict


def build_database_validation_report() -> ValidationReport:
    return ValidationReport(
        totals=_totals(),
        by_tenant=_by_tenant(),
        tenant_integrity=_tenant_integrity(),
        kpis=_kpi_summary(),
    )


def _totals() -> dict:
    user_model = get_user_model()
    return {
        "tenants": Tenant.objects.count(),
        "assistant_profiles": AssistantProfile.objects.count(),
        "conversations": Conversation.objects.count(),
        "messages": Message.objects.count(),
        "leads": LeadDraft.objects.count(),
        "handoffs": HandoffRequest.objects.count(),
        "knowledge_documents": KnowledgeDocument.objects.count(),
        "webhook_configs": TenantWebhookConfig.objects.count(),
        "webhook_delivery_logs": WebhookDeliveryLog.objects.count(),
        "users": user_model.objects.count(),
        "memberships": 0,
        "allowed_origins": TenantAllowedOrigin.objects.count(),
    }


def _by_tenant() -> list[dict]:
    tenants = Tenant.objects.order_by("slug").annotate(
        conversations_count=Count("conversations", distinct=True),
        leads_count=Count("lead_drafts", distinct=True),
        handoffs_count=Count("handoff_requests", distinct=True),
        knowledge_documents_count=Count("knowledge_documents", distinct=True),
        webhook_configs_count=Count("webhook_configs", distinct=True),
    )
    return [
        {
            "tenant": tenant.slug,
            "conversations": tenant.conversations_count,
            "leads": tenant.leads_count,
            "handoffs": tenant.handoffs_count,
            "knowledge_documents": tenant.knowledge_documents_count,
            "webhook_configs": tenant.webhook_configs_count,
        }
        for tenant in tenants
    ]


def _tenant_integrity() -> dict:
    lead_mismatches = _lead_mismatch_count()
    handoff_conversation_mismatches = _handoff_conversation_mismatch_count()
    handoff_lead_mismatches = _handoff_lead_mismatch_count()
    return {
        "lead_conversation_tenant_mismatches": lead_mismatches,
        "handoff_conversation_tenant_mismatches": handoff_conversation_mismatches,
        "handoff_lead_tenant_mismatches": handoff_lead_mismatches,
        "active_tenants_without_active_origin": Tenant.objects.filter(is_active=True).exclude(allowed_origins__is_active=True).distinct().count(),
        "widget_enabled_without_active_origin": AssistantProfile.objects.filter(is_widget_enabled=True, tenant__is_active=True).exclude(tenant__allowed_origins__is_active=True).distinct().count(),
        "invalid_origins": _invalid_origin_count(),
        "logical_duplicate_origins": _logical_duplicate_origin_count(),
        "production_originless_public_api": bool(not settings.DEBUG and getattr(settings, "LIVIA_ALLOW_ORIGINLESS_PUBLIC_API", False)),
        "production_global_origin_list_present": bool(not settings.DEBUG and getattr(settings, "LIVIA_ALLOWED_WIDGET_ORIGINS", [])),
    }


def _lead_mismatch_count() -> int:
    mismatches = 0
    rows = LeadDraft.objects.filter(conversation__isnull=False).select_related("conversation").only("tenant_id", "conversation__tenant_id")
    for lead in rows.iterator():
        if lead.tenant_id != lead.conversation.tenant_id:
            mismatches += 1
    return mismatches


def _handoff_conversation_mismatch_count() -> int:
    mismatches = 0
    rows = HandoffRequest.objects.select_related("conversation").only("tenant_id", "conversation__tenant_id")
    for handoff in rows.iterator():
        if handoff.tenant_id != handoff.conversation.tenant_id:
            mismatches += 1
    return mismatches


def _handoff_lead_mismatch_count() -> int:
    mismatches = 0
    rows = HandoffRequest.objects.filter(lead_draft__isnull=False).select_related("lead_draft").only("tenant_id", "lead_draft__tenant_id")
    for handoff in rows.iterator():
        if handoff.tenant_id != handoff.lead_draft.tenant_id:
            mismatches += 1
    return mismatches


def _kpi_summary() -> dict:
    summary = {}
    for period in (7, 30, 90):
        data = get_dashboard_analytics(period)
        summary[str(period)] = data["kpis"]
    return summary


def _invalid_origin_count() -> int:
    invalid = 0
    for item in TenantAllowedOrigin.objects.only("origin").iterator():
        try:
            normalize_origin(item.origin)
        except Exception:
            invalid += 1
    return invalid


def _logical_duplicate_origin_count() -> int:
    seen = set()
    duplicates = 0
    rows = TenantAllowedOrigin.objects.values_list("tenant_id", "origin")
    for tenant_id, origin in rows:
        try:
            normalized = normalize_origin(origin)
        except Exception:
            continue
        key = (tenant_id, normalized)
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates
