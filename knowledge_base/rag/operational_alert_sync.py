from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED,
    ACTION_OPERATIONAL_ALERT_ASSIGNED,
    ACTION_OPERATIONAL_ALERT_CREATED,
    ACTION_OPERATIONAL_ALERT_REOPENED,
    ACTION_OPERATIONAL_ALERT_RESOLVED,
    ACTION_OPERATIONAL_ALERT_SYNC,
    ACTION_OPERATIONAL_ALERT_UPDATED,
)
from audit.services import record_audit_event
from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.alert_governance import compute_sla_deadlines
from knowledge_base.rag.operational_alert_rules import AlertCandidate, evaluate_alert_candidates
from operations_portal.rag_health_services import build_rag_health_dashboard


class OperationalAlertError(Exception):
    pass


@dataclass(frozen=True)
class SyncOperationalAlertsResult:
    tenant_slug: str
    created: int
    updated: int
    reopened: int
    auto_resolved: int
    active: int
    dry_run: bool


def build_diagnostic_snapshot(*, tenant, period: str = "7d") -> dict:
    return build_rag_health_dashboard(tenant=tenant, period=period)


def sync_operational_alerts(
    *,
    tenant,
    period: str = "7d",
    source: str = "operational_alert_sync",
    actor=None,
    dry_run: bool = False,
    request=None,
    record_sync_audit: bool = True,
    sync_batch_id: str = "",
) -> SyncOperationalAlertsResult:
    snapshot = build_diagnostic_snapshot(tenant=tenant, period=period)
    candidates = evaluate_alert_candidates(tenant=tenant, snapshot=snapshot)
    candidate_map = {item.fingerprint: item for item in candidates}
    now = timezone.now()

    created = updated = reopened = auto_resolved = 0

    with transaction.atomic():
        existing = list(
            TenantOperationalAlert.objects.filter(tenant=tenant).select_for_update()
        )
        existing_by_fp = {alert.fingerprint: alert for alert in existing}

        for candidate in candidates:
            alert = existing_by_fp.get(candidate.fingerprint)
            if alert is None:
                created += 1
                if not dry_run:
                    ack_due_at, resolution_due_at = compute_sla_deadlines(
                        severity=candidate.severity,
                        detected_at=now,
                    )
                    try:
                        with transaction.atomic():
                            alert = TenantOperationalAlert.objects.create(
                                tenant=tenant,
                                category=candidate.category,
                                severity=candidate.severity,
                                status=TenantOperationalAlert.Status.OPEN,
                                rule_id=candidate.rule_id,
                                fingerprint=candidate.fingerprint,
                                title=candidate.title,
                                summary=candidate.summary,
                                detected_at=now,
                                last_seen_at=now,
                                occurrence_count=1,
                                source=source,
                                source_reference=candidate.source_reference,
                                metadata=candidate.metadata,
                                ack_due_at=ack_due_at,
                                resolution_due_at=resolution_due_at,
                                last_sync_batch_id=sync_batch_id,
                            )
                            _record_alert_audit(
                                action=ACTION_OPERATIONAL_ALERT_CREATED,
                                alert=alert,
                                actor=actor,
                                request=request,
                                metadata={"source": source},
                            )
                            from knowledge_base.rag.operational_notification_hooks import notify_alert_critical_created

                            notify_alert_critical_created(alert=alert, actor=actor, request=request)
                    except IntegrityError:
                        alert = TenantOperationalAlert.objects.select_for_update().get(
                            tenant=tenant,
                            fingerprint=candidate.fingerprint,
                        )
                        _update_existing_alert_from_candidate(
                            alert=alert,
                            candidate=candidate,
                            now=now,
                            sync_batch_id=sync_batch_id,
                        )
                        updated += 1
                        _record_alert_audit(
                            action=ACTION_OPERATIONAL_ALERT_UPDATED,
                            alert=alert,
                            actor=actor,
                            request=request,
                            metadata={"source": source, "concurrent_create": True},
                        )
                        existing_by_fp[candidate.fingerprint] = alert
                        created -= 1
                        continue
                existing_by_fp[candidate.fingerprint] = alert
                continue

            if alert.status == TenantOperationalAlert.Status.RESOLVED:
                reopened += 1
                if not dry_run:
                    previous_status = alert.status
                    ack_due_at, resolution_due_at = compute_sla_deadlines(
                        severity=candidate.severity,
                        detected_at=now,
                    )
                    alert.status = TenantOperationalAlert.Status.OPEN
                    alert.last_seen_at = now
                    alert.summary = candidate.summary
                    alert.severity = candidate.severity
                    alert.metadata = candidate.metadata
                    alert.resolved_at = None
                    alert.resolved_by = None
                    alert.resolution_note = ""
                    alert.resolution_source = ""
                    alert.acknowledged_at = None
                    alert.acknowledged_by = None
                    alert.reopen_count += 1
                    alert.last_reopened_at = now
                    alert.escalation_level = 0
                    alert.escalated_at = None
                    alert.escalation_trigger = ""
                    alert.escalation_reason = ""
                    alert.ack_due_at = ack_due_at
                    alert.resolution_due_at = resolution_due_at
                    if sync_batch_id and alert.last_sync_batch_id != sync_batch_id:
                        alert.occurrence_count += 1
                        alert.last_sync_batch_id = sync_batch_id
                    elif not sync_batch_id:
                        alert.occurrence_count += 1
                    alert.save(
                        update_fields=[
                            "status",
                            "last_seen_at",
                            "occurrence_count",
                            "summary",
                            "severity",
                            "metadata",
                            "resolved_at",
                            "resolved_by",
                            "resolution_note",
                            "resolution_source",
                            "acknowledged_at",
                            "acknowledged_by",
                            "ack_due_at",
                            "resolution_due_at",
                            "last_sync_batch_id",
                            "reopen_count",
                            "last_reopened_at",
                            "escalation_level",
                            "escalated_at",
                            "escalation_trigger",
                            "escalation_reason",
                            "updated_at",
                        ]
                    )
                    _record_alert_audit(
                        action=ACTION_OPERATIONAL_ALERT_REOPENED,
                        alert=alert,
                        actor=actor,
                        request=request,
                        metadata={"previous_status": previous_status, "source": source},
                    )
                    from knowledge_base.rag.operational_notification_hooks import notify_alert_reopened

                    notify_alert_reopened(alert=alert, actor=actor, request=request)
                continue

            updated += 1
            if not dry_run:
                _update_existing_alert_from_candidate(
                    alert=alert,
                    candidate=candidate,
                    now=now,
                    sync_batch_id=sync_batch_id,
                )
                _record_alert_audit(
                    action=ACTION_OPERATIONAL_ALERT_UPDATED,
                    alert=alert,
                    actor=actor,
                    request=request,
                    metadata={"source": source},
                )

        for alert in existing:
            if alert.fingerprint in candidate_map:
                continue
            if alert.status == TenantOperationalAlert.Status.RESOLVED:
                continue
            auto_resolved += 1
            if not dry_run:
                previous_status = alert.status
                alert.status = TenantOperationalAlert.Status.RESOLVED
                alert.resolved_at = now
                alert.resolved_by = None
                alert.resolution_source = TenantOperationalAlert.ResolutionSource.AUTO
                alert.resolution_note = "Resolvido automaticamente após health check saudável."
                from knowledge_base.rag.operational_work_queue_services import clear_escalation_on_resolve

                clear_escalation_on_resolve(alert=alert)
                alert.save(
                    update_fields=[
                        "status",
                        "resolved_at",
                        "resolved_by",
                        "resolution_source",
                        "resolution_note",
                        "escalation_level",
                        "escalated_at",
                        "escalation_trigger",
                        "escalation_reason",
                        "updated_at",
                    ]
                )
                _record_alert_audit(
                    action=ACTION_OPERATIONAL_ALERT_RESOLVED,
                    alert=alert,
                    actor=actor,
                    request=request,
                    metadata={
                        "previous_status": previous_status,
                        "resolution_source": "auto",
                        "source": source,
                    },
                )
                from knowledge_base.rag.operational_notification_hooks import notify_alert_resolved

                notify_alert_resolved(alert=alert, actor=actor, request=request)

        if not dry_run and record_sync_audit:
            record_audit_event(
                action=ACTION_OPERATIONAL_ALERT_SYNC,
                actor=actor,
                tenant=tenant,
                object_type="TenantOperationalAlert",
                object_id=str(tenant.pk),
                object_repr=f"sync tenant={tenant.slug}",
                metadata={
                    "source": source,
                    "created": created,
                    "updated": updated,
                    "reopened": reopened,
                    "auto_resolved": auto_resolved,
                    "active_candidates": len(candidate_map),
                },
                request=request,
            )

    active = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    ).count()

    return SyncOperationalAlertsResult(
        tenant_slug=tenant.slug,
        created=created,
        updated=updated,
        reopened=reopened,
        auto_resolved=auto_resolved,
        active=active,
        dry_run=dry_run,
    )


def acknowledge_operational_alert(*, tenant, alert_id: int, actor, request=None) -> TenantOperationalAlert:
    from knowledge_base.rag.alert_governance_services import GovernanceError, _get_membership

    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise OperationalAlertError("Alerta não encontrado.")
        if alert.status != TenantOperationalAlert.Status.OPEN:
            raise OperationalAlertError("Somente alertas abertos podem ser reconhecidos.")
        previous_status = alert.status
        now = timezone.now()
        assigned = False
        if alert.assigned_to_id is None:
            try:
                membership = _get_membership(tenant=tenant, user=actor)
            except GovernanceError as exc:
                raise OperationalAlertError(str(exc)) from exc
            alert.assigned_to = membership
            alert.assigned_by = actor
            alert.assigned_at = now
            assigned = True
        alert.status = TenantOperationalAlert.Status.ACKNOWLEDGED
        alert.acknowledged_at = now
        alert.acknowledged_by = actor
        update_fields = ["status", "acknowledged_at", "acknowledged_by", "updated_at"]
        if assigned:
            update_fields.extend(["assigned_to", "assigned_by", "assigned_at"])
        alert.save(update_fields=update_fields)
        if assigned:
            _record_alert_audit(
                action=ACTION_OPERATIONAL_ALERT_ASSIGNED,
                alert=alert,
                actor=actor,
                request=request,
                metadata={"membership_id": alert.assigned_to_id, "source": "acknowledge_auto_assign"},
            )
            from knowledge_base.rag.operational_notification_hooks import notify_alert_assigned

            notify_alert_assigned(alert=alert, membership_id=alert.assigned_to_id, actor=actor, request=request)
        _record_alert_audit(
            action=ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED,
            alert=alert,
            actor=actor,
            request=request,
            metadata={"previous_status": previous_status},
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_acknowledged

        notify_alert_acknowledged(alert=alert, actor=actor, request=request)
        return alert


def resolve_operational_alert(
    *,
    tenant,
    alert_id: int,
    actor,
    resolution_note: str,
    request=None,
) -> TenantOperationalAlert:
    note = str(resolution_note or "").strip()
    if not note:
        raise OperationalAlertError("Informe uma nota de resolução.")
    if len(note) > 500:
        raise OperationalAlertError("Nota de resolução excede 500 caracteres.")

    with transaction.atomic():
        alert = (
            TenantOperationalAlert.objects.select_for_update()
            .filter(tenant=tenant, pk=alert_id)
            .first()
        )
        if alert is None:
            raise OperationalAlertError("Alerta não encontrado.")
        if alert.status not in {
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        }:
            raise OperationalAlertError("Alerta já resolvido.")
        previous_status = alert.status
        now = timezone.now()
        alert.status = TenantOperationalAlert.Status.RESOLVED
        alert.resolved_at = now
        alert.resolved_by = actor
        alert.resolution_note = note
        alert.resolution_source = TenantOperationalAlert.ResolutionSource.MANUAL
        from knowledge_base.rag.operational_work_queue_services import clear_escalation_on_resolve

        clear_escalation_on_resolve(alert=alert)
        alert.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_note",
                "resolution_source",
                "escalation_level",
                "escalated_at",
                "escalation_trigger",
                "escalation_reason",
                "updated_at",
            ]
        )
        _record_alert_audit(
            action=ACTION_OPERATIONAL_ALERT_RESOLVED,
            alert=alert,
            actor=actor,
            request=request,
            metadata={"previous_status": previous_status, "resolution_source": "manual"},
        )
        from knowledge_base.rag.operational_notification_hooks import notify_alert_resolved

        notify_alert_resolved(alert=alert, actor=actor, request=request)
        return alert


def count_open_operational_alerts(*, tenant) -> dict:
    qs = TenantOperationalAlert.objects.filter(
        tenant=tenant,
        status__in=[
            TenantOperationalAlert.Status.OPEN,
            TenantOperationalAlert.Status.ACKNOWLEDGED,
        ],
    )
    return {
        "total": qs.count(),
        "critical": qs.filter(severity=TenantOperationalAlert.Severity.CRITICAL).count(),
        "warning": qs.filter(severity=TenantOperationalAlert.Severity.WARNING).count(),
        "acknowledged": qs.filter(status=TenantOperationalAlert.Status.ACKNOWLEDGED).count(),
    }


def tenant_has_synced_alerts(*, tenant) -> bool:
    return TenantOperationalAlert.objects.filter(tenant=tenant).exists()


def _record_alert_audit(*, action, alert, actor, request, metadata=None):
    record_audit_event(
        action=action,
        actor=actor,
        tenant=alert.tenant,
        object_type="TenantOperationalAlert",
        object_id=str(alert.pk),
        object_repr=f"{alert.rule_id} / {alert.status}",
        metadata={
            "category": alert.category,
            "severity": alert.severity,
            "fingerprint": alert.fingerprint,
            **(metadata or {}),
        },
        request=request,
    )


def _update_existing_alert_from_candidate(
    *,
    alert: TenantOperationalAlert,
    candidate: AlertCandidate,
    now,
    sync_batch_id: str,
) -> None:
    alert.last_seen_at = now
    alert.summary = candidate.summary
    alert.severity = candidate.severity
    alert.metadata = candidate.metadata
    update_fields = ["last_seen_at", "summary", "severity", "metadata", "updated_at"]
    if sync_batch_id:
        if alert.last_sync_batch_id != sync_batch_id:
            alert.occurrence_count += 1
            alert.last_sync_batch_id = sync_batch_id
            update_fields.extend(["occurrence_count", "last_sync_batch_id"])
    else:
        alert.occurrence_count += 1
        update_fields.append("occurrence_count")
    alert.save(update_fields=update_fields)
