from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.alert_governance import (
    AlertGovernanceState,
    build_alert_governance_state,
    get_active_maintenance_windows,
    is_rule_non_suppressible,
)

PRIORITY_P1 = 1
PRIORITY_P2 = 2
PRIORITY_P3 = 3
PRIORITY_P4 = 4

PRIORITY_LABELS = {
    PRIORITY_P1: "P1 — crítico imediato",
    PRIORITY_P2: "P2 — alto",
    PRIORITY_P3: "P3 — normal",
    PRIORITY_P4: "P4 — baixo",
}

ESCALATION_LEVEL_NORMAL = 0
ESCALATION_LEVEL_OPERATION = 1
ESCALATION_LEVEL_MANAGEMENT = 2
ESCALATION_LEVEL_ADMIN = 3

ESCALATION_LEVEL_LABELS = {
    ESCALATION_LEVEL_NORMAL: "Normal",
    ESCALATION_LEVEL_OPERATION: "Atenção da operação",
    ESCALATION_LEVEL_MANAGEMENT: "Gestão",
    ESCALATION_LEVEL_ADMIN: "Crítico administrativo",
}

TRIGGER_ACK_SLA = "ack_sla_breached"
TRIGGER_RESOLUTION_SLA = "resolution_sla_breached"
TRIGGER_UNASSIGNED_CRITICAL = "unassigned_critical"
TRIGGER_REPEATED_REOPEN = "repeated_reopen"
TRIGGER_INACTIVE_OWNER = "inactive_owner"
TRIGGER_MANUAL = "manual"


def _int_setting(name: str, default: int) -> int:
    try:
        return max(0, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default


def unassigned_critical_minutes() -> int:
    return _int_setting("LIVIA_ALERT_ESCALATION_UNASSIGNED_CRITICAL_MINUTES", 30)


def reopen_escalation_threshold() -> int:
    return max(2, _int_setting("LIVIA_ALERT_ESCALATION_REOPEN_THRESHOLD", 3))


def suspend_auto_escalation_under_maintenance() -> bool:
    return bool(getattr(settings, "LIVIA_ALERT_ESCALATION_SUSPEND_UNDER_MAINTENANCE", True))


def calculate_operational_priority(
    *,
    alert: TenantOperationalAlert,
    governance: AlertGovernanceState | None = None,
    now=None,
) -> int | None:
    now = now or timezone.now()
    if alert.status == TenantOperationalAlert.Status.RESOLVED:
        return None
    if governance is None:
        governance = build_alert_governance_state(alert=alert, now=now)

    non_suppressible = is_rule_non_suppressible(rule_id=alert.rule_id, category=alert.category)
    under_maintenance = governance.is_under_maintenance and not non_suppressible

    if alert.severity == TenantOperationalAlert.Severity.INFO:
        return PRIORITY_P4

    if alert.severity == TenantOperationalAlert.Severity.CRITICAL:
        if non_suppressible or governance.ack_sla_breached or governance.resolution_sla_breached:
            return PRIORITY_P1
        if alert.escalation_level >= ESCALATION_LEVEL_MANAGEMENT:
            return PRIORITY_P1
        if not alert.assigned_to_id:
            return PRIORITY_P1
        if under_maintenance:
            return PRIORITY_P2
        return PRIORITY_P2

    if alert.severity == TenantOperationalAlert.Severity.WARNING:
        if governance.resolution_sla_breached or governance.ack_sla_breached:
            return PRIORITY_P2
        if alert.escalation_level >= ESCALATION_LEVEL_OPERATION:
            return PRIORITY_P2
        if not alert.assigned_to_id:
            return PRIORITY_P3
        if under_maintenance:
            return PRIORITY_P4
        return PRIORITY_P3

    return PRIORITY_P4


def work_queue_sort_key(
    *,
    alert: TenantOperationalAlert,
    priority: int,
    governance: AlertGovernanceState,
) -> tuple:
    sla_breached = governance.ack_sla_breached or governance.resolution_sla_breached
    severity_rank = {
        TenantOperationalAlert.Severity.CRITICAL: 0,
        TenantOperationalAlert.Severity.WARNING: 1,
        TenantOperationalAlert.Severity.INFO: 2,
    }.get(alert.severity, 9)
    return (
        priority,
        0 if sla_breached else 1,
        severity_rank,
        alert.detected_at,
    )


@dataclass(frozen=True)
class AutoEscalationCandidate:
    alert_id: int
    rule_id: str
    current_level: int
    target_level: int
    trigger: str
    reason: str


def evaluate_auto_escalation(
    *,
    alert: TenantOperationalAlert,
    governance: AlertGovernanceState | None = None,
    now=None,
) -> AutoEscalationCandidate | None:
    now = now or timezone.now()
    if alert.status == TenantOperationalAlert.Status.RESOLVED:
        return None
    if governance is None:
        governance = build_alert_governance_state(alert=alert, now=now)

    non_suppressible = is_rule_non_suppressible(rule_id=alert.rule_id, category=alert.category)
    if (
        suspend_auto_escalation_under_maintenance()
        and governance.is_under_maintenance
        and not non_suppressible
    ):
        return None

    current = int(alert.escalation_level or 0)

    if alert.status == TenantOperationalAlert.Status.OPEN and governance.ack_sla_breached:
        target = max(current + 1, ESCALATION_LEVEL_OPERATION)
        if target > current:
            return AutoEscalationCandidate(
                alert_id=alert.pk,
                rule_id=alert.rule_id,
                current_level=current,
                target_level=min(target, ESCALATION_LEVEL_ADMIN),
                trigger=TRIGGER_ACK_SLA,
                reason="ACK SLA vencido sem reconhecimento.",
            )

    if governance.resolution_sla_breached:
        target = max(current + 1, ESCALATION_LEVEL_OPERATION)
        if alert.severity == TenantOperationalAlert.Severity.CRITICAL:
            target = max(target, ESCALATION_LEVEL_MANAGEMENT)
        if target > current:
            return AutoEscalationCandidate(
                alert_id=alert.pk,
                rule_id=alert.rule_id,
                current_level=current,
                target_level=min(target, ESCALATION_LEVEL_ADMIN),
                trigger=TRIGGER_RESOLUTION_SLA,
                reason="SLA de resolução vencido.",
            )

    if (
        alert.severity == TenantOperationalAlert.Severity.CRITICAL
        and not alert.assigned_to_id
        and alert.detected_at
        and now - alert.detected_at >= timedelta(minutes=unassigned_critical_minutes())
    ):
        target = max(current + 1, ESCALATION_LEVEL_OPERATION)
        if target > current:
            return AutoEscalationCandidate(
                alert_id=alert.pk,
                rule_id=alert.rule_id,
                current_level=current,
                target_level=min(target, ESCALATION_LEVEL_ADMIN),
                trigger=TRIGGER_UNASSIGNED_CRITICAL,
                reason="Alerta crítico sem responsável além do limite configurado.",
            )

    threshold = reopen_escalation_threshold()
    if alert.reopen_count >= threshold:
        target = max(current + 1, ESCALATION_LEVEL_OPERATION)
        if target > current:
            return AutoEscalationCandidate(
                alert_id=alert.pk,
                rule_id=alert.rule_id,
                current_level=current,
                target_level=min(target, ESCALATION_LEVEL_ADMIN),
                trigger=TRIGGER_REPEATED_REOPEN,
                reason=f"Condição reaberta {alert.reopen_count} vez(es).",
            )

    return None


def build_work_queue_summary(*, tenant, now=None) -> dict:
    now = now or timezone.now()
    open_qs = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    ).select_related("assigned_to__user")

    maintenance_windows = get_active_maintenance_windows(tenant=tenant, now=now)
    p1 = p2 = unassigned = ack_breached = resolution_breached = escalated = 0

    for alert in open_qs:
        governance = build_alert_governance_state(
            alert=alert,
            maintenance_windows=maintenance_windows,
            now=now,
        )
        priority = calculate_operational_priority(alert=alert, governance=governance, now=now)
        if priority == PRIORITY_P1:
            p1 += 1
        elif priority == PRIORITY_P2:
            p2 += 1
        if not alert.assigned_to_id:
            unassigned += 1
        if governance.ack_sla_breached:
            ack_breached += 1
        if governance.resolution_sla_breached:
            resolution_breached += 1
        if alert.escalation_level > ESCALATION_LEVEL_NORMAL:
            escalated += 1

    return {
        "p1_open": p1,
        "p2_open": p2,
        "unassigned": unassigned,
        "ack_sla_breached": ack_breached,
        "resolution_sla_breached": resolution_breached,
        "escalated": escalated,
    }


def build_personal_work_count(*, tenant, membership) -> int:
    if membership is None:
        return 0
    return TenantOperationalAlert.objects.filter(
        tenant=tenant,
        assigned_to=membership,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    ).count()
