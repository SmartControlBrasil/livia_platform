from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_ALERT_CLAIMED,
    ACTION_OPERATIONAL_ALERT_DEESCALATED,
    ACTION_OPERATIONAL_ALERT_ESCALATED,
    ACTION_OPERATIONAL_ALERT_OWNER_INVALIDATED,
    ACTION_OPERATIONAL_ALERT_TRANSFERRED,
    ACTION_OPERATIONAL_ALERT_UNASSIGNED,
)
from audit.services import record_audit_event
from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.alert_governance import build_alert_governance_state, get_active_maintenance_windows, sanitize_governance_text
from knowledge_base.rag.alert_governance_services import GovernanceError, _get_membership
from knowledge_base.rag.operational_work_queue import (
    ESCALATION_LEVEL_ADMIN,
    ESCALATION_LEVEL_NORMAL,
    TRIGGER_INACTIVE_OWNER,
    TRIGGER_MANUAL,
    AutoEscalationCandidate,
    evaluate_auto_escalation,
)
from tenants.models import TenantMembership


class WorkQueueError(Exception):
    pass


@dataclass(frozen=True)
class ProcessWorkQueueResult:
    tenant_slug: str
    inactive_owners_cleared: int
    auto_escalated: int
    candidates: list[AutoEscalationCandidate]
    dry_run: bool


def _sanitize_reason(value: str, *, required: bool = True) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned and not required:
        return ""
    try:
        return sanitize_governance_text(cleaned)
    except ValueError as exc:
        raise WorkQueueError(str(exc)) from exc


def _validate_operate_membership(*, tenant, membership_id: int) -> TenantMembership:
    membership = TenantMembership.objects.filter(
        pk=membership_id,
        tenant=tenant,
        is_active=True,
    ).select_related("user").first()
    if membership is None:
        raise WorkQueueError("Membership inválida para este tenant.")
    if not membership.user.is_active:
        raise WorkQueueError("Usuário destinatário inativo.")
    return membership


def claim_operational_alert(*, tenant, alert_id: int, actor, request=None) -> TenantOperationalAlert:
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise WorkQueueError("Alerta não encontrado.")
        if alert.status == TenantOperationalAlert.Status.RESOLVED:
            raise WorkQueueError("Alerta resolvido não pode ser assumido.")
        if alert.assigned_to_id:
            raise WorkQueueError("Alerta já possui responsável.")
        membership = _get_membership(tenant=tenant, user=actor)
        now = timezone.now()
        alert.assigned_to = membership
        alert.assigned_by = actor
        alert.assigned_at = now
        alert.save(update_fields=["assigned_to", "assigned_by", "assigned_at", "updated_at"])
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_CLAIMED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={"membership_id": membership.pk},
            request=request,
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_assigned

        notify_alert_assigned(alert=alert, membership_id=membership.pk, actor=actor, request=request)
        return alert


def transfer_operational_alert(
    *,
    tenant,
    alert_id: int,
    actor,
    membership_id: int,
    reason: str,
    request=None,
) -> TenantOperationalAlert:
    cleaned_reason = _sanitize_reason(reason, required=True)
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise WorkQueueError("Alerta não encontrado.")
        if alert.status == TenantOperationalAlert.Status.RESOLVED:
            raise WorkQueueError("Alerta resolvido não pode ser transferido.")
        if alert.severity == TenantOperationalAlert.Severity.CRITICAL and len(cleaned_reason) < 10:
            raise WorkQueueError("Transferência de alerta crítico exige motivo descritivo.")
        target = _validate_operate_membership(tenant=tenant, membership_id=membership_id)
        previous_id = alert.assigned_to_id
        now = timezone.now()
        alert.assigned_to = target
        alert.assigned_by = actor
        alert.assigned_at = now
        alert.save(update_fields=["assigned_to", "assigned_by", "assigned_at", "updated_at"])
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_TRANSFERRED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={
                "previous_membership_id": previous_id,
                "new_membership_id": target.pk,
                "reason": cleaned_reason[:120],
            },
            request=request,
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_transferred

        notify_alert_transferred(
            alert=alert,
            new_membership_id=target.pk,
            previous_membership_id=previous_id,
            actor=actor,
            request=request,
        )
        return alert


def unassign_operational_alert_work_queue(
    *,
    tenant,
    alert_id: int,
    actor,
    reason: str = "",
    request=None,
) -> TenantOperationalAlert:
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise WorkQueueError("Alerta não encontrado.")
        if alert.assigned_to_id is None:
            raise WorkQueueError("Alerta não possui responsável.")
        previous_id = alert.assigned_to_id
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
            metadata={"previous_membership_id": previous_id, "reason": reason[:120]},
            request=request,
        )
        return alert


def escalate_operational_alert_manual(
    *,
    tenant,
    alert_id: int,
    actor,
    target_level: int,
    reason: str,
    request=None,
) -> TenantOperationalAlert:
    cleaned_reason = _sanitize_reason(reason)
    level = int(target_level)
    if level < ESCALATION_LEVEL_NORMAL or level > ESCALATION_LEVEL_ADMIN:
        raise WorkQueueError("Nível de escalonamento inválido.")
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise WorkQueueError("Alerta não encontrado.")
        if alert.status == TenantOperationalAlert.Status.RESOLVED:
            raise WorkQueueError("Alerta resolvido não pode ser escalado.")
        if level <= int(alert.escalation_level or 0):
            raise WorkQueueError("Escalonamento manual deve elevar o nível.")
        now = timezone.now()
        previous = alert.escalation_level
        alert.escalation_level = level
        alert.escalated_at = now
        alert.escalation_trigger = TRIGGER_MANUAL
        alert.escalation_reason = cleaned_reason
        alert.save(
            update_fields=[
                "escalation_level",
                "escalated_at",
                "escalation_trigger",
                "escalation_reason",
                "updated_at",
            ]
        )
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_ESCALATED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={
                "previous_level": previous,
                "target_level": level,
                "trigger": TRIGGER_MANUAL,
            },
            request=request,
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_escalated

        notify_alert_escalated(alert=alert, previous_level=previous, actor=actor, request=request)
        return alert


def deescalate_operational_alert(
    *,
    tenant,
    alert_id: int,
    actor,
    reason: str,
    request=None,
) -> TenantOperationalAlert:
    cleaned_reason = _sanitize_reason(reason)
    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise WorkQueueError("Alerta não encontrado.")
        if int(alert.escalation_level or 0) <= ESCALATION_LEVEL_NORMAL:
            raise WorkQueueError("Alerta não está escalado.")
        previous = alert.escalation_level
        alert.escalation_level = ESCALATION_LEVEL_NORMAL
        alert.escalated_at = None
        alert.escalation_trigger = ""
        alert.escalation_reason = cleaned_reason
        alert.save(
            update_fields=[
                "escalation_level",
                "escalated_at",
                "escalation_trigger",
                "escalation_reason",
                "updated_at",
            ]
        )
        record_audit_event(
            action=ACTION_OPERATIONAL_ALERT_DEESCALATED,
            actor=actor,
            tenant=tenant,
            object_type="TenantOperationalAlert",
            object_id=str(alert.pk),
            object_repr=alert.rule_id,
            metadata={"previous_level": previous, "reason": cleaned_reason[:120]},
            request=request,
        )
        return alert


def clear_escalation_on_resolve(*, alert: TenantOperationalAlert) -> None:
    if int(alert.escalation_level or 0) <= ESCALATION_LEVEL_NORMAL:
        return
    alert.escalation_level = ESCALATION_LEVEL_NORMAL
    alert.escalated_at = None
    alert.escalation_trigger = ""
    alert.escalation_reason = ""


def _apply_auto_escalation(
    *,
    alert: TenantOperationalAlert,
    candidate: AutoEscalationCandidate,
    actor=None,
    request=None,
) -> None:
    now = timezone.now()
    previous = alert.escalation_level
    alert.escalation_level = candidate.target_level
    alert.escalated_at = now
    alert.escalation_trigger = candidate.trigger
    alert.escalation_reason = candidate.reason[:500]
    alert.save(
        update_fields=[
            "escalation_level",
            "escalated_at",
            "escalation_trigger",
            "escalation_reason",
            "updated_at",
        ]
    )
    record_audit_event(
        action=ACTION_OPERATIONAL_ALERT_ESCALATED,
        actor=actor,
        tenant=alert.tenant,
        object_type="TenantOperationalAlert",
        object_id=str(alert.pk),
        object_repr=alert.rule_id,
        metadata={
            "previous_level": previous,
            "target_level": candidate.target_level,
            "trigger": candidate.trigger,
        },
        request=request,
    )
    from knowledge_base.rag.operational_notification_hooks import notify_alert_escalated

    notify_alert_escalated(alert=alert, previous_level=previous, actor=actor, request=request)


def process_operational_work_queue(
    *,
    tenant,
    actor=None,
    dry_run: bool = False,
    request=None,
) -> ProcessWorkQueueResult:
    now = timezone.now()
    inactive_cleared = auto_escalated = 0
    candidates: list[AutoEscalationCandidate] = []

    with transaction.atomic():
        # PostgreSQL rejeita FOR UPDATE no lado nullable de OUTER JOIN (assigned_to).
        locked_pks = list(
            TenantOperationalAlert.objects.select_for_update()
            .filter(
                tenant=tenant,
                status__in=[
                    TenantOperationalAlert.Status.OPEN,
                    TenantOperationalAlert.Status.ACKNOWLEDGED,
                ],
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        alerts = list(
            TenantOperationalAlert.objects.filter(pk__in=locked_pks)
            .select_related("assigned_to", "assigned_to__user")
            .order_by("pk")
        )
        maintenance_windows = get_active_maintenance_windows(tenant=tenant, now=now)

        for alert in alerts:
            if alert.assigned_to_id and (
                not alert.assigned_to.is_active or not alert.assigned_to.user.is_active
            ):
                previous_id = alert.assigned_to_id
                if not dry_run:
                    alert.assigned_to = None
                    alert.assigned_by = None
                    alert.assigned_at = None
                    alert.save(update_fields=["assigned_to", "assigned_by", "assigned_at", "updated_at"])
                    record_audit_event(
                        action=ACTION_OPERATIONAL_ALERT_OWNER_INVALIDATED,
                        actor=actor,
                        tenant=tenant,
                        object_type="TenantOperationalAlert",
                        object_id=str(alert.pk),
                        object_repr=alert.rule_id,
                        metadata={"previous_membership_id": previous_id, "trigger": TRIGGER_INACTIVE_OWNER},
                        request=request,
                    )
                    from knowledge_base.rag.operational_notification_hooks import notify_owner_invalidated

                    notify_owner_invalidated(
                        alert=alert,
                        previous_membership_id=previous_id,
                        actor=actor,
                        request=request,
                    )
                inactive_cleared += 1

            governance = build_alert_governance_state(
                alert=alert,
                maintenance_windows=maintenance_windows,
                now=now,
            )
            candidate = evaluate_auto_escalation(alert=alert, governance=governance, now=now)
            if candidate is None:
                continue
            candidates.append(candidate)
            if dry_run:
                continue
            _apply_auto_escalation(alert=alert, candidate=candidate, actor=actor, request=request)
            auto_escalated += 1

        from knowledge_base.rag.operational_notification_hooks import evaluate_sla_breach_notifications

        if not dry_run:
            evaluate_sla_breach_notifications(tenant=tenant, actor=actor, request=request)

    return ProcessWorkQueueResult(
        tenant_slug=tenant.slug,
        inactive_owners_cleared=inactive_cleared,
        auto_escalated=auto_escalated,
        candidates=candidates,
        dry_run=dry_run,
    )
