from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from config.environment_safety import inspect_environment_safety
from knowledge_base.models import TenantRagOperationRequest
from knowledge_base.rag.alert_thresholds import (
    ai_failure_min_count,
    operation_failed_window_days,
    retrieval_empty_rate_threshold,
    retrieval_min_executed,
    token_usage_warning_threshold,
)
from knowledge_base.rag.operational_alert_runbooks import get_runbook


@dataclass(frozen=True)
class AlertCandidate:
    rule_id: str
    fingerprint: str
    category: str
    severity: str
    title: str
    summary: str
    source_reference: str = ""
    metadata: dict = field(default_factory=dict)


def build_fingerprint(*, rule_id: str, source_reference: str = "") -> str:
    base = str(rule_id).strip()
    ref = str(source_reference or "").strip()
    if ref:
        return f"{base}:{ref}"
    return base


def evaluate_alert_candidates(*, tenant, snapshot: dict) -> list[AlertCandidate]:
    candidates: list[AlertCandidate] = []
    now = timezone.now()
    period = snapshot.get("period", "7d")

    readiness = snapshot.get("readiness", {})
    configuration = snapshot.get("configuration", {})
    vector_health = snapshot.get("vector_health", {})
    operations_summary = snapshot.get("operations_summary", {})
    retrieval_metrics = snapshot.get("retrieval_metrics", {})
    ai_usage = snapshot.get("ai_usage", {})

    _add_environment_candidates(tenant=tenant, candidates=candidates)
    _add_database_candidate(readiness=readiness, candidates=candidates)
    _add_vector_candidate(vector_health=vector_health, candidates=candidates)
    _add_operation_candidates(tenant=tenant, now=now, candidates=candidates)
    _add_retrieval_candidate(retrieval_metrics=retrieval_metrics, period=period, candidates=candidates)
    _add_ai_candidates(ai_usage=ai_usage, period=period, candidates=candidates)

    return candidates


def _candidate_from_runbook(
    *,
    rule_id: str,
    summary: str,
    severity: str | None = None,
    source_reference: str = "",
    metadata: dict | None = None,
) -> AlertCandidate:
    runbook = get_runbook(rule_id)
    if runbook is None:
        raise ValueError(f"Missing runbook for rule_id={rule_id}")
    return AlertCandidate(
        rule_id=rule_id,
        fingerprint=build_fingerprint(rule_id=rule_id, source_reference=source_reference),
        category=runbook.category,
        severity=severity or runbook.default_severity,
        title=runbook.title,
        summary=summary[:500],
        source_reference=source_reference,
        metadata=metadata or {},
    )


def _add_environment_candidates(*, tenant, candidates: list[AlertCandidate]) -> None:
    checks = inspect_environment_safety(tenant_slug=tenant.slug)
    integration_codes = {
        "smart360_dry_run",
        "smart360_real_dispatch",
        "webhooks_dry_run",
        "handoff_notifications_dry_run",
        "debug_disabled",
        "fake_embeddings_flag",
    }
    provider_codes = {"embedding_provider_fake"}

    skip_codes = {
        "environment_name",
        "embedding_provider",
        "running_tests",
        "smart360_dry_run",
        "tenant_rag_gate",
        "tenant_grounded_gate",
        "tenant_missing",
    }

    for check in checks:
        if check.ok or check.level == "info" or check.code in skip_codes:
            continue
        if check.code in provider_codes:
            candidates.append(
                _candidate_from_runbook(
                    rule_id="provider_forbidden",
                    source_reference=check.code,
                    summary=check.detail,
                    severity="critical",
                    metadata={"check_code": check.code},
                )
            )
        elif check.code in integration_codes:
            candidates.append(
                _candidate_from_runbook(
                    rule_id="integration_safety",
                    source_reference=check.code,
                    summary=check.detail,
                    severity="critical" if check.level == "critical" else "warning",
                    metadata={"check_code": check.code},
                )
            )
        elif check.level == "critical":
            candidates.append(
                _candidate_from_runbook(
                    rule_id="environment_not_ready",
                    source_reference=check.code,
                    summary=check.detail,
                    severity="critical",
                    metadata={"check_code": check.code},
                )
            )


def _add_database_candidate(*, readiness: dict, candidates: list[AlertCandidate]) -> None:
    database = readiness.get("database", {})
    if database.get("status") != "NOT_READY":
        return
    pending = int(database.get("pending_migrations") or 0)
    candidates.append(
        _candidate_from_runbook(
            rule_id="database_not_ready",
            summary=f"Migrations pendentes: {pending}.",
            severity="critical",
            metadata={"pending_migrations": pending},
        )
    )


def _add_vector_candidate(*, vector_health: dict, candidates: list[AlertCandidate]) -> None:
    status_label = vector_health.get("status_label")
    reindex_required = int(vector_health.get("reindex_required") or 0)
    if status_label != "REINDEX_REQUIRED" and reindex_required <= 0:
        return
    severity = "critical" if reindex_required > 0 else "warning"
    candidates.append(
        _candidate_from_runbook(
            rule_id="vector_incompatible",
            summary=(
                f"Vector health={status_label}; "
                f"reindex_required={reindex_required}; "
                f"incompatíveis={vector_health.get('incompatible', 0)}."
            ),
            severity=severity,
            metadata={
                "status_label": status_label,
                "reindex_required": reindex_required,
                "compatible": vector_health.get("compatible", 0),
                "incompatible": vector_health.get("incompatible", 0),
            },
        )
    )


def _add_operation_candidates(*, tenant, now, candidates: list[AlertCandidate]) -> None:
    stale_qs = TenantRagOperationRequest.objects.filter(
        tenant=tenant,
        status=TenantRagOperationRequest.Status.RUNNING,
        lease_expires_at__lt=now,
    )
    for operation in stale_qs.only("id", "operation", "run_id", "attempt_count"):
        candidates.append(
            _candidate_from_runbook(
                rule_id="rag_operation_stale",
                source_reference=str(operation.pk),
                summary=(
                    f"Operação {operation.get_operation_display()} ({operation.run_id}) "
                    f"com lease expirado (tentativas={operation.attempt_count})."
                ),
                severity="critical",
                metadata={"operation_id": operation.pk, "run_id": operation.run_id},
            )
        )

    since = now - timedelta(days=operation_failed_window_days())
    failed_qs = TenantRagOperationRequest.objects.filter(
        tenant=tenant,
        status=TenantRagOperationRequest.Status.FAILED,
        finished_at__gte=since,
    )
    for operation in failed_qs.only("id", "operation", "run_id", "error_code", "finished_at"):
        candidates.append(
            _candidate_from_runbook(
                rule_id="rag_operation_failed",
                source_reference=str(operation.pk),
                summary=(
                    f"Operação {operation.get_operation_display()} falhou "
                    f"(código={operation.error_code or 'unknown'})."
                ),
                severity="warning",
                metadata={
                    "operation_id": operation.pk,
                    "error_code": (operation.error_code or "")[:80],
                    "run_id": operation.run_id,
                },
            )
        )


def _add_retrieval_candidate(*, retrieval_metrics: dict, period: str, candidates: list[AlertCandidate]) -> None:
    if not retrieval_metrics.get("has_data"):
        return
    executed = int(retrieval_metrics.get("executed") or 0)
    if executed < retrieval_min_executed():
        return
    empty = int(retrieval_metrics.get("empty") or 0)
    empty_rate = empty / executed if executed else 0.0
    if empty_rate < retrieval_empty_rate_threshold():
        return
    candidates.append(
        _candidate_from_runbook(
            rule_id="retrieval_empty_elevated",
            source_reference=period,
            summary=(
                f"Empty rate {empty_rate:.0%} em {executed} retrievals "
                f"(limiar {retrieval_empty_rate_threshold():.0%})."
            ),
            severity="warning",
            metadata={
                "period": period,
                "executed": executed,
                "empty": empty,
                "empty_rate": round(empty_rate, 4),
            },
        )
    )


def _add_ai_candidates(*, ai_usage: dict, period: str, candidates: list[AlertCandidate]) -> None:
    if not ai_usage.get("has_data"):
        return
    failures = int(ai_usage.get("failure") or 0)
    if failures >= ai_failure_min_count():
        candidates.append(
            _candidate_from_runbook(
                rule_id="openai_failures",
                source_reference=period,
                summary=(
                    f"{failures} falhas de IA no período "
                    f"(mínimo {ai_failure_min_count()})."
                ),
                severity="warning",
                metadata={"period": period, "failures": failures, "requests": ai_usage.get("requests", 0)},
            )
        )

    total_tokens = int(ai_usage.get("total_tokens") or 0)
    threshold = token_usage_warning_threshold()
    if total_tokens >= threshold:
        candidates.append(
            _candidate_from_runbook(
                rule_id="token_usage_elevated",
                source_reference=period,
                summary=f"{total_tokens} tokens no período (limiar {threshold}).",
                severity="warning",
                metadata={"period": period, "total_tokens": total_tokens, "threshold": threshold},
            )
        )
