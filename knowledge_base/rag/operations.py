from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import (
    ACTION_TENANT_RAG_INDEX_COMPLETED,
    ACTION_TENANT_RAG_INDEX_FAILED,
    ACTION_TENANT_RAG_INDEX_STARTED,
    ACTION_TENANT_RAG_OPERATION_COMPLETED,
    ACTION_TENANT_RAG_OPERATION_FAILED,
    ACTION_TENANT_RAG_OPERATION_REJECTED,
    ACTION_TENANT_RAG_OPERATION_REQUESTED,
    ACTION_TENANT_RAG_OPERATION_STARTED,
)
from audit.services import record_audit_event
from knowledge_base.models import (
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
    TenantRagIndexRun,
    TenantRagOperationRequest,
)
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.google_drive_inventory import (
    GoogleDriveAuthenticationError,
    GoogleDriveConfigurationError,
    GoogleDriveInventoryService,
    GoogleDrivePermissionError,
    build_google_drive_readonly_service,
    sanitize_external_error_message,
)
from knowledge_base.rag.indexing import (
    TenantRagIndexingError,
    acquire_tenant_index_lock,
    mark_index_failed,
    run_index_for_tenant,
)
from knowledge_base.rag.sync import (
    TenantRagSyncError,
    acquire_tenant_sync_lock,
    mark_configuration_failed,
    run_chunk_build_for_tenant,
    run_sync_for_inventory,
)
from tenants.models import Tenant

logger = logging.getLogger(__name__)

SYNC_OPERATION_MODES = {
    TenantRagOperationRequest.Operation.INVENTORY: "inventory_only",
    TenantRagOperationRequest.Operation.SYNC_EXPORT: "export_text",
    TenantRagOperationRequest.Operation.BUILD_CHUNKS: "build_chunks",
}


class RagOperationsError(Exception):
    def __init__(self, message: str, *, code: str = "operation_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OperationsGateStatus:
    enabled: bool
    dry_run: bool
    reason: str = ""


def operations_gate_status() -> OperationsGateStatus:
    enabled = bool(getattr(settings, "LIVIA_RAG_OPERATIONS_ENABLED", False))
    dry_run = bool(getattr(settings, "LIVIA_RAG_OPERATIONS_DRY_RUN", True))
    if not enabled:
        return OperationsGateStatus(enabled=False, dry_run=dry_run, reason="Operações RAG desabilitadas globalmente.")
    return OperationsGateStatus(enabled=True, dry_run=dry_run)


def _lease_seconds() -> int:
    return max(int(getattr(settings, "LIVIA_RAG_OPERATIONS_LEASE_SECONDS", 3600) or 3600), 60)


def _sync_stale_cutoff() -> timezone.datetime:
    timeout = max(int(getattr(settings, "LIVIA_RAG_SYNC_RUNNING_TIMEOUT_SECONDS", 1800) or 1800), 60)
    return timezone.now() - timedelta(seconds=timeout)


def _index_stale_cutoff() -> timezone.datetime:
    timeout = max(int(getattr(settings, "LIVIA_RAG_INDEX_RUNNING_TIMEOUT_SECONDS", 1800) or 1800), 60)
    return timezone.now() - timedelta(seconds=timeout)


def _sanitize_error(exc: Exception, *, fallback: str = "operation_failed") -> tuple[str, str]:
    if isinstance(exc, RagOperationsError):
        return exc.code, str(exc)[:500]
    if isinstance(exc, (TenantRagSyncError, TenantRagIndexingError, EmbeddingConfigurationError)):
        return exc.__class__.__name__.lower(), str(exc)[:500]
    message = sanitize_external_error_message(str(exc))[:500]
    return fallback, message or fallback


def _configuration_for_tenant(tenant: Tenant) -> TenantRagConfiguration:
    configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    if configuration is None:
        raise RagOperationsError(
            "Configuração RAG ausente para este tenant.",
            code="configuration_missing",
        )
    return configuration


def _assert_no_active_operation(*, tenant: Tenant, exclude_id: int | None = None) -> None:
    qs = TenantRagOperationRequest.objects.filter(
        tenant=tenant,
        status__in=[
            TenantRagOperationRequest.Status.PENDING,
            TenantRagOperationRequest.Status.RUNNING,
        ],
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise RagOperationsError(
            "Já existe uma solicitação operacional pendente ou em execução para este tenant.",
            code="duplicate_operation",
        )


def _assert_no_pipeline_lock(*, configuration: TenantRagConfiguration) -> None:
    now = timezone.now()
    if (
        configuration.last_inventory_status == TenantRagConfiguration.InventoryStatus.RUNNING
        and configuration.last_inventory_started_at
        and configuration.last_inventory_started_at > _sync_stale_cutoff()
    ):
        raise RagOperationsError(
            "Já existe uma sincronização em andamento para este tenant.",
            code="sync_lock_active",
        )
    if (
        configuration.last_index_status == TenantRagConfiguration.InventoryStatus.RUNNING
        and configuration.last_index_started_at
        and configuration.last_index_started_at > _index_stale_cutoff()
    ):
        raise RagOperationsError(
            "Já existe uma indexação em andamento para este tenant.",
            code="index_lock_active",
        )


def recover_stale_operation_requests(*, tenant: Tenant | None = None) -> int:
    now = timezone.now()
    qs = TenantRagOperationRequest.objects.filter(
        status=TenantRagOperationRequest.Status.RUNNING,
        lease_expires_at__lt=now,
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    recovered = 0
    for request in qs.iterator():
        request.status = TenantRagOperationRequest.Status.FAILED
        request.error_code = "stale_execution"
        request.error_message = "Execução expirou sem conclusão. Solicite novamente após revisar o histórico."
        request.finished_at = now
        request.save(
            update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"]
        )
        record_audit_event(
            action=ACTION_TENANT_RAG_OPERATION_FAILED,
            tenant=request.tenant,
            object_type="knowledge_base.tenantragoperationrequest",
            object_id=str(request.pk),
            object_repr=str(request),
            metadata={
                "source": "rag.operations.recover_stale",
                "run_id": request.run_id,
                "operation": request.operation,
                "error_code": request.error_code,
                "dry_run": request.dry_run,
            },
        )
        recovered += 1
    return recovered


def _simulate_sync_preview(*, tenant: Tenant, operation: str) -> dict[str, int]:
    manifests = TenantRagDriveFileManifest.objects.filter(tenant=tenant, is_active=True).count()
    staging = TenantRagDriveTextStaging.objects.filter(tenant=tenant, manifest__is_active=True).count()
    chunks = TenantRagDocumentChunk.objects.filter(
        tenant=tenant,
        is_active=True,
        status=TenantRagDocumentChunk.Status.ACTIVE,
    ).count()
    if operation == TenantRagOperationRequest.Operation.INVENTORY:
        return {
            "discovered": manifests,
            "exported": 0,
            "updated": 0,
            "unchanged": manifests,
            "removed": 0,
            "skipped": 0,
            "failed": 0,
            "documents": manifests,
        }
    if operation == TenantRagOperationRequest.Operation.SYNC_EXPORT:
        return {
            "discovered": manifests,
            "exported": 0,
            "updated": 0,
            "unchanged": manifests,
            "removed": 0,
            "skipped": 0,
            "failed": 0,
            "documents": manifests,
        }
    return {
        "discovered": staging,
        "exported": 0,
        "updated": 0,
        "unchanged": chunks,
        "removed": 0,
        "skipped": 0,
        "failed": 0,
        "documents": staging,
        "chunks": chunks,
    }


def create_operation_request(
    *,
    tenant: Tenant,
    operation: str,
    requested_by=None,
    source: str = "operations_portal",
) -> TenantRagOperationRequest:
    gate = operations_gate_status()
    if not gate.enabled:
        record_audit_event(
            action=ACTION_TENANT_RAG_OPERATION_REJECTED,
            actor=requested_by,
            tenant=tenant,
            metadata={"source": source, "operation": operation, "reason": gate.reason, "code": "global_disabled"},
        )
        raise RagOperationsError(gate.reason, code="global_disabled")

    allowed_operations = {choice.value for choice in TenantRagOperationRequest.Operation}
    if operation not in allowed_operations:
        raise RagOperationsError("Operação inválida.", code="invalid_operation")

    configuration = _configuration_for_tenant(tenant)
    if operation in {
        TenantRagOperationRequest.Operation.INVENTORY,
        TenantRagOperationRequest.Operation.SYNC_EXPORT,
        TenantRagOperationRequest.Operation.BUILD_CHUNKS,
        TenantRagOperationRequest.Operation.FULL_REINDEX,
    } and not configuration.sync_enabled:
        raise RagOperationsError(
            "Sincronização deste tenant está desabilitada.",
            code="sync_disabled",
        )

    with transaction.atomic():
        locked_config = TenantRagConfiguration.objects.select_for_update().get(pk=configuration.pk)
        _assert_no_active_operation(tenant=tenant)
        _assert_no_pipeline_lock(configuration=locked_config)
        run_id = str(uuid.uuid4())
        request = TenantRagOperationRequest.objects.create(
            tenant=tenant,
            requested_by=requested_by,
            operation=operation,
            status=TenantRagOperationRequest.Status.PENDING,
            dry_run=gate.dry_run,
            run_id=run_id,
            counters={},
        )

    record_audit_event(
        action=ACTION_TENANT_RAG_OPERATION_REQUESTED,
        actor=requested_by,
        tenant=tenant,
        obj=request,
        after_data={
            "operation": operation,
            "dry_run": request.dry_run,
            "run_id": run_id,
        },
        metadata={"source": source},
    )
    return request


def _finalize_request(
    *,
    request: TenantRagOperationRequest,
    status: str,
    counters: dict | None = None,
    error_code: str = "",
    error_message: str = "",
    index_run: TenantRagIndexRun | None = None,
) -> TenantRagOperationRequest:
    request.status = status
    request.counters = counters or {}
    request.error_code = error_code
    request.error_message = error_message[:500] if error_message else ""
    request.finished_at = timezone.now()
    if index_run is not None:
        request.index_run = index_run
    request.save(
        update_fields=[
            "status",
            "counters",
            "error_code",
            "error_message",
            "finished_at",
            "index_run",
            "updated_at",
        ]
    )
    return request


def _execute_sync_operation(
    *,
    tenant: Tenant,
    operation: str,
    dry_run: bool,
    configuration: TenantRagConfiguration | None = None,
) -> tuple[str, dict]:
    if dry_run:
        counters = _simulate_sync_preview(tenant=tenant, operation=operation)
        return TenantRagOperationRequest.Status.SUCCEEDED, counters

    mode = SYNC_OPERATION_MODES[operation]
    configuration = acquire_tenant_sync_lock(tenant=tenant, mode=mode)
    if operation == TenantRagOperationRequest.Operation.BUILD_CHUNKS:
        outcome = run_chunk_build_for_tenant(configuration=configuration)
    else:
        service = build_google_drive_readonly_service()
        inventory = GoogleDriveInventoryService(service).inventory_approved_folder(configuration.approved_folder_id)
        outcome = run_sync_for_inventory(
            configuration=configuration,
            mode=mode,
            inventory_summary=inventory,
            drive_service=GoogleDriveInventoryService(service),
        )
    counters = outcome.counters.as_dict()
    counters["documents"] = outcome.file_count
    status = (
        TenantRagOperationRequest.Status.PARTIAL
        if outcome.status == TenantRagConfiguration.InventoryStatus.PARTIAL
        else TenantRagOperationRequest.Status.SUCCEEDED
    )
    return status, counters


def _execute_index_operation(*, request: TenantRagOperationRequest, only_stale: bool = False) -> tuple[str, dict, TenantRagIndexRun | None]:
    config = load_embedding_config()
    if not request.dry_run and not config.indexing_enabled:
        raise RagOperationsError(
            "Indexação real desabilitada. Habilite LIVIA_RAG_INDEXING_ENABLED apenas após autorização explícita.",
            code="indexing_disabled",
        )

    run_id = request.run_id
    mode = "dry_run" if request.dry_run else "index"
    configuration = acquire_tenant_index_lock(tenant=request.tenant, mode=mode, run_id=run_id)
    record_audit_event(
        action=ACTION_TENANT_RAG_INDEX_STARTED,
        tenant=request.tenant,
        object_type="knowledge_base.tenantragconfiguration",
        object_id=str(configuration.pk),
        object_repr=f"{request.tenant.slug} / index",
        metadata={
            "source": "rag.operations.execute",
            "run_id": run_id,
            "mode": mode,
            "dry_run": request.dry_run,
            "operation": request.operation,
        },
    )
    outcome = run_index_for_tenant(
        configuration=configuration,
        dry_run=request.dry_run,
        only_stale=only_stale,
        config=config,
        run_id=run_id,
    )
    counters = outcome.counters.as_dict()
    index_run = TenantRagIndexRun.objects.filter(tenant=request.tenant, run_id=run_id).first()
    if outcome.status == TenantRagIndexRun.Status.PARTIAL:
        status = TenantRagOperationRequest.Status.PARTIAL
    elif outcome.status == TenantRagIndexRun.Status.FAILED:
        status = TenantRagOperationRequest.Status.FAILED
    else:
        status = TenantRagOperationRequest.Status.SUCCEEDED
    record_audit_event(
        action=ACTION_TENANT_RAG_INDEX_COMPLETED if status != TenantRagOperationRequest.Status.FAILED else ACTION_TENANT_RAG_INDEX_FAILED,
        tenant=request.tenant,
        object_type="knowledge_base.tenantragconfiguration",
        object_id=str(configuration.pk),
        object_repr=f"{request.tenant.slug} / index",
        metadata={
            "source": "rag.operations.execute",
            "run_id": run_id,
            "mode": mode,
            "dry_run": request.dry_run,
            "status": outcome.status,
            **counters,
        },
    )
    return status, counters, index_run


def execute_operation_request(*, request_id: int) -> TenantRagOperationRequest:
    recover_stale_operation_requests()
    with transaction.atomic():
        request = (
            TenantRagOperationRequest.objects.select_for_update()
            .select_related("tenant")
            .filter(pk=request_id)
            .first()
        )
        if request is None:
            raise RagOperationsError("Solicitação não encontrada.", code="not_found")
        if request.status == TenantRagOperationRequest.Status.SUCCEEDED:
            return request
        if request.status in {
            TenantRagOperationRequest.Status.FAILED,
            TenantRagOperationRequest.Status.PARTIAL,
            TenantRagOperationRequest.Status.CANCELLED,
        }:
            return request
        if request.status == TenantRagOperationRequest.Status.RUNNING:
            raise RagOperationsError("Solicitação já está em execução.", code="already_running")
        if request.status != TenantRagOperationRequest.Status.PENDING:
            return request
        request.status = TenantRagOperationRequest.Status.RUNNING
        request.started_at = timezone.now()
        request.lease_expires_at = request.started_at + timedelta(seconds=_lease_seconds())
        request.save(update_fields=["status", "started_at", "lease_expires_at", "updated_at"])

    record_audit_event(
        action=ACTION_TENANT_RAG_OPERATION_STARTED,
        actor=request.requested_by,
        tenant=request.tenant,
        obj=request,
        metadata={
            "source": "rag.operations.execute",
            "run_id": request.run_id,
            "operation": request.operation,
            "dry_run": request.dry_run,
        },
    )

    configuration = _configuration_for_tenant(request.tenant)
    try:
        if request.operation in SYNC_OPERATION_MODES:
            status, counters = _execute_sync_operation(
                tenant=request.tenant,
                operation=request.operation,
                dry_run=request.dry_run,
            )
            index_run = None
        elif request.operation == TenantRagOperationRequest.Operation.INDEX_EMBEDDINGS:
            status, counters, index_run = _execute_index_operation(request=request, only_stale=False)
        elif request.operation == TenantRagOperationRequest.Operation.FULL_REINDEX:
            build_status, build_counters = _execute_sync_operation(
                tenant=request.tenant,
                operation=TenantRagOperationRequest.Operation.BUILD_CHUNKS,
                dry_run=request.dry_run,
            )
            index_status, index_counters, index_run = _execute_index_operation(request=request, only_stale=False)
            counters = {**build_counters, **index_counters}
            status = index_status
            if build_status == TenantRagOperationRequest.Status.PARTIAL or index_status == TenantRagOperationRequest.Status.PARTIAL:
                status = TenantRagOperationRequest.Status.PARTIAL
            elif build_status == TenantRagOperationRequest.Status.FAILED or index_status == TenantRagOperationRequest.Status.FAILED:
                status = TenantRagOperationRequest.Status.FAILED
        else:
            raise RagOperationsError("Operação não suportada.", code="invalid_operation")

        request = _finalize_request(
            request=TenantRagOperationRequest.objects.get(pk=request.pk),
            status=status,
            counters=counters,
            index_run=index_run,
        )
        record_audit_event(
            action=ACTION_TENANT_RAG_OPERATION_COMPLETED,
            actor=request.requested_by,
            tenant=request.tenant,
            obj=request,
            after_data={"status": status, "counters": counters, "dry_run": request.dry_run},
            metadata={"source": "rag.operations.execute", "run_id": request.run_id, "operation": request.operation},
        )
        return request
    except Exception as exc:  # noqa: BLE001
        error_code, error_message = _sanitize_error(exc)
        if isinstance(exc, (TenantRagSyncError, GoogleDriveConfigurationError, GoogleDriveAuthenticationError, GoogleDrivePermissionError)):
            mark_configuration_failed(configuration, error_message=error_message)
        if isinstance(exc, TenantRagIndexingError):
            mark_index_failed(configuration=configuration, run=None, error=error_message)
        request = _finalize_request(
            request=TenantRagOperationRequest.objects.get(pk=request_id),
            status=TenantRagOperationRequest.Status.FAILED,
            error_code=error_code,
            error_message=error_message,
        )
        record_audit_event(
            action=ACTION_TENANT_RAG_OPERATION_FAILED,
            actor=request.requested_by,
            tenant=request.tenant,
            obj=request,
            metadata={
                "source": "rag.operations.execute",
                "run_id": request.run_id,
                "operation": request.operation,
                "error_code": error_code,
                "dry_run": request.dry_run,
            },
        )
        return request


def process_pending_operation_requests(*, tenant: Tenant | None = None, limit: int = 5) -> list[int]:
    recover_stale_operation_requests(tenant=tenant)
    qs = TenantRagOperationRequest.objects.filter(status=TenantRagOperationRequest.Status.PENDING).order_by("created_at", "id")
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    processed: list[int] = []
    for request in qs[:limit]:
        try:
            execute_operation_request(request_id=request.pk)
            processed.append(request.pk)
        except RagOperationsError as exc:
            if exc.code != "already_running":
                logger.warning("rag operation skipped request_id=%s code=%s", request.pk, exc.code)
    return processed
