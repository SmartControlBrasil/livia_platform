from __future__ import annotations

from django.core.paginator import Paginator

from audit.models import AuditEvent
from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.alert_governance import build_alert_governance_state, get_active_maintenance_windows
from knowledge_base.rag.operational_work_queue import (
    ESCALATION_LEVEL_LABELS,
    PRIORITY_LABELS,
    build_personal_work_count,
    build_work_queue_summary,
    calculate_operational_priority,
    work_queue_sort_key,
)
from operations_portal.knowledge_base_selectors import PAGE_SIZE
from operations_portal.operational_alert_services import serialize_operational_alert
from tenants.models import TenantMembership

WORK_QUEUE_AUDIT_ACTIONS = frozenset(
    {
        "operational_alert.created",
        "operational_alert.acknowledged",
        "operational_alert.assigned",
        "operational_alert.unassigned",
        "operational_alert.claimed",
        "operational_alert.transferred",
        "operational_alert.silenced",
        "operational_alert.unsilenced",
        "operational_alert.escalated",
        "operational_alert.deescalated",
        "operational_alert.resolved",
        "operational_alert.reopened",
        "operational_alert.owner_invalidated",
    }
)


def _serialize_work_item(*, alert: TenantOperationalAlert, maintenance_windows=None) -> dict:
    payload = serialize_operational_alert(alert, maintenance_windows=maintenance_windows)
    governance = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows)
    priority = calculate_operational_priority(alert=alert, governance=governance)
    payload["priority"] = priority
    payload["priority_label"] = PRIORITY_LABELS.get(priority, "-") if priority else "-"
    payload["escalation_level"] = alert.escalation_level
    payload["escalation_level_label"] = ESCALATION_LEVEL_LABELS.get(alert.escalation_level, "-")
    payload["escalation_reason"] = alert.escalation_reason
    payload["escalated_at"] = alert.escalated_at
    payload["reopen_count"] = alert.reopen_count
    payload["last_reopened_at"] = alert.last_reopened_at
    payload["sort_key"] = work_queue_sort_key(alert=alert, priority=priority or 99, governance=governance)
    return payload


def _base_open_queryset(*, tenant):
    return TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    ).select_related(
        "assigned_to__user",
        "assigned_by",
        "acknowledged_by",
        "resolved_by",
    )


def _apply_work_queue_filters(
    *,
    alerts: list[TenantOperationalAlert],
    maintenance_windows,
    priority: str | None = None,
    assigned_to: str | None = None,
    unassigned: str | None = None,
    sla_breached: str | None = None,
    under_maintenance: str | None = None,
    silenced: str | None = None,
    reopened: str | None = None,
    escalated: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
) -> list[dict]:
    filtered: list[dict] = []
    for alert in alerts:
        if status and alert.status != status:
            continue
        if severity and alert.severity != severity:
            continue
        if category and alert.category != category:
            continue
        if str(unassigned or "").lower() in {"1", "true", "yes"} and alert.assigned_to_id:
            continue
        assigned_filter = str(assigned_to or "").strip()
        if assigned_filter.isdigit() and alert.assigned_to_id != int(assigned_filter):
            continue
        if str(reopened or "").lower() in {"1", "true", "yes"} and alert.reopen_count <= 0:
            continue
        if str(escalated or "").lower() in {"1", "true", "yes"} and alert.escalation_level <= 0:
            continue

        item = _serialize_work_item(alert=alert, maintenance_windows=maintenance_windows)
        governance = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows)

        if str(silenced or "").lower() == "yes" and not item["is_silenced"]:
            continue
        if str(silenced or "").lower() == "no" and item["is_silenced"]:
            continue
        if str(under_maintenance or "").lower() == "yes" and not item["is_under_maintenance"]:
            continue
        if str(under_maintenance or "").lower() == "no" and item["is_under_maintenance"]:
            continue
        if str(sla_breached or "").lower() in {"1", "true", "yes"} and not (
            governance.ack_sla_breached or governance.resolution_sla_breached
        ):
            continue

        priority_filter = str(priority or "").strip().upper()
        if priority_filter in {"P1", "P2", "P3", "P4"}:
            expected = int(priority_filter[1])
            if item["priority"] != expected:
                continue

        filtered.append(item)

    filtered.sort(key=lambda row: row["sort_key"])
    return filtered


def get_tenant_work_queue(
    *,
    tenant,
    page_number=1,
    **filters,
) -> tuple:
    maintenance_windows = get_active_maintenance_windows(tenant=tenant)
    alerts = list(_base_open_queryset(tenant=tenant))
    items = _apply_work_queue_filters(alerts=alerts, maintenance_windows=maintenance_windows, **filters)
    page = Paginator(items, PAGE_SIZE).get_page(page_number)
    summary = build_work_queue_summary(tenant=tenant)
    return page, summary


def get_personal_work_queue(
    *,
    tenant,
    membership: TenantMembership | None,
    page_number=1,
    include_unassigned: bool = True,
) -> tuple:
    if membership is None:
        return Paginator([], PAGE_SIZE).get_page(page_number), 0

    maintenance_windows = get_active_maintenance_windows(tenant=tenant)
    alerts = list(_base_open_queryset(tenant=tenant))
    items: list[dict] = []
    for alert in alerts:
        if alert.assigned_to_id == membership.pk:
            items.append(_serialize_work_item(alert=alert, maintenance_windows=maintenance_windows))
        elif include_unassigned and not alert.assigned_to_id:
            items.append(_serialize_work_item(alert=alert, maintenance_windows=maintenance_windows))
    items.sort(key=lambda row: row["sort_key"])
    page = Paginator(items, PAGE_SIZE).get_page(page_number)
    count = build_personal_work_count(tenant=tenant, membership=membership)
    return page, count


def get_alert_audit_timeline(*, tenant, alert_id: int) -> list[dict]:
    events = (
        AuditEvent.objects.filter(tenant=tenant, object_type="TenantOperationalAlert", object_id=str(alert_id))
        .filter(action__in=WORK_QUEUE_AUDIT_ACTIONS)
        .select_related("actor")
        .order_by("-created_at", "-id")[:30]
    )
    timeline = []
    for event in events:
        timeline.append(
            {
                "action": event.action,
                "created_at": event.created_at,
                "actor": event.actor.get_username() if event.actor else "sistema",
                "metadata": {
                    key: value
                    for key, value in (event.metadata or {}).items()
                    if key
                    in {
                        "previous_level",
                        "target_level",
                        "trigger",
                        "membership_id",
                        "previous_membership_id",
                        "new_membership_id",
                        "resolution_source",
                    }
                },
            }
        )
    return timeline


def enrich_alert_detail_with_work_queue(*, detail: dict, alert: TenantOperationalAlert) -> dict:
    maintenance_windows = get_active_maintenance_windows(tenant=alert.tenant)
    governance = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows)
    priority = calculate_operational_priority(alert=alert, governance=governance)
    detail["priority"] = priority
    detail["priority_label"] = PRIORITY_LABELS.get(priority, "-") if priority else "-"
    detail["escalation_level"] = alert.escalation_level
    detail["escalation_level_label"] = ESCALATION_LEVEL_LABELS.get(alert.escalation_level, "-")
    detail["escalation_reason"] = alert.escalation_reason
    detail["escalated_at"] = alert.escalated_at
    detail["escalation_trigger"] = alert.escalation_trigger
    detail["reopen_count"] = alert.reopen_count
    detail["last_reopened_at"] = alert.last_reopened_at
    detail["audit_timeline"] = get_alert_audit_timeline(tenant=alert.tenant, alert_id=alert.pk)
    return detail
