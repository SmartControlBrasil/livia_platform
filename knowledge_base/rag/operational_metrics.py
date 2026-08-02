from __future__ import annotations

import statistics
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Avg, Count, Max, Sum
from django.utils import timezone

from assistant_core.models import AiUsageEvent
from assistant_core.services.ai_feature_gates import (
    is_grounded_synthesis_allowed,
    is_rag_semantic_context_active,
)
from knowledge_base.models import RagRetrievalEvent, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.embedding_profile import (
    embedding_coverage_breakdown,
    inspect_tenant_embedding_health,
)
from knowledge_base.rag.operations import operations_gate_status
from knowledge_base.rag.readiness import inspect_rag_vector_readiness
from knowledge_base.rag.vector_search import get_vector_search_backend
from tenants.models import AssistantProfile


ALLOWED_PERIODS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
DEFAULT_PERIOD = "7d"


def parse_health_period(raw: str | None) -> str:
    period = str(raw or DEFAULT_PERIOD).strip().lower()
    if period not in ALLOWED_PERIODS:
        return DEFAULT_PERIOD
    return period


def period_window(*, period: str) -> tuple[str, timezone.datetime]:
    normalized = parse_health_period(period)
    return normalized, timezone.now() - ALLOWED_PERIODS[normalized]


def pending_migration_count() -> int:
    executor = MigrationExecutor(connection)
    return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def build_ai_usage_summary(*, tenant, period: str) -> dict:
    _, since = period_window(period=period)
    qs = AiUsageEvent.objects.filter(tenant=tenant, created_at__gte=since)
    if not qs.exists():
        return {
            "has_data": False,
            "period": period,
            "requests": 0,
            "success": 0,
            "failure": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "avg_latency_ms": 0.0,
            "median_latency_ms": 0,
            "p95_latency_ms": 0,
            "by_operation": [],
            "errors": [],
            "estimated_cost_usd": None,
        }

    latencies = [int(value or 0) for value in qs.values_list("latency_ms", flat=True)]
    totals = qs.aggregate(
        requests=Count("id"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        total_tokens=Sum("total_tokens"),
        avg_latency=Avg("latency_ms"),
    )
    by_operation = []
    for operation in sorted(set(qs.values_list("operation", flat=True))):
        op_qs = qs.filter(operation=operation)
        op_latencies = [int(value or 0) for value in op_qs.values_list("latency_ms", flat=True)]
        op_totals = op_qs.aggregate(
            requests=Count("id"),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            total_tokens=Sum("total_tokens"),
        )
        by_operation.append(
            {
                "operation": operation,
                "requests": op_totals["requests"],
                "success": op_qs.filter(success=True).count(),
                "failure": op_qs.filter(success=False).count(),
                "prompt_tokens": int(op_totals["prompt_tokens"] or 0),
                "completion_tokens": int(op_totals["completion_tokens"] or 0),
                "total_tokens": int(op_totals["total_tokens"] or 0),
                "median_latency_ms": int(statistics.median(op_latencies)) if op_latencies else 0,
                "p95_latency_ms": _percentile(op_latencies, 95),
            }
        )

    errors = []
    error_rows = (
        qs.filter(success=False)
        .exclude(error_type="")
        .values("error_type", "operation", "model")
        .annotate(total=Count("id"), last_seen=Max("created_at"))
        .order_by("-total", "-last_seen")[:10]
    )
    for row in error_rows:
        errors.append(
            {
                "category": _sanitize_error_category(row["error_type"]),
                "operation": row["operation"],
                "model": row["model"] or "-",
                "count": row["total"],
                "last_seen": row["last_seen"],
            }
        )

    return {
        "has_data": True,
        "period": period,
        "requests": totals["requests"],
        "success": qs.filter(success=True).count(),
        "failure": qs.filter(success=False).count(),
        "prompt_tokens": int(totals["prompt_tokens"] or 0),
        "completion_tokens": int(totals["completion_tokens"] or 0),
        "total_tokens": int(totals["total_tokens"] or 0),
        "avg_latency_ms": round(float(totals["avg_latency"] or 0), 1),
        "median_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        "p95_latency_ms": _percentile(latencies, 95),
        "by_operation": by_operation,
        "errors": errors,
        "estimated_cost_usd": None,
    }


def build_retrieval_metrics(*, tenant, period: str) -> dict:
    _, since = period_window(period=period)
    events = RagRetrievalEvent.objects.filter(tenant=tenant, created_at__gte=since)
    executed_qs = events.exclude(status=RagRetrievalEvent.Status.SKIPPED)
    executed = executed_qs.count()
    if executed == 0:
        return {
            "has_data": False,
            "period": period,
            "executed": 0,
            "hits": 0,
            "empty": 0,
            "failed": 0,
            "skipped": events.filter(status=RagRetrievalEvent.Status.SKIPPED).count(),
            "dry_run_events": 0,
            "active_events": 0,
            "hit_rate": None,
            "grounded_success": 0,
            "avg_latency_ms": 0.0,
            "avg_max_score": 0.0,
        }

    hits = executed_qs.filter(hit=True).count()
    aggregates = executed_qs.aggregate(
        avg_latency=Avg("duration_ms"),
        avg_max_score=Avg("max_score"),
    )
    grounded_success = AiUsageEvent.objects.filter(
        tenant=tenant,
        created_at__gte=since,
        operation=AiUsageEvent.Operation.GROUNDED_SYNTHESIS,
        success=True,
    ).count()

    return {
        "has_data": True,
        "period": period,
        "executed": executed,
        "hits": hits,
        "empty": executed_qs.filter(status=RagRetrievalEvent.Status.EMPTY).count(),
        "failed": executed_qs.filter(status=RagRetrievalEvent.Status.FAILED).count(),
        "skipped": events.filter(status=RagRetrievalEvent.Status.SKIPPED).count(),
        "dry_run_events": executed_qs.filter(dry_run=True).count(),
        "active_events": executed_qs.filter(dry_run=False).count(),
        "hit_rate": round(hits / executed, 4) if executed else None,
        "grounded_success": grounded_success,
        "avg_latency_ms": round(float(aggregates["avg_latency"] or 0), 1),
        "avg_max_score": round(float(aggregates["avg_max_score"] or 0), 3),
    }


def build_vector_health_summary(*, tenant) -> dict:
    health = inspect_tenant_embedding_health(tenant=tenant)
    coverage = embedding_coverage_breakdown(tenant=tenant, profile=health.profile)
    return {
        "status_label": health.status_label,
        "profile": {
            "provider": health.profile.provider,
            "model": health.profile.model,
            "dimension": health.profile.dimension,
        },
        "total": health.total,
        "compatible": health.compatible,
        "incompatible": health.incompatible,
        "null_vectors": health.null_vectors,
        "stale": health.stale,
        "reindex_required": health.reindex_required,
        "invalid": health.invalid,
        "coverage": coverage,
    }


def build_rag_configuration_snapshot(*, tenant, configuration: TenantRagConfiguration | None) -> dict:
    profile = AssistantProfile.objects.filter(tenant=tenant, is_active=True).first()
    ops_gate = operations_gate_status()
    embedding_provider = "-"
    embedding_model = "-"
    embedding_dimension = None
    embedding_error = ""
    try:
        cfg = load_embedding_config()
        embedding_provider = cfg.provider
        embedding_model = cfg.model
        embedding_dimension = cfg.dimension
    except EmbeddingConfigurationError as exc:
        embedding_error = str(exc)[:200]

    try:
        backend_name = get_vector_search_backend().name
    except Exception as exc:  # noqa: BLE001
        backend_name = f"unavailable:{exc.__class__.__name__}"

    return {
        "configuration_present": configuration is not None,
        "retrieval_enabled_db": bool(configuration and configuration.retrieval_enabled),
        "operational_monitoring_enabled": bool(
            configuration and configuration.operational_monitoring_enabled
        ),
        "sync_enabled": bool(configuration and configuration.sync_enabled),
        "approved_folder_configured": bool(configuration and configuration.approved_folder_id),
        "rag_semantic_active": is_rag_semantic_context_active(tenant_slug=tenant.slug),
        "grounded_synthesis_allowed": bool(
            profile and is_grounded_synthesis_allowed(tenant_slug=tenant.slug, assistant_profile=profile)
        ),
        "global_rag_enabled": bool(getattr(settings, "LIVIA_RAG_ENABLED", False)),
        "global_rag_dry_run": bool(getattr(settings, "LIVIA_RAG_DRY_RUN", True)),
        "operations_enabled": ops_gate.enabled,
        "operations_dry_run": ops_gate.dry_run,
        "indexing_real_enabled": bool(getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False)),
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "embedding_error": embedding_error,
        "vector_backend": backend_name,
        "last_inventory_at": getattr(configuration, "last_inventory_at", None),
        "last_inventory_status": getattr(configuration, "last_inventory_status", ""),
        "last_index_at": getattr(configuration, "last_index_at", None),
        "last_index_status": getattr(configuration, "last_index_status", ""),
    }


def build_operations_summary(*, tenant) -> dict:
    now = timezone.now()
    base = TenantRagOperationRequest.objects.filter(tenant=tenant)
    stale = base.filter(
        status=TenantRagOperationRequest.Status.RUNNING,
        lease_expires_at__lt=now,
    ).count()
    return {
        "pending": base.filter(status=TenantRagOperationRequest.Status.PENDING).count(),
        "running": base.filter(status=TenantRagOperationRequest.Status.RUNNING).count(),
        "succeeded": base.filter(status=TenantRagOperationRequest.Status.SUCCEEDED).count(),
        "failed": base.filter(status=TenantRagOperationRequest.Status.FAILED).count(),
        "partial": base.filter(status=TenantRagOperationRequest.Status.PARTIAL).count(),
        "cancelled": base.filter(status=TenantRagOperationRequest.Status.CANCELLED).count(),
        "stale_running": stale,
    }


def build_rag_operational_report_payload(*, tenant, days: int) -> dict:
    """Payload compatível com rag_operational_report (janela em dias)."""
    period = "7d" if days >= 7 else "24h" if days <= 1 else "30d"
    profile = AssistantProfile.objects.filter(tenant=tenant, is_active=True).first()
    configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    retrieval = build_retrieval_metrics(tenant=tenant, period=period)
    readiness = [
        {"ok": check.ok, "code": check.code, "detail": check.detail}
        for check in inspect_rag_vector_readiness()
    ]
    embedding_profile = None
    embedding_error = ""
    try:
        cfg = load_embedding_config()
        embedding_profile = {
            "provider": cfg.provider,
            "model": cfg.model,
            "dimension": cfg.dimension,
            "signature": cfg.signature[:16] + "…",
        }
    except EmbeddingConfigurationError as exc:
        embedding_error = str(exc)

    return {
        "tenant": tenant.slug,
        "window_days": days,
        "retrieval_enabled": bool(configuration and configuration.retrieval_enabled),
        "tenant_gates": {
            "rag_semantic_active": is_rag_semantic_context_active(tenant_slug=tenant.slug),
            "grounded_synthesis_allowed": bool(
                profile
                and is_grounded_synthesis_allowed(tenant_slug=tenant.slug, assistant_profile=profile)
            ),
        },
        "embedding_profile": embedding_profile,
        "embedding_error": embedding_error,
        "retrieval_metrics": retrieval,
        "readiness": readiness,
    }


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _sanitize_error_category(error_type: str) -> str:
    normalized = str(error_type or "unknown").strip().lower()
    if "timeout" in normalized:
        return "timeout"
    if "rate" in normalized and "limit" in normalized:
        return "rate_limit"
    if "config" in normalized:
        return "configuration_error"
    if "empty" in normalized:
        return "empty_response"
    if "invalid" in normalized:
        return "invalid_response"
    if normalized in {"providererror", "provider_error", "apierror", "api_error"}:
        return "provider_error"
    return normalized[:80] or "provider_error"
