from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_MONITORING_COMPLETED,
    ACTION_OPERATIONAL_MONITORING_FAILED,
    ACTION_OPERATIONAL_MONITORING_PARTIAL,
    ACTION_OPERATIONAL_MONITORING_RECOVERED,
    ACTION_OPERATIONAL_MONITORING_SKIPPED,
    ACTION_OPERATIONAL_MONITORING_STARTED,
)
from audit.services import record_audit_event
from knowledge_base.models import (
    OperationalMonitoringBatchRun,
    TenantOperationalMonitoringRun,
    TenantRagConfiguration,
)
from knowledge_base.rag.operational_alert_sync import sync_operational_alerts
from tenants.models import Tenant

logger = logging.getLogger(__name__)

ADVISORY_LOCK_ID = 915_120_001


class OperationalMonitoringError(Exception):
    pass


@dataclass(frozen=True)
class MonitoringGateStatus:
    enabled: bool
    dry_run: bool


@dataclass(frozen=True)
class ProcessOperationalMonitoringResult:
    batch_id: int | None
    status: str
    dry_run: bool
    tenants_processed: int
    tenants_failed: int
    tenants_skipped: int
    alerts_created: int
    alerts_updated: int
    alerts_resolved: int
    alerts_reopened: int
    duration_ms: int
    error_summary: str = ""


def monitoring_gate_status() -> MonitoringGateStatus:
    return MonitoringGateStatus(
        enabled=bool(getattr(settings, "LIVIA_OPERATIONAL_MONITORING_ENABLED", False)),
        dry_run=bool(getattr(settings, "LIVIA_OPERATIONAL_MONITORING_DRY_RUN", True)),
    )


def _lease_seconds() -> int:
    return max(60, int(getattr(settings, "LIVIA_OPERATIONAL_MONITORING_LEASE_SECONDS", 900)))


def _max_tenants(limit: int | None) -> int:
    configured = max(1, int(getattr(settings, "LIVIA_OPERATIONAL_MONITORING_MAX_TENANTS", 20)))
    if limit is None:
        return configured
    return max(1, min(configured, int(limit)))


def _worker_identifier() -> str:
    return str(getattr(settings, "LIVIA_MONITORING_WORKER_ID", "") or socket.gethostname() or "unknown")[:120]


def classify_monitoring_error(exc: BaseException) -> tuple[str, str]:
    name = exc.__class__.__name__.lower()
    message = str(exc)[:500]
    if "timeout" in name or "timeout" in message.lower():
        return "timeout", "Timeout durante monitoramento."
    if "database" in name or "operationalerror" in name:
        return "database_error", "Erro de banco durante monitoramento."
    if isinstance(exc, OperationalMonitoringError):
        return "configuration_error", message[:500]
    if "lock" in message.lower() or "concurr" in message.lower():
        return "concurrency_conflict", "Conflito de concorrência no monitoramento."
    if "sync" in message.lower() or "alert" in message.lower():
        return "alert_sync_error", "Falha ao sincronizar alertas."
    if "diagnostic" in message.lower() or "health" in message.lower():
        return "diagnostic_error", "Falha ao calcular diagnóstico."
    return "unexpected_error", message[:500] or "Erro inesperado."


def get_eligible_tenants(*, tenant_slug: str | None = None) -> list[Tenant]:
    queryset = (
        Tenant.objects.filter(is_active=True, rag_configuration__operational_monitoring_enabled=True)
        .exclude(rag_configuration__approved_folder_id="")
        .select_related("rag_configuration")
        .order_by("slug")
    )
    if tenant_slug:
        queryset = queryset.filter(slug=str(tenant_slug).strip())
    return list(queryset)


def recover_stale_monitoring_batches() -> int:
    now = timezone.now()
    recovered = 0
    stale_batches = OperationalMonitoringBatchRun.objects.filter(
        status=OperationalMonitoringBatchRun.Status.RUNNING,
        lease_expires_at__lt=now,
    )
    for batch in stale_batches:
        batch.status = OperationalMonitoringBatchRun.Status.FAILED
        batch.finished_at = now
        batch.error_category = "timeout"
        batch.error_summary = "Execução stale recuperada (lease expirado)."
        batch.save(
            update_fields=["status", "finished_at", "error_category", "error_summary"]
        )
        TenantOperationalMonitoringRun.objects.filter(
            batch=batch,
            status=TenantOperationalMonitoringRun.Status.RUNNING,
        ).update(
            status=TenantOperationalMonitoringRun.Status.FAILED,
            finished_at=now,
            error_category="timeout",
            error_summary="Execução stale recuperada.",
        )
        record_audit_event(
            action=ACTION_OPERATIONAL_MONITORING_RECOVERED,
            tenant=None,
            object_type="OperationalMonitoringBatchRun",
            object_id=str(batch.pk),
            object_repr=f"batch={batch.pk}",
            metadata={"batch_id": batch.pk, "trigger": batch.trigger},
        )
        recovered += 1
    return recovered


def _try_acquire_advisory_lock() -> bool:
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_ID])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_advisory_lock() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_ID])


def process_operational_monitoring(
    *,
    tenant_slug: str | None = None,
    all_eligible: bool = False,
    limit: int | None = None,
    period: str = "7d",
    trigger: str = OperationalMonitoringBatchRun.Trigger.CLI,
    dry_run: bool | None = None,
    actor=None,
    request=None,
    fail_fast: bool = False,
) -> ProcessOperationalMonitoringResult:
    gate = monitoring_gate_status()
    if trigger == OperationalMonitoringBatchRun.Trigger.PORTAL:
        effective_dry_run = False
    elif dry_run is not None:
        effective_dry_run = bool(dry_run)
    else:
        effective_dry_run = gate.dry_run
    started = time.monotonic()

    if not tenant_slug and not all_eligible:
        return ProcessOperationalMonitoringResult(
            batch_id=None,
            status=OperationalMonitoringBatchRun.Status.SKIPPED,
            dry_run=effective_dry_run,
            tenants_processed=0,
            tenants_failed=0,
            tenants_skipped=0,
            alerts_created=0,
            alerts_updated=0,
            alerts_resolved=0,
            alerts_reopened=0,
            duration_ms=0,
            error_summary="Informe --tenant ou --all-eligible.",
        )

    requires_global_gate = trigger in {
        OperationalMonitoringBatchRun.Trigger.SCHEDULER,
    } or (trigger == OperationalMonitoringBatchRun.Trigger.CLI and all_eligible)

    if requires_global_gate and not gate.enabled and trigger != OperationalMonitoringBatchRun.Trigger.TEST:
        batch = _create_skipped_batch(
            trigger=trigger,
            period=period,
            dry_run=effective_dry_run,
            summary="Gate global LIVIA_OPERATIONAL_MONITORING_ENABLED desligado.",
        )
        return _result_from_batch(batch, started)

    recover_stale_monitoring_batches()

    tenants = get_eligible_tenants(tenant_slug=tenant_slug)
    if tenant_slug and not tenants and trigger == OperationalMonitoringBatchRun.Trigger.PORTAL:
        manual_tenant = Tenant.objects.filter(slug=str(tenant_slug).strip(), is_active=True).first()
        if manual_tenant:
            tenants = [manual_tenant]
    if tenant_slug and not tenants:
        batch = _create_skipped_batch(
            trigger=trigger,
            period=period,
            dry_run=effective_dry_run,
            summary=f"Tenant {tenant_slug} não elegível para monitoramento.",
        )
        return _result_from_batch(batch, started)

    tenant_limit = _max_tenants(limit)
    tenants = tenants[:tenant_limit]

    if not tenants:
        batch = _create_skipped_batch(
            trigger=trigger,
            period=period,
            dry_run=effective_dry_run,
            summary="Nenhum tenant elegível para monitoramento.",
        )
        return _result_from_batch(batch, started)

    advisory_locked = _try_acquire_advisory_lock()
    if not advisory_locked:
        batch = _create_skipped_batch(
            trigger=trigger,
            period=period,
            dry_run=effective_dry_run,
            summary="Outra execução de monitoramento está ativa (advisory lock).",
            error_category="concurrency_conflict",
        )
        return _result_from_batch(batch, started)

    now = timezone.now()
    active_batch = OperationalMonitoringBatchRun.objects.filter(
        status=OperationalMonitoringBatchRun.Status.RUNNING,
        lease_expires_at__gt=now,
    ).exists()
    if active_batch:
        batch = _create_skipped_batch(
            trigger=trigger,
            period=period,
            dry_run=effective_dry_run,
            summary="Outra execução de monitoramento está ativa.",
            error_category="concurrency_conflict",
        )
        _release_advisory_lock()
        return _result_from_batch(batch, started)

    now = timezone.now()
    batch = OperationalMonitoringBatchRun.objects.create(
        trigger=trigger,
        status=OperationalMonitoringBatchRun.Status.RUNNING,
        dry_run=effective_dry_run,
        period=period,
        worker_identifier=_worker_identifier(),
        started_at=now,
        lease_expires_at=now + timezone.timedelta(seconds=_lease_seconds()),
        last_heartbeat_at=now,
        tenants_total=len(tenants),
    )
    record_audit_event(
        action=ACTION_OPERATIONAL_MONITORING_STARTED,
        actor=actor,
        tenant=None,
        object_type="OperationalMonitoringBatchRun",
        object_id=str(batch.pk),
        object_repr=f"batch={batch.pk}",
        metadata={
            "batch_id": batch.pk,
            "trigger": trigger,
            "dry_run": effective_dry_run,
            "tenants_total": len(tenants),
        },
        request=request,
    )

    processed = failed = skipped = 0
    alerts_created = alerts_updated = alerts_resolved = alerts_reopened = 0
    warnings: list[str] = []

    try:
        for tenant in tenants:
            batch.last_heartbeat_at = timezone.now()
            batch.lease_expires_at = batch.last_heartbeat_at + timezone.timedelta(seconds=_lease_seconds())
            batch.save(update_fields=["last_heartbeat_at", "lease_expires_at"])

            try:
                tenant_result = _process_single_tenant(
                    batch=batch,
                    tenant=tenant,
                    period=period,
                    dry_run=effective_dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                category, summary = classify_monitoring_error(exc)
                failed += 1
                warnings.append(f"{tenant.slug}: {summary}")
                run = TenantOperationalMonitoringRun.objects.create(
                    batch=batch,
                    tenant=tenant,
                    status=TenantOperationalMonitoringRun.Status.FAILED,
                    started_at=timezone.now(),
                    finished_at=timezone.now(),
                    error_category=category,
                    error_summary=summary,
                )
                from knowledge_base.rag.operational_notification_hooks import notify_monitoring_failed

                notify_monitoring_failed(tenant=tenant, monitoring_run_id=run.pk, actor=actor)
                logger.exception(
                    "operational_monitoring.tenant_failed batch_id=%s tenant=%s category=%s",
                    batch.pk,
                    tenant.slug,
                    category,
                )
                if fail_fast:
                    break
                continue

            if tenant_result.status == TenantOperationalMonitoringRun.Status.SKIPPED:
                skipped += 1
            elif tenant_result.status == TenantOperationalMonitoringRun.Status.SUCCEEDED:
                processed += 1
                alerts_created += tenant_result.alerts_created
                alerts_updated += tenant_result.alerts_updated
                alerts_resolved += tenant_result.alerts_resolved
                alerts_reopened += tenant_result.alerts_reopened
            else:
                failed += 1
                if tenant_result.error_summary:
                    warnings.append(f"{tenant.slug}: {tenant_result.error_summary}")

        batch.tenants_processed = processed
        batch.tenants_failed = failed
        batch.tenants_skipped = skipped
        batch.alerts_created = alerts_created
        batch.alerts_updated = alerts_updated
        batch.alerts_resolved = alerts_resolved
        batch.alerts_reopened = alerts_reopened
        batch.warnings = warnings[:20]
        batch.finished_at = timezone.now()
        batch.duration_ms = int((time.monotonic() - started) * 1000)

        if processed == 0 and failed > 0:
            batch.status = OperationalMonitoringBatchRun.Status.FAILED
            batch.error_category = "unexpected_error"
            batch.error_summary = warnings[0] if warnings else "Nenhum tenant processado."
            audit_action = ACTION_OPERATIONAL_MONITORING_FAILED
        elif failed > 0:
            batch.status = OperationalMonitoringBatchRun.Status.PARTIAL
            batch.error_category = "unexpected_error"
            batch.error_summary = f"{failed} tenant(s) falharam."
            audit_action = ACTION_OPERATIONAL_MONITORING_PARTIAL
        else:
            batch.status = OperationalMonitoringBatchRun.Status.SUCCEEDED
            audit_action = ACTION_OPERATIONAL_MONITORING_COMPLETED

        batch.save()
        record_audit_event(
            action=audit_action,
            actor=actor,
            tenant=None,
            object_type="OperationalMonitoringBatchRun",
            object_id=str(batch.pk),
            object_repr=f"batch={batch.pk}",
            metadata={
                "batch_id": batch.pk,
                "trigger": trigger,
                "status": batch.status,
                "tenants_processed": processed,
                "tenants_failed": failed,
                "alerts_created": alerts_created,
                "duration_ms": batch.duration_ms,
            },
            request=request,
        )
        return _result_from_batch(batch, started)
    finally:
        _release_advisory_lock()


def _process_single_tenant(
    *,
    batch: OperationalMonitoringBatchRun,
    tenant: Tenant,
    period: str,
    dry_run: bool,
) -> TenantOperationalMonitoringRun:
    started = timezone.now()
    tenant_run = TenantOperationalMonitoringRun.objects.create(
        batch=batch,
        tenant=tenant,
        status=TenantOperationalMonitoringRun.Status.RUNNING,
        started_at=started,
    )
    with transaction.atomic():
        sync_result = sync_operational_alerts(
            tenant=tenant,
            period=period,
            source=f"operational_monitoring.batch:{batch.pk}",
            dry_run=dry_run,
            record_sync_audit=False,
            sync_batch_id=str(batch.pk),
        )
        from knowledge_base.rag.operational_work_queue_services import process_operational_work_queue

        queue_result = process_operational_work_queue(
            tenant=tenant,
            actor=None,
            dry_run=dry_run,
        )
    finished = timezone.now()
    tenant_run.status = TenantOperationalMonitoringRun.Status.SUCCEEDED
    tenant_run.finished_at = finished
    tenant_run.duration_ms = int((finished - started).total_seconds() * 1000)
    tenant_run.alerts_created = sync_result.created
    tenant_run.alerts_updated = sync_result.updated
    tenant_run.alerts_resolved = sync_result.auto_resolved
    tenant_run.alerts_reopened = sync_result.reopened
    tenant_run.alerts_active = sync_result.active
    tenant_run.save()
    logger.info(
        "operational_monitoring.tenant_completed batch_id=%s tenant=%s created=%s updated=%s resolved=%s escalated=%s duration_ms=%s",
        batch.pk,
        tenant.slug,
        sync_result.created,
        sync_result.updated,
        sync_result.auto_resolved,
        queue_result.auto_escalated,
        tenant_run.duration_ms,
    )
    return tenant_run


def _create_skipped_batch(
    *,
    trigger: str,
    period: str,
    dry_run: bool,
    summary: str,
    error_category: str = "configuration_error",
) -> OperationalMonitoringBatchRun:
    now = timezone.now()
    batch = OperationalMonitoringBatchRun.objects.create(
        trigger=trigger,
        status=OperationalMonitoringBatchRun.Status.SKIPPED,
        dry_run=dry_run,
        period=period,
        worker_identifier=_worker_identifier(),
        started_at=now,
        finished_at=now,
        error_category=error_category,
        error_summary=summary[:500],
    )
    record_audit_event(
        action=ACTION_OPERATIONAL_MONITORING_SKIPPED,
        tenant=None,
        object_type="OperationalMonitoringBatchRun",
        object_id=str(batch.pk),
        object_repr=f"batch={batch.pk}",
        metadata={"batch_id": batch.pk, "trigger": trigger, "summary": summary[:200]},
    )
    return batch


def _result_from_batch(batch: OperationalMonitoringBatchRun, started: float) -> ProcessOperationalMonitoringResult:
    duration_ms = batch.duration_ms or int((time.monotonic() - started) * 1000)
    return ProcessOperationalMonitoringResult(
        batch_id=batch.pk,
        status=batch.status,
        dry_run=batch.dry_run,
        tenants_processed=batch.tenants_processed,
        tenants_failed=batch.tenants_failed,
        tenants_skipped=batch.tenants_skipped,
        alerts_created=batch.alerts_created,
        alerts_updated=batch.alerts_updated,
        alerts_resolved=batch.alerts_resolved,
        alerts_reopened=batch.alerts_reopened,
        duration_ms=duration_ms,
        error_summary=batch.error_summary,
    )


def build_tenant_monitoring_summary(*, tenant) -> dict:
    gate = monitoring_gate_status()
    configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    tenant_enabled = bool(configuration and configuration.operational_monitoring_enabled)
    eligible = bool(
        tenant.is_active
        and configuration
        and configuration.approved_folder_id
        and tenant_enabled
    )

    last_run = (
        TenantOperationalMonitoringRun.objects.filter(tenant=tenant)
        .select_related("batch")
        .order_by("-started_at", "-id")
        .first()
    )
    last_success = (
        TenantOperationalMonitoringRun.objects.filter(
            tenant=tenant,
            status=TenantOperationalMonitoringRun.Status.SUCCEEDED,
        )
        .order_by("-finished_at", "-id")
        .first()
    )
    last_failure = (
        TenantOperationalMonitoringRun.objects.filter(
            tenant=tenant,
            status=TenantOperationalMonitoringRun.Status.FAILED,
        )
        .order_by("-finished_at", "-id")
        .first()
    )
    active_batch = OperationalMonitoringBatchRun.objects.filter(
        status=OperationalMonitoringBatchRun.Status.RUNNING,
    ).first()

    stale_success_hours = int(getattr(settings, "LIVIA_OPERATIONAL_MONITORING_STALE_SUCCESS_HOURS", 24))
    monitoring_stale = False
    if gate.enabled and eligible and not gate.dry_run:
        if last_success is None or (
            last_success.finished_at
            and last_success.finished_at < timezone.now() - timezone.timedelta(hours=stale_success_hours)
        ):
            monitoring_stale = True

    metrics_24h = _monitoring_metrics_since(hours=24)

    return {
        "gate_enabled": gate.enabled,
        "gate_dry_run": gate.dry_run,
        "tenant_enabled": tenant_enabled,
        "tenant_eligible": eligible,
        "last_run_at": last_run.started_at if last_run else None,
        "last_run_status": last_run.status if last_run else "",
        "last_success_at": last_success.finished_at if last_success else None,
        "last_failure_at": last_failure.finished_at if last_failure else None,
        "last_failure_summary": last_failure.error_summary if last_failure else "",
        "active_batch_id": active_batch.pk if active_batch else None,
        "active_batch_started_at": active_batch.started_at if active_batch else None,
        "monitoring_stale_warning": monitoring_stale,
        "metrics_24h": metrics_24h,
        "timer_documented_interval": "15 min (template systemd — não ativo por padrão)",
    }


def build_monitoring_readiness_checks(*, tenant) -> list[dict]:
    summary = build_tenant_monitoring_summary(tenant=tenant)
    checks: list[dict] = []
    checks.append(
        {
            "code": "monitoring_gate",
            "ok": not summary["gate_enabled"] or summary["gate_enabled"],
            "severity": "info",
            "detail": f"enabled={summary['gate_enabled']} dry_run={summary['gate_dry_run']}",
        }
    )
    checks.append(
        {
            "code": "monitoring_tenant_enabled",
            "ok": summary["tenant_eligible"] or not summary["tenant_enabled"],
            "severity": "warning" if summary["tenant_enabled"] and not summary["tenant_eligible"] else "info",
            "detail": f"eligible={summary['tenant_eligible']}",
        }
    )
    if summary["gate_enabled"] and summary["tenant_eligible"]:
        checks.append(
            {
                "code": "monitoring_last_success",
                "ok": not summary["monitoring_stale_warning"],
                "severity": "warning" if summary["monitoring_stale_warning"] else "info",
                "detail": (
                    "sem execução bem-sucedida recente"
                    if summary["monitoring_stale_warning"]
                    else "execução recente ok"
                ),
            }
        )
    active = summary["active_batch_id"] is not None
    checks.append(
        {
            "code": "monitoring_active_run",
            "ok": not active,
            "severity": "info" if active else "info",
            "detail": f"active_batch={summary['active_batch_id'] or '-'}",
        }
    )
    return checks


def _monitoring_metrics_since(*, hours: int) -> dict:
    from django.db.models import Sum

    since = timezone.now() - timezone.timedelta(hours=hours)
    batches = OperationalMonitoringBatchRun.objects.filter(started_at__gte=since)
    total = batches.count()
    succeeded = batches.filter(status=OperationalMonitoringBatchRun.Status.SUCCEEDED).count()
    durations = [item.duration_ms for item in batches.exclude(duration_ms=0).only("duration_ms")]
    durations.sort()
    median = durations[len(durations) // 2] if durations else 0
    aggregates = batches.aggregate(
        alerts_created=Sum("alerts_created"),
        alerts_resolved=Sum("alerts_resolved"),
    )
    return {
        "executions": total,
        "success_rate": round(succeeded / total, 4) if total else None,
        "median_duration_ms": median,
        "alerts_created": int(aggregates["alerts_created"] or 0),
        "alerts_resolved": int(aggregates["alerts_resolved"] or 0),
    }


def prune_operational_monitoring_runs(*, days: int | None = None) -> dict:
    retention_days = max(1, int(days or getattr(settings, "LIVIA_OPERATIONAL_MONITORING_RETENTION_DAYS", 90)))
    cutoff = timezone.now() - timezone.timedelta(days=retention_days)
    old_batches = OperationalMonitoringBatchRun.objects.filter(started_at__lt=cutoff)
    batch_count = old_batches.count()
    tenant_count = TenantOperationalMonitoringRun.objects.filter(batch__in=old_batches).count()
    old_batches.delete()
    return {
        "retention_days": retention_days,
        "batches_deleted": batch_count,
        "tenant_runs_deleted": tenant_count,
    }
