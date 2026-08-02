from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from audit.models import ACTION_OPERATIONAL_ALERT_ESCALATED, AuditEvent
from knowledge_base.models import (
    OperationalMonitoringBatchRun,
    TenantOperationalAlert,
    TenantOperationalMonitoringRun,
    TenantOperationalNotification,
)
from knowledge_base.rag.alert_governance import build_alert_governance_state, get_active_maintenance_windows
from knowledge_base.rag.operational_metrics import DEFAULT_PERIOD, parse_health_period
from knowledge_base.rag.operational_work_queue import (
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    PRIORITY_P4,
    PRIORITY_LABELS,
    calculate_operational_priority,
)
from tenants.models import TenantMembership

ANALYTICS_PERIODS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}

AGE_BUCKET_DEFINITIONS = (
    ("lt_1h", "Menos de 1 hora", timedelta(hours=1)),
    ("1_4h", "1–4 horas", timedelta(hours=4)),
    ("4_24h", "4–24 horas", timedelta(hours=24)),
    ("1_3d", "1–3 dias", timedelta(days=3)),
    ("3_7d", "3–7 dias", timedelta(days=7)),
    ("gt_7d", "Mais de 7 dias", None),
)

CAPACITY_NORMAL = "normal"
CAPACITY_ATTENTION = "attention"
CAPACITY_OVERLOAD = "overload"
CAPACITY_IDLE = "idle"

INSUFFICIENT_DATA = "Sem dados suficientes"


@dataclass(frozen=True)
class AnalyticsFilters:
    priority: str = ""
    severity: str = ""
    category: str = ""
    assigned_to: str = ""
    status: str = ""


def parse_analytics_period(raw: str | None) -> str:
    period = str(raw or DEFAULT_PERIOD).strip().lower()
    if period not in ANALYTICS_PERIODS:
        return DEFAULT_PERIOD
    return period


def analytics_period_window(*, period: str) -> tuple[str, timezone.datetime, timezone.datetime]:
    normalized = parse_analytics_period(period)
    end_at = timezone.now()
    start_at = end_at - ANALYTICS_PERIODS[normalized]
    return normalized, start_at, end_at


def min_sample_size() -> int:
    return max(1, int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_MIN_SAMPLE", 3) or 3))


def workload_weights() -> dict[int, int]:
    return {
        PRIORITY_P1: int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_WORKLOAD_P1", 4) or 4),
        PRIORITY_P2: int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_WORKLOAD_P2", 3) or 3),
        PRIORITY_P3: int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_WORKLOAD_P3", 2) or 2),
        PRIORITY_P4: int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_WORKLOAD_P4", 1) or 1),
    }


def capacity_thresholds() -> dict[str, int]:
    return {
        "attention_score": int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_CAPACITY_ATTENTION_SCORE", 8) or 8),
        "overload_score": int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_CAPACITY_OVERLOAD_SCORE", 16) or 16),
        "p1_overload": int(getattr(settings, "LIVIA_OPERATIONAL_ANALYTICS_CAPACITY_P1_OVERLOAD", 2) or 2),
    }


def compute_percentiles(values: list[float]) -> dict:
    if not values:
        return {
            "has_data": False,
            "count": 0,
            "median_minutes": None,
            "p75_minutes": None,
            "p90_minutes": None,
            "p95_minutes": None,
            "max_minutes": None,
            "population_note": INSUFFICIENT_DATA,
        }
    if len(values) < min_sample_size():
        return {
            "has_data": False,
            "count": len(values),
            "median_minutes": None,
            "p75_minutes": None,
            "p90_minutes": None,
            "p95_minutes": None,
            "max_minutes": None,
            "population_note": INSUFFICIENT_DATA,
        }
    sorted_values = sorted(values)

    def _pct(p: float) -> float:
        idx = max(0, min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * p))))
        return round(sorted_values[idx], 1)

    return {
        "has_data": True,
        "count": len(values),
        "median_minutes": round(statistics.median(sorted_values), 1),
        "p75_minutes": _pct(0.75),
        "p90_minutes": _pct(0.90),
        "p95_minutes": _pct(0.95),
        "max_minutes": round(max(sorted_values), 1),
        "population_note": f"{len(values)} alertas no período",
    }


def build_operational_analytics(*, tenant, period: str | None = None, filters: AnalyticsFilters | None = None) -> dict:
    filters = filters or AnalyticsFilters()
    normalized, start_at, end_at = analytics_period_window(period=period or DEFAULT_PERIOD)
    now = end_at
    maintenance_windows = get_active_maintenance_windows(tenant=tenant, now=now)

    open_alerts = list(
        _apply_alert_filters(
            TenantOperationalAlert.objects.filter(
                tenant=tenant,
                status__in=[
                    TenantOperationalAlert.Status.OPEN,
                    TenantOperationalAlert.Status.ACKNOWLEDGED,
                ],
            ).select_related("assigned_to__user"),
            filters,
        )
    )
    enriched_open = [_enrich_alert(alert, maintenance_windows, now) for alert in open_alerts]
    if filters.priority:
        mapping = {"P1": PRIORITY_P1, "P2": PRIORITY_P2, "P3": PRIORITY_P3, "P4": PRIORITY_P4}
        expected = mapping.get(filters.priority.upper())
        if expected:
            enriched_open = [item for item in enriched_open if item["priority"] == expected]

    volume = _build_volume_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters)
    backlog = _build_backlog_metrics(enriched_open)
    age_buckets = _build_age_buckets(enriched_open, now=now)
    ack_times = _build_ack_time_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters)
    resolution_times = _build_resolution_time_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters)
    ack_sla = _build_ack_sla_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters, now=now)
    resolution_sla = _build_resolution_sla_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters, now=now)
    recurrence = _build_recurrence_metrics(tenant=tenant, start_at=start_at, end_at=end_at, filters=filters)
    occurrences = _build_occurrence_metrics(tenant=tenant, filters=filters)
    escalations = _build_escalation_metrics(tenant=tenant, start_at=start_at, end_at=end_at, enriched_open=enriched_open)
    ownership = _build_ownership_metrics(enriched_open, include_detail=True)
    capacity = _build_capacity_metrics(enriched_open)
    unassigned = _build_unassigned_metrics(enriched_open)
    notifications = _build_notification_analytics(tenant=tenant, start_at=start_at, end_at=end_at)
    monitoring = _build_monitoring_analytics(tenant=tenant, start_at=start_at, end_at=end_at)
    trends = _build_daily_trends(tenant=tenant, start_at=start_at, end_at=end_at)
    backlog_trend = _build_backlog_trend(trends)
    recommendations = _build_recommendations(
        backlog=backlog,
        ack_sla=ack_sla,
        resolution_sla=resolution_sla,
        recurrence=recurrence,
        notifications=notifications,
        monitoring=monitoring,
        unassigned=unassigned,
        capacity=capacity,
    )

    period_labels = {
        "24h": "Últimas 24 horas",
        "7d": "Últimos 7 dias",
        "30d": "Últimos 30 dias",
        "90d": "Últimos 90 dias",
    }

    return {
        "period": normalized,
        "period_label": period_labels[normalized],
        "start_at": start_at,
        "end_at": end_at,
        "filters": {
            "priority": filters.priority,
            "severity": filters.severity,
            "category": filters.category,
            "assigned_to": filters.assigned_to,
            "status": filters.status,
        },
        "scorecards": _build_scorecards(
            backlog=backlog,
            ack_sla=ack_sla,
            resolution_sla=resolution_sla,
            ack_times=ack_times,
            resolution_times=resolution_times,
            recurrence=recurrence,
            notifications=notifications,
            enriched_open=enriched_open,
        ),
        "volume": volume,
        "backlog": backlog,
        "age_buckets": age_buckets,
        "ack_times": ack_times,
        "resolution_times": resolution_times,
        "ack_sla": ack_sla,
        "resolution_sla": resolution_sla,
        "recurrence": recurrence,
        "occurrences": occurrences,
        "escalations": escalations,
        "ownership": ownership,
        "capacity": capacity,
        "unassigned": unassigned,
        "notifications": notifications,
        "monitoring": monitoring,
        "trends": trends,
        "backlog_trend": backlog_trend,
        "recommendations": recommendations,
        "links": _build_drill_down_links(filters),
    }


def build_operational_health_summary(*, tenant) -> dict:
    payload = build_operational_analytics(tenant=tenant, period="7d")
    trend = payload["backlog_trend"]
    return {
        "p1_open": payload["backlog"]["by_priority"].get("P1", 0),
        "p2_open": payload["backlog"]["by_priority"].get("P2", 0),
        "unassigned": payload["backlog"]["unassigned"],
        "ack_sla_breached": payload["backlog"]["ack_sla_breached"],
        "resolution_sla_breached": payload["backlog"]["resolution_sla_breached"],
        "backlog_trend": trend.get("direction", "unknown"),
        "backlog_trend_label": trend.get("label", INSUFFICIENT_DATA),
        "analytics_url": reverse("operations_portal:operational_analytics"),
    }


def _cycle_start(alert: TenantOperationalAlert):
    if alert.reopen_count and alert.last_reopened_at:
        return alert.last_reopened_at
    return alert.detected_at


def _enrich_alert(alert, maintenance_windows, now):
    governance = build_alert_governance_state(
        alert=alert,
        maintenance_windows=maintenance_windows,
        now=now,
    )
    priority = calculate_operational_priority(alert=alert, governance=governance, now=now)
    return {
        "alert": alert,
        "governance": governance,
        "priority": priority,
        "priority_label": PRIORITY_LABELS.get(priority, "-") if priority else "-",
    }


def _apply_alert_filters(queryset, filters: AnalyticsFilters):
    if filters.severity:
        queryset = queryset.filter(severity=filters.severity)
    if filters.category:
        queryset = queryset.filter(category=filters.category)
    if filters.status:
        queryset = queryset.filter(status=filters.status)
    if filters.assigned_to == "none":
        queryset = queryset.filter(assigned_to__isnull=True)
    elif filters.assigned_to:
        queryset = queryset.filter(assigned_to_id=int(filters.assigned_to))
    return queryset


def _matches_priority_filter(item, priority_filter: str) -> bool:
    if not priority_filter:
        return True
    mapping = {"P1": PRIORITY_P1, "P2": PRIORITY_P2, "P3": PRIORITY_P3, "P4": PRIORITY_P4}
    expected = mapping.get(priority_filter.upper())
    return item["priority"] == expected if expected else True


def _build_volume_metrics(*, tenant, start_at, end_at, filters: AnalyticsFilters) -> dict:
    base = TenantOperationalAlert.objects.filter(tenant=tenant)
    created = base.filter(detected_at__gte=start_at, detected_at__lte=end_at).count()
    acknowledged = base.filter(acknowledged_at__gte=start_at, acknowledged_at__lte=end_at).count()
    resolved = base.filter(resolved_at__gte=start_at, resolved_at__lte=end_at).count()
    reopened = base.filter(last_reopened_at__gte=start_at, last_reopened_at__lte=end_at).count()
    auto_resolved = base.filter(
        resolved_at__gte=start_at,
        resolved_at__lte=end_at,
        resolution_source=TenantOperationalAlert.ResolutionSource.AUTO,
    ).count()
    manual_resolved = base.filter(
        resolved_at__gte=start_at,
        resolved_at__lte=end_at,
        resolution_source=TenantOperationalAlert.ResolutionSource.MANUAL,
    ).count()
    escalated = AuditEvent.objects.filter(
        tenant=tenant,
        action=ACTION_OPERATIONAL_ALERT_ESCALATED,
        created_at__gte=start_at,
        created_at__lte=end_at,
    ).count()
    return {
        "created": created,
        "acknowledged": acknowledged,
        "resolved": resolved,
        "reopened": reopened,
        "escalated_events": escalated,
        "auto_resolved": auto_resolved,
        "manual_resolved": manual_resolved,
        "note": "Eventos no período; backlog atual é calculado separadamente.",
    }


def _build_backlog_metrics(enriched_open: list) -> dict:
    by_priority = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_escalation: dict[str, int] = {}
    unassigned = ack_breached = resolution_breached = escalated = silenced = under_maintenance = 0

    for item in enriched_open:
        alert = item["alert"]
        priority = item["priority"]
        if priority == PRIORITY_P1:
            by_priority["P1"] += 1
        elif priority == PRIORITY_P2:
            by_priority["P2"] += 1
        elif priority == PRIORITY_P3:
            by_priority["P3"] += 1
        elif priority == PRIORITY_P4:
            by_priority["P4"] += 1
        by_severity[alert.severity] = by_severity.get(alert.severity, 0) + 1
        by_category[alert.category] = by_category.get(alert.category, 0) + 1
        by_status[alert.status] = by_status.get(alert.status, 0) + 1
        level = str(int(alert.escalation_level or 0))
        by_escalation[level] = by_escalation.get(level, 0) + 1
        if not alert.assigned_to_id:
            unassigned += 1
        if item["governance"].ack_sla_breached:
            ack_breached += 1
        if item["governance"].resolution_sla_breached:
            resolution_breached += 1
        if int(alert.escalation_level or 0) > 0:
            escalated += 1
        if item["governance"].is_silenced:
            silenced += 1
        if item["governance"].is_under_maintenance:
            under_maintenance += 1

    return {
        "total_open": len(enriched_open),
        "by_priority": by_priority,
        "by_severity": by_severity,
        "by_category": by_category,
        "by_status": by_status,
        "by_escalation_level": by_escalation,
        "unassigned": unassigned,
        "ack_sla_breached": ack_breached,
        "resolution_sla_breached": resolution_breached,
        "escalated": escalated,
        "silenced": silenced,
        "under_maintenance": under_maintenance,
    }


def _age_bucket_key(age: timedelta) -> str:
    hours = age.total_seconds() / 3600.0
    if hours < 1:
        return "lt_1h"
    if hours < 4:
        return "1_4h"
    if hours < 24:
        return "4_24h"
    days = hours / 24.0
    if days < 3:
        return "1_3d"
    if days < 7:
        return "3_7d"
    return "gt_7d"


def _build_age_buckets(enriched_open: list, *, now) -> dict:
    buckets = {key: 0 for key, _, _ in AGE_BUCKET_DEFINITIONS}
    for item in enriched_open:
        alert = item["alert"]
        buckets[_age_bucket_key(now - _cycle_start(alert))] += 1
    return {
        "buckets": [
            {"key": key, "label": label, "count": buckets[key]}
            for key, label, _ in AGE_BUCKET_DEFINITIONS
        ],
        "total": sum(buckets.values()),
    }


def _build_ack_time_metrics(*, tenant, start_at, end_at, filters: AnalyticsFilters) -> dict:
    qs = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        acknowledged_at__isnull=False,
        acknowledged_at__gte=start_at,
        acknowledged_at__lte=end_at,
    )
    qs = _apply_alert_filters(qs, filters)
    durations = []
    for alert in qs.only("detected_at", "last_reopened_at", "reopen_count", "acknowledged_at"):
        start = _cycle_start(alert)
        if start and alert.acknowledged_at:
            minutes = (alert.acknowledged_at - start).total_seconds() / 60.0
            if minutes >= 0:
                durations.append(minutes)
    result = compute_percentiles(durations)
    result["population_note"] = (
        f"Alertas reconhecidos entre {start_at.date()} e {end_at.date()}. "
        + result.get("population_note", "")
    )
    return result


def _build_resolution_time_metrics(*, tenant, start_at, end_at, filters: AnalyticsFilters) -> dict:
    qs = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        resolved_at__isnull=False,
        resolved_at__gte=start_at,
        resolved_at__lte=end_at,
    )
    qs = _apply_alert_filters(qs, filters)
    auto = manual = 0
    durations = []
    for alert in qs.only(
        "detected_at",
        "last_reopened_at",
        "reopen_count",
        "resolved_at",
        "resolution_source",
    ):
        start = _cycle_start(alert)
        if start and alert.resolved_at:
            minutes = (alert.resolved_at - start).total_seconds() / 60.0
            if minutes >= 0:
                durations.append(minutes)
        if alert.resolution_source == TenantOperationalAlert.ResolutionSource.AUTO:
            auto += 1
        elif alert.resolution_source == TenantOperationalAlert.ResolutionSource.MANUAL:
            manual += 1
    result = compute_percentiles(durations)
    result["auto_resolved"] = auto
    result["manual_resolved"] = manual
    result["population_note"] = (
        f"Alertas resolvidos entre {start_at.date()} e {end_at.date()}. "
        + result.get("population_note", "")
    )
    return result


def _build_ack_sla_metrics(*, tenant, start_at, end_at, filters, now) -> dict:
    qs = TenantOperationalAlert.objects.filter(tenant=tenant, ack_due_at__isnull=False)
    qs = _apply_alert_filters(qs, filters)
    on_time = late = open_breached = 0
    eligible = 0
    for alert in qs.only("status", "ack_due_at", "acknowledged_at", "detected_at"):
        in_period = (
            (alert.acknowledged_at and start_at <= alert.acknowledged_at <= end_at)
            or (alert.ack_due_at and start_at <= alert.ack_due_at <= end_at)
            or (alert.status == TenantOperationalAlert.Status.OPEN and alert.ack_due_at <= now and alert.detected_at <= end_at)
        )
        if not in_period:
            continue
        eligible += 1
        if alert.acknowledged_at:
            if alert.acknowledged_at <= alert.ack_due_at:
                on_time += 1
            else:
                late += 1
        elif alert.status == TenantOperationalAlert.Status.OPEN and now > alert.ack_due_at:
            open_breached += 1
    rate = round(on_time / eligible, 3) if eligible >= min_sample_size() else None
    return {
        "eligible": eligible,
        "on_time": on_time,
        "late": late,
        "open_breached": open_breached,
        "compliance_rate": rate,
        "compliance_percent": round(rate * 100, 1) if rate is not None else None,
        "population_note": (
            f"Denominador: alertas com ack_due_at e deadline/reconhecimento no escopo ({eligible})."
            if eligible >= min_sample_size()
            else INSUFFICIENT_DATA
        ),
    }


def _build_resolution_sla_metrics(*, tenant, start_at, end_at, filters, now) -> dict:
    qs = TenantOperationalAlert.objects.filter(tenant=tenant, resolution_due_at__isnull=False).exclude(
        severity=TenantOperationalAlert.Severity.INFO,
    )
    qs = _apply_alert_filters(qs, filters)
    on_time = late = open_breached = 0
    eligible = 0
    for alert in qs.only("status", "resolution_due_at", "resolved_at"):
        in_period = (
            (alert.resolved_at and start_at <= alert.resolved_at <= end_at)
            or (alert.resolution_due_at and start_at <= alert.resolution_due_at <= end_at)
            or (
                alert.status != TenantOperationalAlert.Status.RESOLVED
                and alert.resolution_due_at <= now
            )
        )
        if not in_period:
            continue
        eligible += 1
        if alert.resolved_at:
            if alert.resolved_at <= alert.resolution_due_at:
                on_time += 1
            else:
                late += 1
        elif alert.status != TenantOperationalAlert.Status.RESOLVED and now > alert.resolution_due_at:
            open_breached += 1
    rate = round(on_time / eligible, 3) if eligible >= min_sample_size() else None
    return {
        "eligible": eligible,
        "on_time": on_time,
        "late": late,
        "open_breached": open_breached,
        "compliance_rate": rate,
        "compliance_percent": round(rate * 100, 1) if rate is not None else None,
        "population_note": (
            f"Denominador: alertas com resolution_due_at elegíveis ({eligible})."
            if eligible >= min_sample_size()
            else INSUFFICIENT_DATA
        ),
    }


def _build_recurrence_metrics(*, tenant, start_at, end_at, filters) -> dict:
    qs = TenantOperationalAlert.objects.filter(tenant=tenant, reopen_count__gt=0)
    qs = _apply_alert_filters(qs, filters)
    with_reopen = qs.count()
    total_reopens = sum(qs.values_list("reopen_count", flat=True))
    avg_reopens = round(total_reopens / with_reopen, 2) if with_reopen else 0
    top_rules = list(
        qs.values("rule_id")
        .annotate(total=Count("id"), reopens=Count("id"))
        .order_by("-reopens")[:10]
    )
    top_categories = list(
        qs.values("category").annotate(total=Count("id")).order_by("-total")[:10]
    )
    return {
        "alerts_with_reopen": with_reopen,
        "total_reopen_count": total_reopens,
        "average_reopens": avg_reopens,
        "top_rules": top_rules[:5],
        "top_categories": top_categories[:5],
    }


def _build_occurrence_metrics(*, tenant, filters) -> dict:
    qs = _apply_alert_filters(
        TenantOperationalAlert.objects.filter(tenant=tenant).order_by("-occurrence_count"),
        filters,
    )
    top_occurrence = [
        {"rule_id": a.rule_id, "title": a.title[:120], "occurrence_count": a.occurrence_count, "reopen_count": a.reopen_count}
        for a in qs[:10]
    ]
    return {"top_by_occurrence": top_occurrence[:5], "note": "occurrence_count reflete polling; reopen_count reflete ciclos."}


def _build_escalation_metrics(*, tenant, start_at, end_at, enriched_open) -> dict:
    still_escalated = sum(1 for item in enriched_open if int(item["alert"].escalation_level or 0) > 0)
    audit_qs = AuditEvent.objects.filter(
        tenant=tenant,
        action=ACTION_OPERATIONAL_ALERT_ESCALATED,
        created_at__gte=start_at,
        created_at__lte=end_at,
    )
    by_trigger: dict[str, int] = {}
    auto = manual = 0
    for event in audit_qs.only("metadata"):
        trigger = str((event.metadata or {}).get("trigger") or "unknown")
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1
        if trigger == "manual":
            manual += 1
        else:
            auto += 1
    levels = [int(item["alert"].escalation_level or 0) for item in enriched_open]
    return {
        "events_in_period": audit_qs.count(),
        "still_escalated": still_escalated,
        "average_level_open": round(sum(levels) / len(levels), 2) if levels else 0,
        "max_level_open": max(levels) if levels else 0,
        "auto_escalations": auto,
        "manual_escalations": manual,
        "by_trigger": by_trigger,
    }


def _build_ownership_metrics(enriched_open: list, *, include_detail: bool) -> dict:
    weights = workload_weights()
    by_membership: dict[int, dict] = {}
    for item in enriched_open:
        alert = item["alert"]
        membership_id = alert.assigned_to_id or 0
        if membership_id not in by_membership:
            username = alert.assigned_to.user.get_username() if alert.assigned_to else "Sem responsável"
            by_membership[membership_id] = {
                "membership_id": membership_id or None,
                "username": username if membership_id else "Sem responsável",
                "total": 0,
                "p1": 0,
                "p2": 0,
                "sla_breached": 0,
                "workload_score": 0,
            }
        row = by_membership[membership_id]
        row["total"] += 1
        priority = item["priority"]
        if priority == PRIORITY_P1:
            row["p1"] += 1
        elif priority == PRIORITY_P2:
            row["p2"] += 1
        if item["governance"].ack_sla_breached or item["governance"].resolution_sla_breached:
            row["sla_breached"] += 1
        if priority:
            row["workload_score"] += weights.get(priority, 1)

    rows = sorted(by_membership.values(), key=lambda r: r["workload_score"], reverse=True)
    if not include_detail:
        rows = [row for row in rows if row["membership_id"]]
    return {"assignees": rows, "note": "Indicador de capacidade operacional, não avaliação individual."}


def _build_capacity_metrics(enriched_open: list) -> dict:
    thresholds = capacity_thresholds()
    ownership = _build_ownership_metrics(enriched_open, include_detail=False)
    assignees = [row for row in ownership["assignees"] if row["membership_id"]]
    states = []
    for row in assignees:
        state = CAPACITY_IDLE
        if row["total"] == 0:
            state = CAPACITY_IDLE
        elif row["p1"] >= thresholds["p1_overload"] or row["workload_score"] >= thresholds["overload_score"]:
            state = CAPACITY_OVERLOAD
        elif row["workload_score"] >= thresholds["attention_score"] or row["sla_breached"] > 0:
            state = CAPACITY_ATTENTION
        else:
            state = CAPACITY_NORMAL
        states.append({**row, "capacity_state": state})
    scores = [row["workload_score"] for row in assignees]
    distribution_note = INSUFFICIENT_DATA
    if len(scores) >= min_sample_size():
        distribution_note = f"Média {round(statistics.mean(scores), 1)} · Mediana {round(statistics.median(scores), 1)}"
    tenant_id = enriched_open[0]["alert"].tenant_id if enriched_open else None
    active_members = TenantMembership.objects.filter(tenant_id=tenant_id, is_active=True).count() if tenant_id else 0
    busy = len([s for s in states if s["total"] > 0])
    idle_count = max(active_members - busy, 0) if tenant_id else 0
    return {
        "assignees": states,
        "distribution_note": distribution_note,
        "idle_memberships_estimate": idle_count,
        "thresholds": thresholds,
        "weights": workload_weights(),
    }


def _build_unassigned_metrics(enriched_open: list) -> dict:
    unassigned = [item for item in enriched_open if not item["alert"].assigned_to_id]
    urgent = sorted(
        unassigned,
        key=lambda item: (
            0 if item["priority"] == PRIORITY_P1 else 1,
            -(item["alert"].detected_at.timestamp() if item["alert"].detected_at else 0),
        ),
    )[:10]
    return {
        "count": len(unassigned),
        "top_urgent": [
            {
                "alert_id": item["alert"].pk,
                "title": item["alert"].title[:120],
                "priority": item["priority_label"],
                "category": item["alert"].category,
            }
            for item in urgent[:5]
        ],
    }


def _build_notification_analytics(*, tenant, start_at, end_at) -> dict:
    base = TenantOperationalNotification.objects.filter(tenant=tenant, created_at__gte=start_at, created_at__lte=end_at)
    in_app = base.filter(channel=TenantOperationalNotification.Channel.IN_APP)
    email = base.filter(channel=TenantOperationalNotification.Channel.EMAIL)
    webhook = base.filter(channel=TenantOperationalNotification.Channel.WEBHOOK)
    delivered = in_app.filter(
        status__in=[
            TenantOperationalNotification.Status.DELIVERED,
            TenantOperationalNotification.Status.READ,
        ]
    )
    read = in_app.filter(read_at__isnull=False)
    unread_critical = in_app.filter(
        severity=TenantOperationalNotification.Severity.CRITICAL,
        read_at__isnull=True,
        status=TenantOperationalNotification.Status.DELIVERED,
    ).count()
    read_latencies = []
    for notification in read.only("sent_at", "read_at", "created_at"):
        sent = notification.sent_at or notification.created_at
        if sent and notification.read_at:
            read_latencies.append((notification.read_at - sent).total_seconds() / 60.0)
    latency = compute_percentiles(read_latencies)
    delivered_count = delivered.count()
    read_count = read.count()
    read_rate = round(read_count / delivered_count, 3) if delivered_count >= min_sample_size() else None
    return {
        "created": base.count(),
        "in_app_delivered": delivered_count,
        "in_app_read": read_count,
        "in_app_unread": in_app.filter(read_at__isnull=True, status=TenantOperationalNotification.Status.DELIVERED).count(),
        "critical_unread": unread_critical,
        "failed": base.filter(status=TenantOperationalNotification.Status.FAILED).count(),
        "read_rate": read_rate,
        "read_rate_percent": round(read_rate * 100, 1) if read_rate is not None else None,
        "read_latency": latency,
        "email_dry_run_created": email.count(),
        "webhook_dry_run_created": webhook.count(),
        "population_note": (
            f"read_rate = lidas / entregues in-app ({read_count}/{delivered_count})"
            if read_rate is not None
            else INSUFFICIENT_DATA
        ),
    }


def _build_monitoring_analytics(*, tenant, start_at, end_at) -> dict:
    tenant_runs = TenantOperationalMonitoringRun.objects.filter(
        tenant=tenant,
        started_at__gte=start_at,
        started_at__lte=end_at,
    )
    by_status = {
        row["status"]: row["total"]
        for row in tenant_runs.values("status").annotate(total=Count("id"))
    }
    durations = [run.duration_ms for run in tenant_runs.filter(duration_ms__gt=0).only("duration_ms")]
    median_duration = round(statistics.median(durations), 1) if durations else None
    return {
        "runs_total": tenant_runs.count(),
        "runs_succeeded": by_status.get(TenantOperationalMonitoringRun.Status.SUCCEEDED, 0),
        "runs_failed": by_status.get(TenantOperationalMonitoringRun.Status.FAILED, 0),
        "runs_skipped": by_status.get(TenantOperationalMonitoringRun.Status.SKIPPED, 0),
        "alerts_created": sum(tenant_runs.values_list("alerts_created", flat=True) or [0]),
        "alerts_resolved": sum(tenant_runs.values_list("alerts_resolved", flat=True) or [0]),
        "median_duration_ms": median_duration,
    }


def _build_daily_trends(*, tenant, start_at, end_at) -> dict:
    current_tz = timezone.get_current_timezone()
    created = (
        TenantOperationalAlert.objects.filter(tenant=tenant, detected_at__gte=start_at, detected_at__lte=end_at)
        .annotate(day=TruncDate("detected_at", tzinfo=current_tz))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    resolved = (
        TenantOperationalAlert.objects.filter(tenant=tenant, resolved_at__gte=start_at, resolved_at__lte=end_at)
        .annotate(day=TruncDate("resolved_at", tzinfo=current_tz))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    return {
        "created_by_day": list(created),
        "resolved_by_day": list(resolved),
        "has_data": bool(created or resolved),
    }


def _build_backlog_trend(trends: dict) -> dict:
    created = trends.get("created_by_day") or []
    resolved = trends.get("resolved_by_day") or []
    if len(created) + len(resolved) < min_sample_size():
        return {"direction": "unknown", "label": INSUFFICIENT_DATA, "net": None}
    total_created = sum(row["total"] for row in created)
    total_resolved = sum(row["total"] for row in resolved)
    net = total_created - total_resolved
    if net > 2:
        direction = "growing"
        label = "Backlog líquido crescente"
    elif net < -2:
        direction = "shrinking"
        label = "Backlog líquido reduzindo"
    else:
        direction = "stable"
        label = "Backlog líquido estável"
    return {"direction": direction, "label": label, "net": net, "created": total_created, "resolved": total_resolved}


def _build_recommendations(**sections) -> list[dict]:
    recs: list[dict] = []
    backlog = sections["backlog"]
    if backlog["by_priority"].get("P1", 0) >= 3:
        recs.append({"severity": "warning", "message": "Backlog P1 elevado", "action": "Revisar ownership e capacidade da equipe."})
    if backlog["unassigned"] >= 2:
        recs.append({"severity": "warning", "message": "Alertas sem responsável", "action": "Revisar escala operacional e fila de atribuição."})
    ack_sla = sections["ack_sla"]
    if ack_sla.get("compliance_rate") is not None and ack_sla["compliance_rate"] < 0.8:
        recs.append({"severity": "warning", "message": "ACK SLA abaixo do objetivo", "action": "Revisar cobertura da equipe nos horários de pico."})
    notifications = sections["notifications"]
    if notifications.get("critical_unread", 0) >= 3:
        recs.append({"severity": "info", "message": "Notificações críticas não lidas", "action": "Revisar uso da Central de Notificações."})
    monitoring = sections["monitoring"]
    if monitoring.get("runs_failed", 0) >= 2:
        recs.append({"severity": "warning", "message": "Monitoramento com falhas recorrentes", "action": "Revisar tenants com erro no worker."})
    recurrence = sections["recurrence"]
    if recurrence.get("top_rules"):
        top = recurrence["top_rules"][0]
        if top.get("total", 0) >= 3:
            recs.append(
                {
                    "severity": "info",
                    "message": f"Alta reabertura em {top.get('rule_id')}",
                    "action": "Investigar causa raiz da regra operacional.",
                }
            )
    capacity = sections["capacity"]
    overloaded = [a for a in capacity.get("assignees", []) if a.get("capacity_state") == CAPACITY_OVERLOAD]
    low_load = [a for a in capacity.get("assignees", []) if a.get("capacity_state") == CAPACITY_IDLE]
    if backlog["unassigned"] and low_load:
        recs.append(
            {
                "severity": "info",
                "message": "Pendências P1/P2 sem responsável com operadores ociosos",
                "action": "Considerar redistribuição manual via fila (sem automação nesta fase).",
            }
        )
    return recs


def _build_scorecards(**sections) -> list[dict]:
    backlog = sections["backlog"]
    ack_sla = sections["ack_sla"]
    resolution_sla = sections["resolution_sla"]
    ack_times = sections["ack_times"]
    resolution_times = sections["resolution_times"]
    recurrence = sections["recurrence"]
    notifications = sections["notifications"]
    enriched_open = sections["enriched_open"]

    def _sla_card(title, payload):
        value = payload.get("compliance_percent")
        return {
            "title": title,
            "value": f"{value}%" if value is not None else INSUFFICIENT_DATA,
            "status": "success" if value and value >= 80 else "warning" if value else "secondary",
        }

    escalated = sum(1 for item in enriched_open if int(item["alert"].escalation_level or 0) > 0)
    return [
        {"title": "Backlog aberto", "value": backlog["total_open"], "status": "warning" if backlog["total_open"] else "success"},
        {"title": "P1 / P2", "value": f"{backlog['by_priority'].get('P1', 0)} / {backlog['by_priority'].get('P2', 0)}", "status": "danger" if backlog["by_priority"].get("P1") else "warning"},
        {"title": "Sem responsável", "value": backlog["unassigned"], "status": "warning" if backlog["unassigned"] else "success"},
        _sla_card("ACK SLA", ack_sla),
        _sla_card("Resolution SLA", resolution_sla),
        {
            "title": "Mediana ACK",
            "value": f"{ack_times['median_minutes']} min" if ack_times.get("has_data") else INSUFFICIENT_DATA,
            "status": "info",
        },
        {
            "title": "Mediana resolução",
            "value": f"{resolution_times['median_minutes']} min" if resolution_times.get("has_data") else INSUFFICIENT_DATA,
            "status": "info",
        },
        {"title": "Reaberturas", "value": recurrence["total_reopen_count"], "status": "info"},
        {"title": "Escalonados", "value": escalated, "status": "warning" if escalated else "success"},
        {"title": "Críticas não lidas", "value": notifications["critical_unread"], "status": "warning" if notifications["critical_unread"] else "success"},
    ]


def _build_drill_down_links(filters: AnalyticsFilters) -> dict:
    return {
        "work_queue": reverse("operations_portal:operational_work_queue"),
        "my_work": reverse("operations_portal:operational_my_work"),
        "alerts": reverse("operations_portal:knowledge_base_alerts"),
        "notifications": reverse("operations_portal:operational_notifications"),
        "health": reverse("operations_portal:knowledge_base_health"),
    }


def export_analytics_csv_rows(*, tenant, period: str | None = None) -> list[list[str]]:
    payload = build_operational_analytics(tenant=tenant, period=period)
    rows = [
        ["metric", "value"],
        ["period", payload["period"]],
        ["backlog_open", str(payload["backlog"]["total_open"])],
        ["p1_open", str(payload["backlog"]["by_priority"].get("P1", 0))],
        ["p2_open", str(payload["backlog"]["by_priority"].get("P2", 0))],
        ["unassigned", str(payload["backlog"]["unassigned"])],
        ["ack_sla_compliance", str(payload["ack_sla"].get("compliance_percent") or INSUFFICIENT_DATA)],
        ["resolution_sla_compliance", str(payload["resolution_sla"].get("compliance_percent") or INSUFFICIENT_DATA)],
        ["created_in_period", str(payload["volume"]["created"])],
        ["resolved_in_period", str(payload["volume"]["resolved"])],
    ]
    return rows
