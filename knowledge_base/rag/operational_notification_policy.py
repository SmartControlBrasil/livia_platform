from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from django.utils import timezone

from knowledge_base.models import TenantOperationalAlert, TenantOperationalNotificationPreference
from knowledge_base.rag.alert_governance import NON_SUPPRESSIBLE_CATEGORIES
from knowledge_base.rag.operational_notification_events import (
    EVENT_ALERT_ACKNOWLEDGED,
    EVENT_ALERT_ASSIGNED,
    EVENT_ALERT_CRITICAL_CREATED,
    EVENT_ALERT_ESCALATED,
    EVENT_ALERT_REOPENED,
    EVENT_ALERT_RESOLVED,
    EVENT_ALERT_TRANSFERRED,
    EVENT_DIGEST,
    EVENT_MAINTENANCE_CANCELLED,
    EVENT_MAINTENANCE_STARTED,
    EVENT_MONITORING_FAILED,
    EVENT_OWNER_INVALIDATED,
    EVENT_SLA_ACK_BREACHED,
    EVENT_SLA_RESOLUTION_BREACHED,
    OperationalNotificationEvent,
    SLA_EVENTS,
)
from knowledge_base.rag.operational_work_queue import PRIORITY_P1, PRIORITY_P2, calculate_operational_priority
from tenants.models import TenantMembership


MANDATORY_IN_APP_EVENTS = frozenset(
    {
        EVENT_ALERT_CRITICAL_CREATED,
        EVENT_SLA_ACK_BREACHED,
        EVENT_SLA_RESOLUTION_BREACHED,
        EVENT_ALERT_ESCALATED,
        EVENT_OWNER_INVALIDATED,
    }
)


@dataclass(frozen=True)
class NotificationDecision:
    notify: bool
    suppressed: bool = False
    suppression_reason: str = ""
    channels: tuple[str, ...] = ()
    severity: str = "info"
    category: str = "alert"
    immediate: bool = True
    mandatory_in_app: bool = False


def evaluate_notification_policy(
    *,
    event: OperationalNotificationEvent,
    alert: TenantOperationalAlert | None,
    preference: TenantOperationalNotificationPreference | None,
    under_maintenance: bool = False,
    silenced: bool = False,
) -> NotificationDecision:
    if event.event_type not in {
        EVENT_ALERT_CRITICAL_CREATED,
        EVENT_ALERT_ASSIGNED,
        EVENT_ALERT_TRANSFERRED,
        EVENT_ALERT_ACKNOWLEDGED,
        EVENT_ALERT_RESOLVED,
        EVENT_ALERT_REOPENED,
        EVENT_SLA_ACK_BREACHED,
        EVENT_SLA_RESOLUTION_BREACHED,
        EVENT_ALERT_ESCALATED,
        EVENT_OWNER_INVALIDATED,
        EVENT_MAINTENANCE_STARTED,
        EVENT_MAINTENANCE_CANCELLED,
        EVENT_MONITORING_FAILED,
        EVENT_DIGEST,
    }:
        return NotificationDecision(notify=False)

    mandatory = event.event_type in MANDATORY_IN_APP_EVENTS or (
        alert is not None
        and alert.category in NON_SUPPRESSIBLE_CATEGORIES
        and event.event_type in {EVENT_ALERT_CRITICAL_CREATED, EVENT_ALERT_ESCALATED}
    )

    if silenced and not mandatory and event.event_type not in SLA_EVENTS | {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED}:
        return NotificationDecision(notify=False, suppressed=True, suppression_reason="alert_silenced")

    if under_maintenance and not mandatory and event.event_type not in SLA_EVENTS | {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED}:
        if preference and preference.digest_frequency != TenantOperationalNotificationPreference.DigestFrequency.IMMEDIATE:
            return NotificationDecision(
                notify=True,
                channels=("in_app",),
                severity=_severity_for(event, alert),
                category=_category_for(event),
                immediate=False,
                mandatory_in_app=mandatory,
            )
        if event.event_type not in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED, EVENT_MONITORING_FAILED}:
            return NotificationDecision(notify=False, suppressed=True, suppression_reason="maintenance_window")

    pref = preference
    channels: list[str] = []
    if mandatory or pref is None or pref.in_app_enabled:
        channels.append("in_app")
    if pref and pref.email_enabled and _pref_allows_event(pref, event.event_type):
        channels.append("email")

    if not channels:
        if mandatory:
            channels = ["in_app"]
        else:
            return NotificationDecision(notify=False, suppressed=True, suppression_reason="preferences_disabled")

    immediate = True
    if pref and pref.digest_frequency != TenantOperationalNotificationPreference.DigestFrequency.IMMEDIATE:
        if event.event_type == EVENT_DIGEST or (not mandatory and event.event_type not in SLA_EVENTS):
            immediate = False

    return NotificationDecision(
        notify=True,
        channels=tuple(dict.fromkeys(channels)),
        severity=_severity_for(event, alert),
        category=_category_for(event),
        immediate=immediate,
        mandatory_in_app=mandatory,
    )


def resolve_recipients(
    *,
    event: OperationalNotificationEvent,
    alert: TenantOperationalAlert | None,
    tenant,
) -> list[TenantMembership]:
    memberships = TenantMembership.objects.filter(tenant=tenant, is_active=True).select_related("user")
    active_users = {m.pk: m for m in memberships if m.user.is_active}

    if event.event_type in {EVENT_ALERT_ASSIGNED, EVENT_ALERT_TRANSFERRED}:
        target_id = event.target_membership_id or (alert.assigned_to_id if alert else None)
        if target_id and target_id in active_users:
            return [active_users[target_id]]
        return []

    if event.event_type == EVENT_ALERT_ACKNOWLEDGED and alert and alert.assigned_to_id:
        m = active_users.get(alert.assigned_to_id)
        return [m] if m else []

    if event.event_type == EVENT_ALERT_RESOLVED:
        recipients: list[TenantMembership] = []
        if alert and alert.assigned_to_id and alert.assigned_to_id in active_users:
            recipients.append(active_users[alert.assigned_to_id])
        return recipients

    if event.event_type in {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED}:
        recipients = _management_memberships(active_users.values())
        if alert and alert.assigned_to_id and alert.assigned_to_id in active_users:
            assignee = active_users[alert.assigned_to_id]
            if assignee not in recipients:
                recipients.insert(0, assignee)
        return recipients

    if event.event_type in SLA_EVENTS | {EVENT_ALERT_CRITICAL_CREATED, EVENT_ALERT_REOPENED}:
        if alert and alert.assigned_to_id and alert.assigned_to_id in active_users:
            return [active_users[alert.assigned_to_id]]
        priority = calculate_operational_priority(alert=alert) if alert else PRIORITY_P1
        if priority in {PRIORITY_P1, PRIORITY_P2}:
            return _operator_and_management(active_users.values())
        return _management_memberships(active_users.values())

    if event.event_type in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED, EVENT_MONITORING_FAILED}:
        return _management_memberships(active_users.values())

    if event.event_type == EVENT_DIGEST:
        return list(active_users.values())

    return _management_memberships(active_users.values())


def build_deduplication_key(
    *,
    tenant_id: int,
    membership_id: int,
    event_type: str,
    alert_id: int | None = None,
    reopen_count: int = 0,
    escalation_level: int = 0,
    sla_type: str = "",
    channel: str = "in_app",
) -> str:
    parts = [f"t{tenant_id}", f"r{membership_id}", f"e{event_type}", f"c{channel}"]
    if alert_id is not None:
        parts.extend([f"alert{alert_id}", f"cycle{reopen_count}"])
    if escalation_level:
        parts.append(f"esc{escalation_level}")
    if sla_type:
        parts.append(f"sla{sla_type}")
    return ":".join(parts)[:220]


def is_quiet_hours(*, preference: TenantOperationalNotificationPreference | None, now=None) -> bool:
    if preference is None or not preference.quiet_hours_start or not preference.quiet_hours_end:
        return False
    now = now or timezone.now()
    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(preference.timezone or "America/Sao_Paulo")
    except Exception:
        tz = timezone.get_current_timezone()
    local_now = timezone.localtime(now, tz)
    current: time = local_now.time()
    start = preference.quiet_hours_start
    end = preference.quiet_hours_end
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _pref_allows_event(pref: TenantOperationalNotificationPreference, event_type: str) -> bool:
    if event_type in {EVENT_ALERT_ASSIGNED, EVENT_ALERT_TRANSFERRED}:
        return pref.notify_on_assignment
    if event_type in {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED}:
        return pref.notify_on_escalation
    if event_type in SLA_EVENTS:
        return pref.notify_on_sla_breach
    if event_type == EVENT_ALERT_RESOLVED:
        return pref.notify_on_resolution
    return True


def _management_memberships(memberships) -> list[TenantMembership]:
    return [
        m
        for m in memberships
        if m.role in {TenantMembership.Role.TENANT_ADMIN, TenantMembership.Role.MANAGER}
    ]


def _operator_and_management(memberships) -> list[TenantMembership]:
    return [
        m
        for m in memberships
        if m.role
        in {
            TenantMembership.Role.TENANT_ADMIN,
            TenantMembership.Role.MANAGER,
            TenantMembership.Role.OPERATOR,
        }
    ]


def _severity_for(event: OperationalNotificationEvent, alert: TenantOperationalAlert | None) -> str:
    if alert:
        return alert.severity
    if event.event_type in SLA_EVENTS | {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED, EVENT_MONITORING_FAILED}:
        return TenantOperationalAlert.Severity.CRITICAL
    if event.event_type in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED}:
        return TenantOperationalAlert.Severity.WARNING
    return TenantOperationalAlert.Severity.INFO


def _category_for(event: OperationalNotificationEvent) -> str:
    from knowledge_base.models import TenantOperationalNotification

    if event.event_type in SLA_EVENTS:
        return TenantOperationalNotification.Category.SLA
    if event.event_type in {EVENT_ALERT_ESCALATED, EVENT_OWNER_INVALIDATED}:
        return TenantOperationalNotification.Category.ESCALATION
    if event.event_type in {EVENT_ALERT_ASSIGNED, EVENT_ALERT_TRANSFERRED}:
        return TenantOperationalNotification.Category.OWNERSHIP
    if event.event_type in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED}:
        return TenantOperationalNotification.Category.MAINTENANCE
    if event.event_type == EVENT_MONITORING_FAILED:
        return TenantOperationalNotification.Category.MONITORING
    if event.event_type == EVENT_DIGEST:
        return TenantOperationalNotification.Category.DIGEST
    return TenantOperationalNotification.Category.ALERT
