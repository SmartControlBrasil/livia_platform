from __future__ import annotations

from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.operational_notification_events import (
    EVENT_ALERT_ACKNOWLEDGED,
    EVENT_ALERT_ASSIGNED,
    EVENT_ALERT_CRITICAL_CREATED,
    EVENT_ALERT_ESCALATED,
    EVENT_ALERT_REOPENED,
    EVENT_ALERT_RESOLVED,
    EVENT_ALERT_TRANSFERRED,
    EVENT_MAINTENANCE_CANCELLED,
    EVENT_MAINTENANCE_STARTED,
    EVENT_MONITORING_FAILED,
    EVENT_OWNER_INVALIDATED,
    EVENT_SLA_ACK_BREACHED,
    EVENT_SLA_RESOLUTION_BREACHED,
    OperationalNotificationEvent,
)
from knowledge_base.rag.operational_notification_services import schedule_operational_notification_event


def notify_alert_critical_created(*, alert: TenantOperationalAlert, actor=None, request=None) -> None:
    if alert.severity != TenantOperationalAlert.Severity.CRITICAL:
        return
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_reopened(*, alert: TenantOperationalAlert, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_REOPENED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_assigned(*, alert: TenantOperationalAlert, membership_id: int, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_ASSIGNED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            target_membership_id=membership_id,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_transferred(
    *,
    alert: TenantOperationalAlert,
    new_membership_id: int,
    previous_membership_id: int | None = None,
    actor=None,
    request=None,
) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_TRANSFERRED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            target_membership_id=new_membership_id,
            previous_membership_id=previous_membership_id,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_acknowledged(*, alert: TenantOperationalAlert, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_ACKNOWLEDGED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_resolved(*, alert: TenantOperationalAlert, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_RESOLVED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_alert_escalated(
    *,
    alert: TenantOperationalAlert,
    previous_level: int,
    actor=None,
    request=None,
) -> None:
    if int(alert.escalation_level or 0) <= int(previous_level or 0):
        return
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_ALERT_ESCALATED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            escalation_level=alert.escalation_level,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_owner_invalidated(*, alert: TenantOperationalAlert, previous_membership_id: int | None, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_OWNER_INVALIDATED,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            previous_membership_id=previous_membership_id,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_sla_breach(*, alert: TenantOperationalAlert, sla_type: str, actor=None, request=None) -> None:
    event_type = EVENT_SLA_ACK_BREACHED if sla_type == "ack" else EVENT_SLA_RESOLUTION_BREACHED
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=event_type,
            tenant_id=alert.tenant_id,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            sla_type=sla_type,
        ),
        tenant=alert.tenant,
        alert=alert,
        actor=actor,
        request=request,
    )


def notify_maintenance_started(*, tenant, maintenance_id: int, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_MAINTENANCE_STARTED,
            tenant_id=tenant.pk,
            maintenance_id=maintenance_id,
        ),
        tenant=tenant,
        actor=actor,
        request=request,
    )


def notify_maintenance_cancelled(*, tenant, maintenance_id: int, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_MAINTENANCE_CANCELLED,
            tenant_id=tenant.pk,
            maintenance_id=maintenance_id,
        ),
        tenant=tenant,
        actor=actor,
        request=request,
    )


def notify_monitoring_failed(*, tenant, monitoring_run_id: int | None = None, actor=None, request=None) -> None:
    schedule_operational_notification_event(
        event=OperationalNotificationEvent(
            event_type=EVENT_MONITORING_FAILED,
            tenant_id=tenant.pk,
            monitoring_run_id=monitoring_run_id,
        ),
        tenant=tenant,
        actor=actor,
        request=request,
    )


def evaluate_sla_breach_notifications(*, tenant, actor=None, request=None) -> None:
    from django.utils import timezone

    now = timezone.now()
    alerts = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    )
    for alert in alerts:
        if alert.ack_due_at and alert.ack_due_at <= now and alert.status == TenantOperationalAlert.Status.OPEN:
            notify_sla_breach(alert=alert, sla_type="ack", actor=actor, request=request)
        if alert.resolution_due_at and alert.resolution_due_at <= now:
            notify_sla_breach(alert=alert, sla_type="resolution", actor=actor, request=request)
