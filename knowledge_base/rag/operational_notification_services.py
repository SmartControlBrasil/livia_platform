from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_NOTIFICATION_CANCELLED,
    ACTION_OPERATIONAL_NOTIFICATION_CREATED,
    ACTION_OPERATIONAL_NOTIFICATION_READ,
    ACTION_OPERATIONAL_NOTIFICATION_SUPPRESSED,
    ACTION_NOTIFICATION_PREFERENCES_UPDATED,
)
from audit.services import record_audit_event
from knowledge_base.models import (
    TenantOperationalAlert,
    TenantOperationalNotification,
    TenantOperationalNotificationPreference,
)
from knowledge_base.rag.alert_governance import build_alert_governance_state, get_active_maintenance_windows
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
from knowledge_base.rag.operational_notification_policy import (
    build_deduplication_key,
    evaluate_notification_policy,
    is_quiet_hours,
    resolve_recipients,
)
from tenants.models import TenantMembership

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    pass


@dataclass(frozen=True)
class EnqueueNotificationsResult:
    created: int
    suppressed: int
    skipped_duplicate: int


def get_or_create_preference(*, tenant, membership: TenantMembership) -> TenantOperationalNotificationPreference:
    pref, _ = TenantOperationalNotificationPreference.objects.get_or_create(
        tenant=tenant,
        membership=membership,
        defaults={
            "in_app_enabled": True,
            "email_enabled": False,
        },
    )
    return pref


def schedule_operational_notification_event(
    *,
    event: OperationalNotificationEvent,
    tenant,
    alert: TenantOperationalAlert | None = None,
    actor=None,
    request=None,
) -> None:
    """Agenda enqueue após commit da transação atual."""

    def _enqueue():
        enqueue_operational_notifications_for_event(
            event=event,
            tenant=tenant,
            alert=alert,
            actor=actor,
            request=request,
        )

    transaction.on_commit(_enqueue)


def enqueue_operational_notifications_for_event(
    *,
    event: OperationalNotificationEvent,
    tenant,
    alert: TenantOperationalAlert | None = None,
    actor=None,
    request=None,
) -> EnqueueNotificationsResult:
    if alert is None and event.alert_id:
        alert = TenantOperationalAlert.objects.filter(tenant=tenant, pk=event.alert_id).first()

    now = timezone.now()
    maintenance_windows = get_active_maintenance_windows(tenant=tenant, now=now)
    governance = build_alert_governance_state(alert=alert, maintenance_windows=maintenance_windows, now=now) if alert else None
    under_maintenance = bool(governance and governance.is_under_maintenance)
    silenced = bool(governance and governance.is_silenced)

    recipients = resolve_recipients(event=event, alert=alert, tenant=tenant)
    created = suppressed = skipped_duplicate = 0

    for membership in recipients:
        if membership.tenant_id != tenant.pk:
            continue
        preference = get_or_create_preference(tenant=tenant, membership=membership)
        decision = evaluate_notification_policy(
            event=event,
            alert=alert,
            preference=preference,
            under_maintenance=under_maintenance,
            silenced=silenced,
        )
        if not decision.notify:
            if decision.suppressed:
                suppressed += 1
                record_audit_event(
                    action=ACTION_OPERATIONAL_NOTIFICATION_SUPPRESSED,
                    actor=actor,
                    tenant=tenant,
                    object_type="TenantOperationalNotification",
                    object_id=str(event.event_type),
                    object_repr=event.event_type,
                    metadata={"reason": decision.suppression_reason, "membership_id": membership.pk},
                    request=request,
                )
            continue

        title, summary = build_notification_content(event=event, alert=alert, tenant=tenant)
        destination_route, destination_object_id = build_destination(event=event, alert=alert)
        scheduled_at = now
        if not decision.immediate:
            scheduled_at = _next_digest_slot(preference)

        for channel in decision.channels:
            if channel == "email" and is_quiet_hours(preference=preference, now=now) and not decision.mandatory_in_app:
                scheduled_at = _end_quiet_hours(preference, now)

            dedupe = build_deduplication_key(
                tenant_id=tenant.pk,
                membership_id=membership.pk,
                event_type=event.event_type,
                alert_id=alert.pk if alert else event.alert_id,
                reopen_count=event.reopen_count or (alert.reopen_count if alert else 0),
                escalation_level=event.escalation_level or (alert.escalation_level if alert else 0),
                sla_type=event.sla_type,
                channel=channel,
            )
            try:
                with transaction.atomic():
                    notification = TenantOperationalNotification.objects.create(
                        tenant=tenant,
                        recipient_membership=membership,
                        channel=channel,
                        category=decision.category,
                        severity=decision.severity,
                        event_type=event.event_type,
                        title=title,
                        summary=summary,
                        status=TenantOperationalNotification.Status.PENDING,
                        source_type=_source_type_for(event),
                        source_reference=_source_reference(event, alert),
                        destination_route=destination_route,
                        destination_object_id=destination_object_id,
                        deduplication_key=dedupe,
                        scheduled_at=scheduled_at,
                        metadata=_safe_metadata(event, alert),
                    )
                    created += 1
                    record_audit_event(
                        action=ACTION_OPERATIONAL_NOTIFICATION_CREATED,
                        actor=actor,
                        tenant=tenant,
                        object_type="TenantOperationalNotification",
                        object_id=str(notification.pk),
                        object_repr=notification.event_type,
                        metadata={
                            "channel": channel,
                            "membership_id": membership.pk,
                            "event_type": event.event_type,
                        },
                        request=request,
                    )
            except IntegrityError:
                skipped_duplicate += 1

    return EnqueueNotificationsResult(created=created, suppressed=suppressed, skipped_duplicate=skipped_duplicate)


def build_notification_content(*, event: OperationalNotificationEvent, alert: TenantOperationalAlert | None, tenant) -> tuple[str, str]:
    tenant_name = tenant.name
    if event.event_type == EVENT_ALERT_CRITICAL_CREATED and alert:
        return (
            f"Alerta crítico em {tenant_name}",
            f"{alert.title} requer análise imediata.",
        )
    if event.event_type == EVENT_ALERT_ASSIGNED and alert:
        return (f"Alerta atribuído — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_ALERT_TRANSFERRED and alert:
        return (f"Alerta transferido — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_ALERT_ACKNOWLEDGED and alert:
        return (f"Alerta reconhecido — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_ALERT_RESOLVED and alert:
        return (f"Alerta resolvido — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_ALERT_REOPENED and alert:
        return (f"Alerta reaberto — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_SLA_ACK_BREACHED and alert:
        return (f"SLA de ACK vencido — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_SLA_RESOLUTION_BREACHED and alert:
        return (f"SLA de resolução vencido — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_ALERT_ESCALATED and alert:
        return (
            f"Alerta escalonado (nível {alert.escalation_level}) — {tenant_name}",
            alert.title[:200],
        )
    if event.event_type == EVENT_OWNER_INVALIDATED and alert:
        return (f"Responsável invalidado — {tenant_name}", alert.title[:200])
    if event.event_type == EVENT_MAINTENANCE_STARTED:
        return (f"Manutenção iniciada — {tenant_name}", "Janela de manutenção operacional ativa.")
    if event.event_type == EVENT_MAINTENANCE_CANCELLED:
        return (f"Manutenção cancelada — {tenant_name}", "Janela de manutenção operacional encerrada.")
    if event.event_type == EVENT_MONITORING_FAILED:
        return (f"Monitoramento falhou — {tenant_name}", "Execução de monitoramento operacional com falha.")
    return (f"Notificação operacional — {tenant_name}", "Evento operacional relevante.")


def build_destination(*, event: OperationalNotificationEvent, alert: TenantOperationalAlert | None) -> tuple[str, str]:
    from knowledge_base.models import TenantOperationalNotification

    if alert:
        return TenantOperationalNotification.DestinationRoute.ALERT_DETAIL, str(alert.pk)
    if event.event_type in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED} and event.maintenance_id:
        return TenantOperationalNotification.DestinationRoute.MAINTENANCE, str(event.maintenance_id)
    if event.event_type == EVENT_MONITORING_FAILED:
        return TenantOperationalNotification.DestinationRoute.HEALTH, ""
    return TenantOperationalNotification.DestinationRoute.WORK_QUEUE, ""


def mark_notification_read(*, notification: TenantOperationalNotification, membership: TenantMembership, actor, request=None) -> TenantOperationalNotification:
    if notification.recipient_membership_id != membership.pk:
        raise NotificationError("Notificação pertence a outro usuário.")
    if notification.tenant_id != membership.tenant_id:
        raise NotificationError("Notificação de outro tenant.")
    if notification.read_at:
        return notification
    now = timezone.now()
    notification.read_at = now
    if notification.status in {
        TenantOperationalNotification.Status.SENT,
        TenantOperationalNotification.Status.DELIVERED,
    }:
        notification.status = TenantOperationalNotification.Status.READ
    notification.save(update_fields=["read_at", "status", "updated_at"])
    record_audit_event(
        action=ACTION_OPERATIONAL_NOTIFICATION_READ,
        actor=actor,
        tenant=notification.tenant,
        object_type="TenantOperationalNotification",
        object_id=str(notification.pk),
        object_repr=notification.event_type,
        metadata={"membership_id": membership.pk},
        request=request,
    )
    return notification


def mark_all_notifications_read(*, tenant, membership: TenantMembership, actor, request=None) -> int:
    now = timezone.now()
    qs = TenantOperationalNotification.objects.filter(
        tenant=tenant,
        recipient_membership=membership,
        channel=TenantOperationalNotification.Channel.IN_APP,
        read_at__isnull=True,
        status__in=[
            TenantOperationalNotification.Status.SENT,
            TenantOperationalNotification.Status.DELIVERED,
        ],
    )
    updated = qs.update(read_at=now, status=TenantOperationalNotification.Status.READ, updated_at=now)
    return updated


def update_notification_preferences(
    *,
    tenant,
    membership: TenantMembership,
    actor,
    request=None,
    **fields,
) -> TenantOperationalNotificationPreference:
    pref = get_or_create_preference(tenant=tenant, membership=membership)
    allowed = {
        "in_app_enabled",
        "email_enabled",
        "notify_on_assignment",
        "notify_on_escalation",
        "notify_on_sla_breach",
        "notify_on_resolution",
        "digest_frequency",
        "quiet_hours_start",
        "quiet_hours_end",
        "timezone",
    }
    update_fields = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        setattr(pref, key, value)
        update_fields.append(key)
    if update_fields:
        pref.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=ACTION_NOTIFICATION_PREFERENCES_UPDATED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalNotificationPreference",
            object_id=str(pref.pk),
            object_repr=str(membership.pk),
            metadata={k: str(fields[k]) for k in update_fields},
            request=request,
        )
    return pref


def cancel_pending_notifications_for_membership(*, membership: TenantMembership, reason: str) -> int:
    now = timezone.now()
    return TenantOperationalNotification.objects.filter(
        recipient_membership=membership,
        status=TenantOperationalNotification.Status.PENDING,
    ).update(
        status=TenantOperationalNotification.Status.CANCELLED,
        cancellation_reason=reason[:120],
        updated_at=now,
    )


def count_unread_notifications(*, tenant, membership: TenantMembership) -> int:
    return TenantOperationalNotification.objects.filter(
        tenant=tenant,
        recipient_membership=membership,
        channel=TenantOperationalNotification.Channel.IN_APP,
        read_at__isnull=True,
        status__in=[
            TenantOperationalNotification.Status.SENT,
            TenantOperationalNotification.Status.DELIVERED,
        ],
    ).count()


def _source_type_for(event: OperationalNotificationEvent) -> str:
    from knowledge_base.models import TenantOperationalNotification

    if event.event_type in {EVENT_MAINTENANCE_STARTED, EVENT_MAINTENANCE_CANCELLED}:
        return TenantOperationalNotification.SourceType.MAINTENANCE_WINDOW
    if event.event_type == EVENT_MONITORING_FAILED:
        return TenantOperationalNotification.SourceType.MONITORING_RUN
    return TenantOperationalNotification.SourceType.OPERATIONAL_ALERT


def _source_reference(event: OperationalNotificationEvent, alert: TenantOperationalAlert | None) -> str:
    if alert:
        return f"alert:{alert.pk}"
    if event.maintenance_id:
        return f"maintenance:{event.maintenance_id}"
    if event.monitoring_run_id:
        return f"monitoring:{event.monitoring_run_id}"
    return ""


def _safe_metadata(event: OperationalNotificationEvent, alert: TenantOperationalAlert | None) -> dict:
    data = {
        "event_type": event.event_type,
        "reopen_count": event.reopen_count or (alert.reopen_count if alert else 0),
        "escalation_level": event.escalation_level or (alert.escalation_level if alert else 0),
    }
    if event.sla_type:
        data["sla_type"] = event.sla_type
    return data


def _next_digest_slot(preference: TenantOperationalNotificationPreference):
    now = timezone.now()
    if preference.digest_frequency == TenantOperationalNotificationPreference.DigestFrequency.WEEKLY:
        return now + timezone.timedelta(days=7)
    return now + timezone.timedelta(days=1)


def _end_quiet_hours(preference: TenantOperationalNotificationPreference, now):
    return now + timezone.timedelta(hours=1)
