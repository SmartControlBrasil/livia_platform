from __future__ import annotations

from dataclasses import dataclass

# Eventos notificáveis centralizados (Fase 15).
EVENT_ALERT_CRITICAL_CREATED = "alert_critical_created"
EVENT_ALERT_ASSIGNED = "alert_assigned"
EVENT_ALERT_TRANSFERRED = "alert_transferred"
EVENT_ALERT_ACKNOWLEDGED = "alert_acknowledged"
EVENT_ALERT_RESOLVED = "alert_resolved"
EVENT_ALERT_REOPENED = "alert_reopened"
EVENT_SLA_ACK_BREACHED = "sla_ack_breached"
EVENT_SLA_RESOLUTION_BREACHED = "sla_resolution_breached"
EVENT_ALERT_ESCALATED = "alert_escalated"
EVENT_ALERT_DEESCALATED = "alert_deescalated"
EVENT_OWNER_INVALIDATED = "owner_invalidated"
EVENT_MAINTENANCE_STARTED = "maintenance_started"
EVENT_MAINTENANCE_CANCELLED = "maintenance_cancelled"
EVENT_MONITORING_FAILED = "monitoring_failed"
EVENT_DIGEST = "operational_digest"

NOTIFIABLE_EVENTS = frozenset(
    {
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
    }
)

SLA_EVENTS = frozenset({EVENT_SLA_ACK_BREACHED, EVENT_SLA_RESOLUTION_BREACHED})


@dataclass(frozen=True)
class OperationalNotificationEvent:
    event_type: str
    tenant_id: int
    alert_id: int | None = None
    reopen_count: int = 0
    escalation_level: int = 0
    sla_type: str = ""
    maintenance_id: int | None = None
    monitoring_run_id: int | None = None
    previous_membership_id: int | None = None
    target_membership_id: int | None = None
    actor_membership_id: int | None = None
    metadata: dict | None = None
