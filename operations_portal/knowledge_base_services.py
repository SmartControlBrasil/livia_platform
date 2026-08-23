from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from assistant_core.services.ai_feature_gates import is_rag_semantic_context_active
from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.conversation_retrieval import (
    RagRetrievalResult,
    _apply_tenant_retrieval_timeout,
    _resolve_effective_limits,
    _resolve_effective_threshold,
    retrieve_context,
)
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.embedding_profile import embedding_coverage_breakdown, load_embedding_profile


@dataclass(frozen=True)
class KnowledgeBaseReadiness:
    label: str
    tone: str
    code: str
    detail: str


@dataclass(frozen=True)
class EffectiveRagLimits:
    global_max_chunks: int
    global_max_context_chars: int
    global_min_similarity_score: float
    global_timeout_seconds: int
    effective_max_chunks: int
    effective_max_context_chars: int
    effective_min_similarity_score: float
    effective_timeout_seconds: int
    tenant_max_chunks: int | None
    tenant_max_context_chars: int | None
    tenant_min_similarity_score: float | None
    tenant_timeout_seconds: int | None


@dataclass(frozen=True)
class DiagnosticSearchResult:
    query: str
    retrieval: RagRetrievalResult
    sufficiency_label: str
    sufficiency_tone: str
    fallback_reason: str
    evidence: list[dict]


def sanitize_excerpt(text: str, *, max_len: int = 180) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def compute_effective_rag_limits(*, configuration: TenantRagConfiguration | None) -> EffectiveRagLimits:
    global_threshold = float(getattr(settings, "LIVIA_RAG_MIN_SIMILARITY_SCORE", 0.25) or 0.0)
    global_max_chunks = int(getattr(settings, "LIVIA_RAG_MAX_RETRIEVED_CHUNKS", 5) or 0)
    global_max_chars = int(getattr(settings, "LIVIA_RAG_MAX_CONTEXT_CHARS", 3000) or 0)
    try:
        base_cfg = load_embedding_config()
    except EmbeddingConfigurationError:
        base_cfg = None
    global_timeout = int(getattr(base_cfg, "timeout_seconds", 0) or getattr(settings, "LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS", 30) or 0)

    effective_max_chunks, effective_max_chars = _resolve_effective_limits(
        configuration=configuration,
        global_max_chunks=global_max_chunks,
        global_max_chars=global_max_chars,
    )
    threshold, _ = _resolve_effective_threshold(
        configuration=configuration,
        global_threshold=global_threshold,
        threshold_override=None,
    )
    effective_cfg = _apply_tenant_retrieval_timeout(base_cfg, configuration) if base_cfg is not None else None
    effective_timeout = int(getattr(effective_cfg, "timeout_seconds", global_timeout) or global_timeout)

    return EffectiveRagLimits(
        global_max_chunks=global_max_chunks,
        global_max_context_chars=global_max_chars,
        global_min_similarity_score=global_threshold,
        global_timeout_seconds=global_timeout,
        effective_max_chunks=effective_max_chunks,
        effective_max_context_chars=effective_max_chars,
        effective_min_similarity_score=threshold,
        effective_timeout_seconds=effective_timeout,
        tenant_max_chunks=getattr(configuration, "max_retrieved_chunks", None),
        tenant_max_context_chars=getattr(configuration, "max_context_chars", None),
        tenant_min_similarity_score=getattr(configuration, "min_similarity_score", None),
        tenant_timeout_seconds=getattr(configuration, "retrieval_timeout_seconds", None),
    )


def compute_knowledge_base_readiness(
    *,
    tenant,
    configuration: TenantRagConfiguration | None,
    coverage: dict[str, int | float],
) -> KnowledgeBaseReadiness:
    if configuration is None:
        return KnowledgeBaseReadiness(
            label="Configuração incompleta",
            tone="warning",
            code="configuration_missing",
            detail="Este tenant ainda não possui configuração RAG cadastrada.",
        )

    if configuration.last_index_status == TenantRagConfiguration.InventoryStatus.FAILED:
        return KnowledgeBaseReadiness(
            label="Erro na última indexação",
            tone="danger",
            code="last_index_failed",
            detail=configuration.last_index_error or "A última execução de indexação terminou com falha.",
        )

    global_enabled = bool(getattr(settings, "LIVIA_RAG_ENABLED", False))
    semantic_active = is_rag_semantic_context_active(tenant_slug=tenant.slug)
    if not global_enabled or not configuration.retrieval_enabled or not semantic_active:
        detail_parts = []
        if not global_enabled:
            detail_parts.append("recuperação global desativada")
        if not configuration.retrieval_enabled:
            detail_parts.append("recuperação desativada para o tenant")
        if not semantic_active and bool(getattr(settings, "LIVIA_RAG_DRY_RUN", True)):
            detail_parts.append("dry-run ativo sem injeção no chat")
        return KnowledgeBaseReadiness(
            label="Desativado",
            tone="secondary",
            code="retrieval_disabled",
            detail="; ".join(detail_parts) or "Recuperação semântica indisponível no chat.",
        )

    indexable = int(coverage.get("indexable_chunks") or 0)
    if indexable <= 0:
        return KnowledgeBaseReadiness(
            label="Índice vazio",
            tone="warning",
            code="empty_index",
            detail="Não há chunks ativos indexados para este tenant.",
        )

    missing = int(coverage.get("missing_embedding") or 0)
    incompatible = int(coverage.get("incompatible_embedding") or 0)
    if missing > 0 or incompatible > 0:
        return KnowledgeBaseReadiness(
            label="Embeddings incompletos",
            tone="warning",
            code="embeddings_incomplete",
            detail=(
                f"{missing} chunk(s) sem embedding compatível"
                + (f" e {incompatible} com embedding desatualizado." if incompatible else ".")
            ),
        )

    return KnowledgeBaseReadiness(
        label="Pronto",
        tone="success",
        code="ready",
        detail="Base indexada e elegível para recuperação semântica.",
    )


def build_dashboard_metrics(*, tenant, configuration: TenantRagConfiguration | None) -> dict:
    from django.db.models import Count

    from knowledge_base.models import (
        KnowledgeDocument,
        TenantRagChunkEmbedding,
        TenantRagDocumentChunk,
        TenantRagDriveFileManifest,
    )
    from knowledge_base.services.manual_rag import MANUAL_SOURCE_PREFIX

    documents_qs = TenantRagDriveFileManifest.objects.filter(tenant=tenant)
    documents_total = documents_qs.count()
    documents_active = documents_qs.filter(is_active=True).count()
    manual_documents_qs = KnowledgeDocument.objects.filter(tenant=tenant)
    manual_manifest_qs = documents_qs.filter(drive_file_id__startswith=MANUAL_SOURCE_PREFIX)
    drive_manifest_qs = documents_qs.exclude(drive_file_id__startswith=MANUAL_SOURCE_PREFIX)

    chunks_qs = TenantRagDocumentChunk.objects.filter(tenant=tenant)
    chunks_active = chunks_qs.filter(is_active=True, status=TenantRagDocumentChunk.Status.ACTIVE).count()
    chunks_with_embedding = (
        chunks_qs.filter(
            is_active=True,
            status=TenantRagDocumentChunk.Status.ACTIVE,
            embeddings__is_active=True,
            embeddings__status=TenantRagChunkEmbedding.Status.ACTIVE,
        )
        .distinct()
        .count()
    )
    chunks_without_embedding = max(chunks_active - chunks_with_embedding, 0)

    coverage = embedding_coverage_breakdown(tenant=tenant)
    limits = compute_effective_rag_limits(configuration=configuration)
    readiness = compute_knowledge_base_readiness(tenant=tenant, configuration=configuration, coverage=coverage)
    from knowledge_base.services.lifecycle import KnowledgeLifecycleService

    lifecycle_readiness = KnowledgeLifecycleService().readiness(tenant=tenant)

    profile = None
    profile_error = ""
    try:
        loaded = load_embedding_profile(validate_schema=False)
        profile = {"provider": loaded.provider, "model": loaded.model, "dimension": loaded.dimension}
    except EmbeddingConfigurationError as exc:
        profile_error = str(exc)

    manifest_status_counts = {
        row["status"]: row["total"]
        for row in documents_qs.values("status").annotate(total=Count("id"))
    }

    return {
        "readiness": readiness,
        "lifecycle_readiness": lifecycle_readiness,
        "limits": limits,
        "coverage": coverage,
        "documents_total": documents_total,
        "documents_active": documents_active,
        "manual_documents_total": manual_documents_qs.count(),
        "manual_documents_active": manual_documents_qs.filter(status=KnowledgeDocument.Status.ACTIVE).count(),
        "manual_manifests_total": manual_manifest_qs.count(),
        "manual_manifests_active": manual_manifest_qs.filter(is_active=True).count(),
        "drive_manifests_total": drive_manifest_qs.count(),
        "drive_manifests_active": drive_manifest_qs.filter(is_active=True).count(),
        "drive_new_files": drive_manifest_qs.filter(status=TenantRagDriveFileManifest.Status.DISCOVERED).count(),
        "drive_changed_files": drive_manifest_qs.filter(status=TenantRagDriveFileManifest.Status.UPDATED).count(),
        "drive_removed_files": drive_manifest_qs.filter(status=TenantRagDriveFileManifest.Status.REMOVED).count(),
        "manifest_status_counts": manifest_status_counts,
        "chunks_active": chunks_active,
        "chunks_with_embedding": chunks_with_embedding,
        "chunks_without_embedding": chunks_without_embedding,
        "configuration_present": configuration is not None,
        "retrieval_enabled": bool(configuration and configuration.retrieval_enabled),
        "global_rag_enabled": bool(getattr(settings, "LIVIA_RAG_ENABLED", False)),
        "global_dry_run": bool(getattr(settings, "LIVIA_RAG_DRY_RUN", True)),
        "semantic_active": is_rag_semantic_context_active(tenant_slug=tenant.slug),
        "last_inventory_status": getattr(configuration, "last_inventory_status", "") if configuration else "",
        "last_inventory_at": getattr(configuration, "last_inventory_at", None) if configuration else None,
        "last_index_status": getattr(configuration, "last_index_status", "") if configuration else "",
        "last_index_at": getattr(configuration, "last_index_at", None) if configuration else None,
        "last_index_error": getattr(configuration, "last_index_error", "") if configuration else "",
        "embedding_profile": profile,
        "embedding_profile_error": profile_error,
    }


def classify_retrieval_sufficiency(result: RagRetrievalResult) -> tuple[str, str]:
    if result.status == "completed" and result.chunks:
        return "Suficiente", "success"
    if result.status == "empty":
        return "Insuficiente", "warning"
    if result.status == "skipped" and result.reason in {
        "global_disabled",
        "tenant_retrieval_disabled",
        "configuration_missing",
    }:
        return "Desativado", "secondary"
    if result.status in {"failed", "skipped"}:
        return "Indisponível", "danger"
    return "Parcial", "warning"


def run_diagnostic_search(*, tenant, query: str) -> DiagnosticSearchResult:
    cleaned = " ".join(str(query or "").split())
    try:
        retrieval = retrieve_context(tenant=tenant, query=cleaned, conversation=None)
    except Exception:  # noqa: BLE001 - busca diagnóstica não derruba o painel
        retrieval = RagRetrievalResult(
            chunks=[],
            status="failed",
            reason="provider_or_runtime",
            duration_ms=0,
            threshold=0.0,
            max_chunks=0,
            max_context_chars=0,
            provider="",
            model="",
            max_score=0.0,
        )
    sufficiency_label, sufficiency_tone = classify_retrieval_sufficiency(retrieval)
    fallback_reason = retrieval.reason if retrieval.status != "completed" or not retrieval.chunks else ""
    evidence = [
        {
            "source_name": chunk.source_name or chunk.source_reference or f"Chunk #{chunk.chunk_id}",
            "source_reference": chunk.source_reference,
            "score": f"{chunk.score:.4f}",
            "excerpt": sanitize_excerpt(chunk.text),
            "chunk_id": chunk.chunk_id,
        }
        for chunk in retrieval.chunks
    ]
    return DiagnosticSearchResult(
        query=cleaned,
        retrieval=retrieval,
        sufficiency_label=sufficiency_label,
        sufficiency_tone=sufficiency_tone,
        fallback_reason=fallback_reason,
        evidence=evidence,
    )


def serialize_retrieval_event(event) -> dict:
    min_score = event.threshold if event.status == event.Status.COMPLETED and event.result_count else 0.0
    return {
        "created_at": event.created_at,
        "status": event.status,
        "status_label": event.get_status_display(),
        "reason": event.reason or "-",
        "candidate_count": event.candidate_count,
        "result_count": event.result_count,
        "duration_ms": event.duration_ms,
        "max_score": event.max_score,
        "min_score": min_score,
        "dry_run": event.dry_run,
        "provider": event.provider or "-",
        "model": event.model or "-",
        "sufficiency_label": "Com evidência" if event.hit else ("Sem evidência" if event.status == event.Status.EMPTY else "-"),
    }


def build_operations_dashboard(*, tenant, configuration: TenantRagConfiguration | None) -> dict:
    from knowledge_base.models import TenantRagOperationRequest
    from knowledge_base.rag.operations import operations_gate_status
    from operations_portal.knowledge_base_selectors import serialize_operation_request

    gate = operations_gate_status()
    from django.utils import timezone

    now = timezone.now()
    stale_count = TenantRagOperationRequest.objects.filter(
        tenant=tenant,
        status=TenantRagOperationRequest.Status.RUNNING,
        lease_expires_at__lt=now,
    ).count()
    latest = (
        TenantRagOperationRequest.objects.filter(tenant=tenant)
        .select_related("requested_by")
        .order_by("-created_at", "-id")
        .first()
    )
    active = TenantRagOperationRequest.objects.filter(
        tenant=tenant,
        status__in=[
            TenantRagOperationRequest.Status.PENDING,
            TenantRagOperationRequest.Status.RUNNING,
        ],
    ).first()
    return {
        "gate": gate,
        "configuration": configuration,
        "source_mode": getattr(configuration, "source_mode", "") if configuration else "",
        "sync_enabled": bool(configuration and configuration.sync_enabled),
        "approved_folder_id": getattr(configuration, "approved_folder_id", "") if configuration else "",
        "last_inventory_status": getattr(configuration, "last_inventory_status", "") if configuration else "",
        "last_inventory_at": getattr(configuration, "last_inventory_at", None) if configuration else None,
        "last_inventory_mode": getattr(configuration, "last_inventory_mode", "") if configuration else "",
        "last_inventory_error": getattr(configuration, "last_inventory_error", "") if configuration else "",
        "last_index_status": getattr(configuration, "last_index_status", "") if configuration else "",
        "last_index_at": getattr(configuration, "last_index_at", None) if configuration else None,
        "last_index_mode": getattr(configuration, "last_index_mode", "") if configuration else "",
        "last_index_error": getattr(configuration, "last_index_error", "") if configuration else "",
        "latest_request": serialize_operation_request(latest) if latest else None,
        "active_request": serialize_operation_request(active) if active else None,
        "stale_running_count": stale_count,
        "lease_seconds": int(getattr(settings, "LIVIA_RAG_OPERATIONS_LEASE_SECONDS", 3600) or 3600),
        "max_attempts": int(getattr(settings, "LIVIA_RAG_OPERATIONS_MAX_ATTEMPTS", 3) or 3),
        "simulation_mode": gate.dry_run,
        "real_execution_allowed": gate.enabled and not gate.dry_run,
        "worker_command": "python manage.py process_tenant_rag_operations",
        "status_command": "python manage.py tenant_rag_operations_status",
        "readiness_command": "python manage.py tenant_rag_operations_readiness",
    }
