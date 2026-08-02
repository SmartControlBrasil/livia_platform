from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_ALERT_ASSIGNED,
    ACTION_OPERATIONAL_ALERT_SILENCED,
    ACTION_OPERATIONAL_ALERT_UNASSIGNED,
    ACTION_OPERATIONAL_ALERT_UNSILENCED,
    ACTION_OPERATIONAL_MAINTENANCE_CANCELLED,
    ACTION_OPERATIONAL_MAINTENANCE_CREATED,
)
from audit.services import record_audit_event
from knowledge_base.models import (
    TenantOperationalAlert,
    TenantOperationalAlertSilence,
    TenantOperationalMaintenanceWindow,
)
from knowledge_base.rag.alert_governance import (
    SILENCE_PRESETS_HOURS,
    is_rule_non_suppressible,
    sanitize_governance_text,
    silence_max_hours,
)
from tenants.models import TenantMembership


class GovernanceError(Exception):
    pass


def _sanitize_or_raise(value: str, *, max_length: int = 500) -> str:
    try:
        return sanitize_governance_text(value, max_length=max_length)
    except ValueError as exc:
        raise GovernanceError(str(exc)) from exc


def _get_membership(*, tenant, user):
    membership = (
        TenantMembership.objects.select_related("user", "tenant")
        .filter(tenant=tenant, user=user, is_active=True, tenant__is_active=True)
        .first()
    )
    if membership is None:
        raise GovernanceError("Membership ativa do tenant é obrigatória.")
    return membership


def assign_operational_alert(
    *,
    tenant,
    alert_id: int,
    actor,
    membership_id: int | None,
    request=None,
) -> TenantOperationalAlert:
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise GovernanceError("Alerta não encontrado.")
        if alert.status == TenantOperationalAlert.Status.RESOLVED:
            raise GovernanceError("Alerta resolvido não pode ser atribuído.")

        if membership_id is None:
            membership = _get_membership(tenant=tenant, user=actor)
        else:
            membership = TenantMembership.objects.filter(
                pk=membership_id,
                tenant=tenant,
                is_active=True,
            ).first()
            if membership is None:
                raise GovernanceError("Responsável inválido para este tenant.")

        now = timezone.now()
        alert.assigned_to = membership
        alert.assigned_by = actor
        alert.assigned_at = now
        alert.save(update_fields=["assigned_to", "assigned_by", "assigned_at", "updated_at"])
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_ASSIGNED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={"membership_id": membership.pk, "assignee": membership.user.get_username()},
            request=request,
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_assigned

        notify_alert_assigned(alert=alert, membership_id=membership.pk, actor=actor, request=request)
        return alert


def unassign_operational_alert(*, tenant, alert_id: int, actor, request=None) -> TenantOperationalAlert:
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise GovernanceError("Alerta não encontrado.")
        if alert.assigned_to_id is None:
            raise GovernanceError("Alerta não possui responsável.")
        previous = alert.assigned_to.user.get_username() if alert.assigned_to else ""
        alert.assigned_to = None
        alert.assigned_by = None
        alert.assigned_at = None
        alert.save(update_fields=["assigned_to", "assigned_by", "assigned_at", "updated_at"])
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_UNASSIGNED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={"previous_assignee": previous},
            request=request,
        )
        return alert


def silence_operational_alert(
    *,
    tenant,
    alert_id: int,
    actor,
    reason: str,
    duration_key: str | None = None,
    ends_at=None,
    request=None,
) -> TenantOperationalAlertSilence:
    cleaned_reason = _sanitize_or_raise(reason)
    now = timezone.now()
    if ends_at is None:
        hours = SILENCE_PRESETS_HOURS.get(str(duration_key or "").strip())
        if hours is None:
            raise GovernanceError("Duração de silenciamento inválida.")
        ends_at = now + timedelta(hours=hours)
    if ends_at <= now:
        raise GovernanceError("Fim do silenciamento deve ser posterior ao início.")
    max_end = now + timedelta(hours=silence_max_hours())
    if ends_at > max_end:
        raise GovernanceError(f"Silenciamento excede o máximo de {silence_max_hours()} horas.")

    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise GovernanceError("Alerta não encontrado.")
        if is_rule_non_suppressible(rule_id=alert.rule_id, category=alert.category):
            raise GovernanceError("Esta regra crítica não pode ser silenciada.")

        active = TenantOperationalAlertSilence.objects.filter(
            alert=alert,
            tenant=tenant,
            starts_at__lte=now,
            ends_at__gt=now,
            cancelled_at__isnull=True,
        ).exists()
        if active:
            raise GovernanceError("Alerta já possui silenciamento ativo.")

        silence = TenantOperationalAlertSilence.objects.create(
            tenant=tenant,
            alert=alert,
            reason=cleaned_reason,
            starts_at=now,
            ends_at=ends_at,
            created_by=actor,
        )
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_SILENCED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlertSilence",
            object_id=str(silence.pk),
            object_repr=alert.rule_id,
            metadata={"alert_id": alert.pk, "ends_at": ends_at.isoformat()},
            request=request,
        )
        return silence


def cancel_operational_alert_silence(*, tenant, alert_id: int, actor, request=None) -> None:
    now = timezone.now()
    with transaction.atomic():
        silence = (
            TenantOperationalAlertSilence.objects.select_for_update()
            .filter(
                tenant=tenant,
                alert_id=alert_id,
                starts_at__lte=now,
                ends_at__gt=now,
                cancelled_at__isnull=True,
            )
            .order_by("-starts_at", "-id")
            .first()
        )
        if silence is None:
            raise GovernanceError("Nenhum silenciamento ativo encontrado.")
        silence.cancelled_at = now
        silence.cancelled_by = actor
        silence.save(update_fields=["cancelled_at", "cancelled_by"])
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_UNSILENCED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlertSilence",
            object_id=str(silence.pk),
            object_repr=str(alert_id),
            metadata={"alert_id": alert_id},
            request=request,
        )


def create_maintenance_window(
    *,
    tenant,
    actor,
    title: str,
    description: str,
    starts_at,
    ends_at,
    scope: str,
    scope_categories=None,
    scope_rule_ids=None,
    scope_resource_reference: str = "",
    request=None,
) -> TenantOperationalMaintenanceWindow:
    if ends_at <= starts_at:
        raise GovernanceError("Fim deve ser posterior ao início.")
    cleaned_title = _sanitize_or_raise(title, max_length=200)
    cleaned_description = _sanitize_or_raise(description, max_length=500)
    now = timezone.now()
    if now < starts_at:
        status = TenantOperationalMaintenanceWindow.Status.SCHEDULED
    elif now <= ends_at:
        status = TenantOperationalMaintenanceWindow.Status.ACTIVE
    else:
        status = TenantOperationalMaintenanceWindow.Status.ENDED

    window = TenantOperationalMaintenanceWindow.objects.create(
        tenant=tenant,
        title=cleaned_title,
        description=cleaned_description,
        starts_at=starts_at,
        ends_at=ends_at,
        scope=scope,
        scope_categories=list(scope_categories or []),
        scope_rule_ids=list(scope_rule_ids or []),
        scope_resource_reference=str(scope_resource_reference or "")[:120],
        created_by=actor,
        status=status,
    )
    record_audit_event(
        action=ACTION_OPERATIONAL_MAINTENANCE_CREATED,
        actor=actor,
        tenant=tenant,
        object_type="TenantOperationalMaintenanceWindow",
        object_id=str(window.pk),
        object_repr=window.title,
        metadata={"scope": scope, "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()},
        request=request,
    )
    from knowledge_base.rag.operational_notification_hooks import notify_maintenance_started

    if status == TenantOperationalMaintenanceWindow.Status.ACTIVE:
        notify_maintenance_started(tenant=tenant, maintenance_id=window.pk, actor=actor, request=request)
    return window


def cancel_maintenance_window(
    *,
    tenant,
    window_id: int,
    actor,
    cancellation_note: str,
    request=None,
) -> TenantOperationalMaintenanceWindow:
    note = _sanitize_or_raise(cancellation_note)
    with transaction.atomic():
        window = (
            TenantOperationalMaintenanceWindow.objects.select_for_update()
            .filter(tenant=tenant, pk=window_id)
            .first()
        )
        if window is None:
            raise GovernanceError("Janela de manutenção não encontrada.")
        if window.status == TenantOperationalMaintenanceWindow.Status.CANCELLED:
            raise GovernanceError("Janela já cancelada.")
        now = timezone.now()
        window.status = TenantOperationalMaintenanceWindow.Status.CANCELLED
        window.cancelled_at = now
        window.cancelled_by = actor
        window.cancellation_note = note
        window.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_note", "updated_at"])
        record_audit_event(
            action=ACTION_OPERATIONAL_MAINTENANCE_CANCELLED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalMaintenanceWindow",
            object_id=str(window.pk),
            object_repr=window.title,
            metadata={"note": note[:120]},
            request=request,
        )
        from knowledge_base.rag.operational_notification_hooks import notify_maintenance_cancelled

        notify_maintenance_cancelled(tenant=tenant, maintenance_id=window.pk, actor=actor, request=request)
        return window
