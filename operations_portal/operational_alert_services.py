from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone

from knowledge_base.models import (
    TenantOperationalAlert,
    TenantOperationalAlertSilence,
    TenantOperationalMaintenanceWindow,
)
from knowledge_base.rag.alert_governance import (
    build_alert_governance_state,
    get_active_maintenance_windows,
)
from knowledge_base.rag.operational_alert_runbooks import get_runbook
from knowledge_base.rag.operational_metrics import ALLOWED_PERIODS, parse_health_period, period_window
from operations_portal.knowledge_base_selectors import PAGE_SIZE
from tenants.models import TenantMembership

SLA_STATE_LABELS = {
    "on_track": "No prazo",
    "due_soon": "Vence em breve",
    "breached": "Vencido",
    "not_applicable": "N/A",
    "paused": "Pausado (manutenção)",
}


def _serialize_governance(*, alert: TenantOperationalAlert, maintenance_windows=None) -> dict:
    state = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows)
    return {
        "is_silenced": state.is_silenced,
        "is_under_maintenance": state.is_under_maintenance,
        "suppress_operational_noise": state.suppress_operational_noise,
        "maintenance_title": state.maintenance_title,
        "silence_reason": state.silence_reason,
        "silence_ends_at": state.silence_ends_at,
        "sla_state": state.sla_state,
        "sla_state_label": SLA_STATE_LABELS.get(state.sla_state, state.sla_state),
        "ack_sla_breached": state.ack_sla_breached,
        "resolution_sla_breached": state.resolution_sla_breached,
        "assignee_username": state.assignee_username,
        "assigned_to_id": alert.assigned_to_id,
        "assigned_at": alert.assigned_at,
        "assigned_by": alert.assigned_by.get_username() if alert.assigned_by else "",
        "ack_due_at": alert.ack_due_at,
        "resolution_due_at": alert.resolution_due_at,
    }


def serialize_operational_alert(
    alert: TenantOperationalAlert,
    *,
    maintenance_windows=None,
) -> dict:
    runbook = get_runbook(alert.rule_id)
    payload = {
        "id": alert.pk,
        "category": alert.category,
        "category_label": alert.get_category_display(),
        "severity": alert.severity,
        "severity_label": alert.get_severity_display(),
        "status": alert.status,
        "status_label": alert.get_status_display(),
        "rule_id": alert.rule_id,
        "title": alert.title,
        "summary": alert.summary,
        "detected_at": alert.detected_at,
        "last_seen_at": alert.last_seen_at,
        "occurrence_count": alert.occurrence_count,
        "source_reference": alert.source_reference,
        "metadata": alert.metadata,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by": alert.acknowledged_by.get_username() if alert.acknowledged_by else "",
        "resolved_at": alert.resolved_at,
        "resolved_by": alert.resolved_by.get_username() if alert.resolved_by else "",
        "resolution_note": alert.resolution_note,
        "resolution_source": alert.resolution_source,
        "runbook_action": runbook.recommended_action if runbook else "",
        "runbook_doc": runbook.documentation_reference if runbook else "",
    }
    payload.update(_serialize_governance(alert=alert, maintenance_windows=maintenance_windows))
    return payload


def _apply_governance_filters(
    *,
    alerts: list[TenantOperationalAlert],
    maintenance_windows,
    silenced: str | None,
    under_maintenance: str | None,
    sla_breached: str | None,
) -> list[TenantOperationalAlert]:
    filtered: list[TenantOperationalAlert] = []
    for alert in alerts:
        state = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows)
        if silenced == "yes" and not state.is_silenced:
            continue
        if silenced == "no" and state.is_silenced:
            continue
        if under_maintenance == "yes" and not state.is_under_maintenance:
            continue
        if under_maintenance == "no" and state.is_under_maintenance:
            continue
        if sla_breached == "yes" and not (state.ack_sla_breached or state.resolution_sla_breached):
            continue
        filtered.append(alert)
    return filtered


def get_operational_alert_list(
    *,
    tenant,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    period: str | None = None,
    assigned_to: str | None = None,
    unassigned: str | None = None,
    silenced: str | None = None,
    under_maintenance: str | None = None,
    sla_breached: str | None = None,
    page_number=1,
):
    queryset = TenantOperationalAlert.objects.filter(tenant=tenant).select_related(
        "acknowledged_by",
        "resolved_by",
        "assigned_to__user",
        "assigned_by",
    )
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {choice.value for choice in TenantOperationalAlert.Status}:
        queryset = queryset.filter(status=normalized_status)

    normalized_severity = str(severity or "").strip().lower()
    if normalized_severity in {choice.value for choice in TenantOperationalAlert.Severity}:
        queryset = queryset.filter(severity=normalized_severity)

    normalized_category = str(category or "").strip().lower()
    if normalized_category in {choice.value for choice in TenantOperationalAlert.Category}:
        queryset = queryset.filter(category=normalized_category)

    normalized_period = parse_health_period(period)
    if normalized_period in ALLOWED_PERIODS:
        _, since = period_window(period=normalized_period)
        queryset = queryset.filter(last_seen_at__gte=since)

    if str(unassigned or "").lower() in {"1", "true", "yes"}:
        queryset = queryset.filter(assigned_to__isnull=True)

    assigned_filter = str(assigned_to or "").strip()
    if assigned_filter.isdigit():
        queryset = queryset.filter(assigned_to_id=int(assigned_filter))

    governance_filters = any(
        str(value or "").strip()
        for value in (silenced, under_maintenance, sla_breached)
    )

    if governance_filters:
        maintenance_windows = get_active_maintenance_windows(tenant=tenant)
        alerts = list(queryset.order_by("-last_seen_at", "-id"))
        alerts = _apply_governance_filters(
            alerts=alerts,
            maintenance_windows=maintenance_windows,
            silenced=str(silenced or "").strip().lower() or None,
            under_maintenance=str(under_maintenance or "").strip().lower() or None,
            sla_breached=str(sla_breached or "").strip().lower() or None,
        )
        page = Paginator(alerts, PAGE_SIZE).get_page(page_number)
        page.object_list = [
            serialize_operational_alert(item, maintenance_windows=maintenance_windows)
            for item in page.object_list
        ]
        return page

    queryset = queryset.order_by("-last_seen_at", "-id")
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    maintenance_windows = get_active_maintenance_windows(tenant=tenant)
    page.object_list = [
        serialize_operational_alert(item, maintenance_windows=maintenance_windows)
        for item in page.object_list
    ]
    return page


def get_operational_alert_detail(*, tenant, alert_id: int) -> dict | None:
    alert = (
        TenantOperationalAlert.objects.filter(tenant=tenant, pk=alert_id)
        .select_related(
            "acknowledged_by",
            "resolved_by",
            "assigned_to__user",
            "assigned_by",
            "tenant",
        )
        .first()
    )
    if alert is None:
        return None
    maintenance_windows = get_active_maintenance_windows(tenant=tenant)
    payload = serialize_operational_alert(alert, maintenance_windows=maintenance_windows)
    links = {
        "health": True,
        "operations": alert.rule_id.startswith("rag_operation_") and bool(alert.source_reference),
        "config": alert.category
        in {
            TenantOperationalAlert.Category.CONFIGURATION,
            TenantOperationalAlert.Category.VECTOR_HEALTH,
        },
        "diagnostic": alert.category == TenantOperationalAlert.Category.RETRIEVAL,
    }
    if links["operations"] and alert.source_reference.isdigit():
        payload["operation_id"] = int(alert.source_reference)
    payload["links"] = links
    payload["tenant_memberships"] = [
        {
            "id": membership.pk,
            "username": membership.user.get_username(),
            "role": membership.role,
        }
        for membership in TenantMembership.objects.filter(tenant=tenant, is_active=True).select_related("user")
    ]
    return payload


def serialize_maintenance_window(window: TenantOperationalMaintenanceWindow) -> dict:
    return {
        "id": window.pk,
        "title": window.title,
        "description": window.description,
        "starts_at": window.starts_at,
        "ends_at": window.ends_at,
        "status": window.status,
        "status_label": window.get_status_display(),
        "scope": window.scope,
        "scope_label": window.get_scope_display(),
        "scope_categories": window.scope_categories,
        "scope_rule_ids": window.scope_rule_ids,
        "scope_resource_reference": window.scope_resource_reference,
        "created_by": window.created_by.get_username() if window.created_by else "",
        "cancelled_at": window.cancelled_at,
        "cancelled_by": window.cancelled_by.get_username() if window.cancelled_by else "",
        "cancellation_note": window.cancellation_note,
    }


def get_maintenance_window_list(
    *,
    tenant,
    status: str | None = None,
    category: str | None = None,
    period: str | None = None,
    page_number=1,
):
    queryset = TenantOperationalMaintenanceWindow.objects.filter(tenant=tenant).select_related(
        "created_by",
        "cancelled_by",
    )
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {choice.value for choice in TenantOperationalMaintenanceWindow.Status}:
        queryset = queryset.filter(status=normalized_status)

    normalized_category = str(category or "").strip().lower()
    if normalized_category:
        queryset = queryset.filter(
            Q(scope=TenantOperationalMaintenanceWindow.Scope.ALL)
            | Q(scope_categories__contains=[normalized_category])
        )

    normalized_period = parse_health_period(period)
    if normalized_period in ALLOWED_PERIODS:
        _, since = period_window(period=normalized_period)
        queryset = queryset.filter(starts_at__gte=since)

    queryset = queryset.order_by("-starts_at", "-id")
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    page.object_list = [serialize_maintenance_window(item) for item in page.object_list]
    return page


def get_active_silence_for_alert(*, tenant, alert_id: int):
    now = timezone.now()
    return (
        TenantOperationalAlertSilence.objects.filter(
            tenant=tenant,
            alert_id=alert_id,
            starts_at__lte=now,
            ends_at__gt=now,
            cancelled_at__isnull=True,
        )
        .select_related("created_by")
        .order_by("-starts_at", "-id")
        .first()
    )
