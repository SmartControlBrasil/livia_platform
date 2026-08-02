from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from knowledge_base.models import (
    TenantOperationalAlert,
    TenantOperationalAlertSilence,
    TenantOperationalMaintenanceWindow,
)

NON_SUPPRESSIBLE_RULE_IDS = frozenset(
    {
        "provider_forbidden",
        "integration_safety",
    }
)
NON_SUPPRESSIBLE_CATEGORIES = frozenset(
    {
        TenantOperationalAlert.Category.TENANT_ISOLATION,
        TenantOperationalAlert.Category.INTEGRATION_SAFETY,
    }
)

SILENCE_PRESETS_HOURS = {
    "1h": 1,
    "4h": 4,
    "24h": 24,
    "7d": 168,
}


def _int_setting(name: str, default: int) -> int:
    try:
        return max(0, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default


def critical_ack_sla_minutes() -> int:
    return _int_setting("LIVIA_ALERT_CRITICAL_ACK_SLA_MINUTES", 30)


def critical_resolution_sla_minutes() -> int:
    return _int_setting("LIVIA_ALERT_CRITICAL_RESOLUTION_SLA_MINUTES", 240)


def warning_ack_sla_minutes() -> int:
    return _int_setting("LIVIA_ALERT_WARNING_ACK_SLA_MINUTES", 240)


def warning_resolution_sla_minutes() -> int:
    return _int_setting("LIVIA_ALERT_WARNING_RESOLUTION_SLA_MINUTES", 4320)


def sla_due_soon_minutes() -> int:
    return _int_setting("LIVIA_ALERT_SLA_DUE_SOON_MINUTES", 60)


def silence_max_hours() -> int:
    return _int_setting("LIVIA_ALERT_SILENCE_MAX_HOURS", 168)


def sanitize_governance_text(value: str, *, max_length: int = 500) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) < 10:
        raise ValueError("Informe um motivo com pelo menos 10 caracteres.")
    return cleaned[:max_length]


def is_rule_non_suppressible(*, rule_id: str, category: str) -> bool:
    base_rule = str(rule_id or "").split(":", 1)[0]
    if base_rule in NON_SUPPRESSIBLE_RULE_IDS:
        return True
    return str(category or "") in NON_SUPPRESSIBLE_CATEGORIES


def compute_sla_deadlines(*, severity: str, detected_at) -> tuple[timezone.datetime | None, timezone.datetime | None]:
    if severity == TenantOperationalAlert.Severity.INFO:
        return None, None
    if severity == TenantOperationalAlert.Severity.CRITICAL:
        ack_minutes = critical_ack_sla_minutes()
        resolution_minutes = critical_resolution_sla_minutes()
    else:
        ack_minutes = warning_ack_sla_minutes()
        resolution_minutes = warning_resolution_sla_minutes()
    return (
        detected_at + timedelta(minutes=ack_minutes),
        detected_at + timedelta(minutes=resolution_minutes),
    )


def refresh_maintenance_window_status(window: TenantOperationalMaintenanceWindow, *, now=None) -> str:
    now = now or timezone.now()
    if window.status == TenantOperationalMaintenanceWindow.Status.CANCELLED:
        return window.status
    if now < window.starts_at:
        return TenantOperationalMaintenanceWindow.Status.SCHEDULED
    if now <= window.ends_at:
        return TenantOperationalMaintenanceWindow.Status.ACTIVE
    return TenantOperationalMaintenanceWindow.Status.ENDED


def get_active_maintenance_windows(*, tenant, now=None) -> list[TenantOperationalMaintenanceWindow]:
    now = now or timezone.now()
    windows = list(
        TenantOperationalMaintenanceWindow.objects.filter(
            tenant=tenant,
            status__in=[
                TenantOperationalMaintenanceWindow.Status.SCHEDULED,
                TenantOperationalMaintenanceWindow.Status.ACTIVE,
            ],
            ends_at__gte=now,
        ).order_by("starts_at", "id")
    )
    active: list[TenantOperationalMaintenanceWindow] = []
    for window in windows:
        computed = refresh_maintenance_window_status(window, now=now)
        if computed != window.status:
            window.status = computed
            window.save(update_fields=["status", "updated_at"])
        if computed == TenantOperationalMaintenanceWindow.Status.ACTIVE:
            active.append(window)
    return active


def maintenance_window_matches_alert(
    *,
    window: TenantOperationalMaintenanceWindow,
    alert: TenantOperationalAlert,
) -> bool:
    if window.scope == TenantOperationalMaintenanceWindow.Scope.ALL:
        return True
    if window.scope == TenantOperationalMaintenanceWindow.Scope.CATEGORIES:
        return alert.category in {str(item) for item in window.scope_categories or []}
    if window.scope == TenantOperationalMaintenanceWindow.Scope.RULES:
        base_rule = str(alert.rule_id).split(":", 1)[0]
        scoped = {str(item).split(":", 1)[0] for item in window.scope_rule_ids or []}
        return alert.rule_id in scoped or base_rule in scoped
    if window.scope == TenantOperationalMaintenanceWindow.Scope.RESOURCE:
        return (
            bool(window.scope_resource_reference)
            and window.scope_resource_reference == alert.source_reference
        )
    return False


def get_active_silence(*, alert: TenantOperationalAlert, now=None) -> TenantOperationalAlertSilence | None:
    now = now or timezone.now()
    return (
        TenantOperationalAlertSilence.objects.filter(
            alert=alert,
            tenant=alert.tenant,
            starts_at__lte=now,
            ends_at__gt=now,
            cancelled_at__isnull=True,
        )
        .order_by("-starts_at", "-id")
        .first()
    )


@dataclass(frozen=True)
class AlertGovernanceState:
    is_silenced: bool
    is_under_maintenance: bool
    suppress_operational_noise: bool
    maintenance_title: str
    silence_reason: str
    silence_ends_at: timezone.datetime | None
    sla_state: str
    ack_sla_breached: bool
    resolution_sla_breached: bool
    assignee_username: str


def build_alert_governance_state(
    *,
    alert: TenantOperationalAlert,
    maintenance_windows: list[TenantOperationalMaintenanceWindow] | None = None,
    active_silence: TenantOperationalAlertSilence | None = None,
    now=None,
) -> AlertGovernanceState:
    now = now or timezone.now()
    if maintenance_windows is None:
        maintenance_windows = get_active_maintenance_windows(tenant=alert.tenant, now=now)
    if active_silence is None:
        active_silence = get_active_silence(alert=alert, now=now)

    matching_windows = [window for window in maintenance_windows if maintenance_window_matches_alert(window=window, alert=alert)]
    is_under_maintenance = bool(matching_windows)
    non_suppressible = is_rule_non_suppressible(rule_id=alert.rule_id, category=alert.category)

    is_silenced = bool(active_silence) and not non_suppressible
    suppress_operational_noise = is_silenced or (is_under_maintenance and not non_suppressible)

    sla_state, ack_breached, resolution_breached = compute_sla_state(
        alert=alert,
        is_under_maintenance=is_under_maintenance and not non_suppressible,
        now=now,
    )

    assignee = ""
    if alert.assigned_to_id and alert.assigned_to and alert.assigned_to.is_active:
        assignee = alert.assigned_to.user.get_username()

    return AlertGovernanceState(
        is_silenced=is_silenced,
        is_under_maintenance=is_under_maintenance,
        suppress_operational_noise=suppress_operational_noise,
        maintenance_title=matching_windows[0].title if matching_windows else "",
        silence_reason=active_silence.reason if active_silence else "",
        silence_ends_at=active_silence.ends_at if active_silence else None,
        sla_state=sla_state,
        ack_sla_breached=ack_breached,
        resolution_sla_breached=resolution_breached,
        assignee_username=assignee,
    )


def compute_sla_state(
    *,
    alert: TenantOperationalAlert,
    is_under_maintenance: bool,
    now=None,
) -> tuple[str, bool, bool]:
    now = now or timezone.now()
    if alert.status == TenantOperationalAlert.Status.RESOLVED:
        return "not_applicable", False, False
    if alert.severity == TenantOperationalAlert.Severity.INFO:
        return "not_applicable", False, False
    if is_under_maintenance:
        return "paused", False, False

    ack_breached = (
        alert.status == TenantOperationalAlert.Status.OPEN
        and alert.ack_due_at is not None
        and now > alert.ack_due_at
    )
    resolution_breached = (
        alert.status != TenantOperationalAlert.Status.RESOLVED
        and alert.resolution_due_at is not None
        and now > alert.resolution_due_at
    )

    due_soon_delta = timedelta(minutes=sla_due_soon_minutes())
    if ack_breached or resolution_breached:
        return "breached", ack_breached, resolution_breached

    upcoming_ack = (
        alert.status == TenantOperationalAlert.Status.OPEN
        and alert.ack_due_at is not None
        and alert.ack_due_at - now <= due_soon_delta
    )
    upcoming_resolution = (
        alert.resolution_due_at is not None
        and alert.resolution_due_at - now <= due_soon_delta
    )
    if upcoming_ack or upcoming_resolution:
        return "due_soon", False, False
    return "on_track", False, False


def build_tenant_governance_summary(*, tenant, now=None) -> dict:
    now = now or timezone.now()
    open_qs = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    ).select_related("assigned_to__user")

    maintenance_windows = get_active_maintenance_windows(tenant=tenant, now=now)
    silenced = 0
    under_maintenance = 0
    unassigned = 0
    ack_sla_breached = 0
    resolution_sla_breached = 0

    for alert in open_qs:
        state = build_alert_governance_state(
            alert=alert,
            maintenance_windows=maintenance_windows,
            now=now,
        )
        if state.is_silenced:
            silenced += 1
        if state.is_under_maintenance:
            under_maintenance += 1
        if not alert.assigned_to_id:
            unassigned += 1
        if state.ack_sla_breached:
            ack_sla_breached += 1
        if state.resolution_sla_breached:
            resolution_sla_breached += 1

    active_maintenance_count = TenantOperationalMaintenanceWindow.objects.filter(
        tenant=tenant,
        status=TenantOperationalMaintenanceWindow.Status.ACTIVE,
    ).count()

    return {
        "silenced": silenced,
        "under_maintenance": under_maintenance,
        "unassigned": unassigned,
        "ack_sla_breached": ack_sla_breached,
        "resolution_sla_breached": resolution_sla_breached,
        "active_maintenance_windows": active_maintenance_count,
    }
