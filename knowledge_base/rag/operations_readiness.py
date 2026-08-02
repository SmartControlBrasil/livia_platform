from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.utils import timezone

from knowledge_base.models import TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.operations import _lease_seconds, _max_attempts, operations_gate_status
from tenants.models import Tenant


@dataclass(frozen=True)
class OperationsReadinessCheck:
    code: str
    ok: bool
    severity: str
    detail: str


def _migration_pending(app_label: str, model_name: str, field_names: list[str]) -> bool:
    model = apps.get_model(app_label, model_name)
    table = model._meta.db_table
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    existing = {column.name for column in description}
    return any(name not in existing for name in field_names)


def inspect_rag_operations_readiness(*, tenant: Tenant | None = None) -> list[OperationsReadinessCheck]:
    checks: list[OperationsReadinessCheck] = []
    gate = operations_gate_status()

    checks.append(
        OperationsReadinessCheck(
            code="operations_feature_gate",
            ok=True,
            severity="info",
            detail="disabled_by_default" if not gate.enabled else "enabled",
        )
    )
    checks.append(
        OperationsReadinessCheck(
            code="operations_dry_run",
            ok=True,
            severity="info",
            detail="simulation_forced" if gate.dry_run else "real_execution_allowed",
        )
    )
    checks.append(
        OperationsReadinessCheck(
            code="operations_lease_seconds",
            ok=_lease_seconds() >= 60,
            severity="error" if _lease_seconds() < 60 else "info",
            detail=f"lease={_lease_seconds()}s max_attempts={_max_attempts()}",
        )
    )

    migration_pending = _migration_pending(
        "knowledge_base",
        "TenantRagOperationRequest",
        ["last_heartbeat_at", "attempt_count"],
    )
    checks.append(
        OperationsReadinessCheck(
            code="operations_schema",
            ok=not migration_pending,
            severity="error" if migration_pending else "info",
            detail="pending_migrations" if migration_pending else "schema_ok",
        )
    )

    try:
        load_embedding_config()
        embedding_ok = True
        embedding_detail = "embedding_config_ok"
    except EmbeddingConfigurationError as exc:
        embedding_ok = False
        embedding_detail = str(exc)[:200]

    checks.append(
        OperationsReadinessCheck(
            code="embedding_config",
            ok=embedding_ok or gate.dry_run,
            severity="warning" if not embedding_ok and gate.enabled and not gate.dry_run else "info",
            detail=embedding_detail,
        )
    )

    if not getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False):
        checks.append(
            OperationsReadinessCheck(
                code="indexing_real_gate",
                ok=gate.dry_run or not gate.enabled,
                severity="info",
                detail="indexing_real_disabled",
            )
        )

    now = timezone.now()
    stale_qs = TenantRagOperationRequest.objects.filter(
        status=TenantRagOperationRequest.Status.RUNNING,
        lease_expires_at__lt=now,
    )
    active_qs = TenantRagOperationRequest.objects.filter(
        status__in=[
            TenantRagOperationRequest.Status.PENDING,
            TenantRagOperationRequest.Status.RUNNING,
        ]
    )
    if tenant is not None:
        stale_qs = stale_qs.filter(tenant=tenant)
        active_qs = active_qs.filter(tenant=tenant)
        configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        if configuration is None:
            checks.append(
                OperationsReadinessCheck(
                    code="tenant_configuration",
                    ok=False,
                    severity="warning",
                    detail="tenant_without_rag_configuration",
                )
            )
        elif not configuration.approved_folder_id:
            checks.append(
                OperationsReadinessCheck(
                    code="tenant_source",
                    ok=False,
                    severity="warning",
                    detail="approved_folder_missing",
                )
            )
        else:
            checks.append(
                OperationsReadinessCheck(
                    code="tenant_source",
                    ok=True,
                    severity="info",
                    detail="approved_folder_configured",
                )
            )

    stale_count = stale_qs.count()
    checks.append(
        OperationsReadinessCheck(
            code="stale_operations",
            ok=stale_count == 0,
            severity="warning" if stale_count else "info",
            detail=f"stale_running={stale_count}",
        )
    )
    checks.append(
        OperationsReadinessCheck(
            code="active_operations",
            ok=True,
            severity="info",
            detail=f"active_pending_or_running={active_qs.count()}",
        )
    )

    simulation_ready = (
        not migration_pending
        and (not tenant or TenantRagConfiguration.objects.filter(tenant=tenant).exists())
    )
    checks.append(
        OperationsReadinessCheck(
            code="simulation_ready",
            ok=simulation_ready and (gate.dry_run or not gate.enabled),
            severity="info",
            detail="ready_for_dry_run_simulation" if simulation_ready else "simulation_blocked",
        )
    )

    real_ready = (
        gate.enabled
        and not gate.dry_run
        and not migration_pending
        and stale_count == 0
        and embedding_ok
        and bool(getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False))
    )
    checks.append(
        OperationsReadinessCheck(
            code="real_execution_ready",
            ok=real_ready,
            severity="info",
            detail="ready_for_real_execution" if real_ready else "real_execution_not_ready",
        )
    )

    return checks


def readiness_has_blocking_errors(checks: list[OperationsReadinessCheck]) -> bool:
    return any(not check.ok and check.severity == "error" for check in checks)
