from __future__ import annotations

from dataclasses import dataclass

from config.environment_safety import inspect_environment_safety, summarize_environment_readiness
from knowledge_base.rag.operations_readiness import inspect_rag_operations_readiness, readiness_has_blocking_errors
from knowledge_base.rag.operational_metrics import pending_migration_count
from knowledge_base.rag.readiness import inspect_rag_vector_readiness


@dataclass(frozen=True)
class HealthRecommendation:
    code: str
    severity: str
    message: str
    action: str


@dataclass(frozen=True)
class OverallHealth:
    label: str
    tone: str
    readiness_code: str
    detail: str


def build_consolidated_readiness(*, tenant) -> dict:
    env_checks = inspect_environment_safety(tenant_slug=tenant.slug)
    env_status = summarize_environment_readiness(env_checks)
    ops_checks = inspect_rag_operations_readiness(tenant=tenant)
    ops_blocking = readiness_has_blocking_errors(ops_checks)
    vector_checks = inspect_rag_vector_readiness()
    vector_failures = [check for check in vector_checks if not check.ok]
    pending_migrations = pending_migration_count()

    database_status = "READY"
    if pending_migrations > 0:
        database_status = "NOT_READY"

    vector_status = "READY"
    if vector_failures:
        vector_status = "READY_WITH_WARNINGS" if len(vector_failures) <= 2 else "NOT_READY"

    ops_status = "NOT_READY" if ops_blocking else "READY"
    if not ops_blocking and any(not check.ok for check in ops_checks):
        ops_status = "READY_WITH_WARNINGS"

    sections = {
        "environment": {
            "status": env_status,
            "checks": [{"ok": c.ok, "code": c.code, "detail": c.detail, "level": c.level} for c in env_checks],
        },
        "database": {
            "status": database_status,
            "pending_migrations": pending_migrations,
        },
        "rag_operations": {
            "status": ops_status,
            "checks": [
                {"ok": c.ok, "code": c.code, "detail": c.detail, "severity": c.severity}
                for c in ops_checks
            ],
        },
        "vector": {
            "status": vector_status,
            "checks": [{"ok": c.ok, "code": c.code, "detail": c.detail} for c in vector_checks],
        },
    }
    from knowledge_base.rag.operational_monitoring import build_monitoring_readiness_checks

    monitoring_checks = build_monitoring_readiness_checks(tenant=tenant)
    monitoring_status = "READY"
    if any(not item["ok"] and item["severity"] == "warning" for item in monitoring_checks):
        monitoring_status = "READY_WITH_WARNINGS"
    sections["monitoring"] = {
        "status": monitoring_status,
        "checks": monitoring_checks,
    }
    overall = _overall_from_sections(sections)
    sections["overall"] = overall
    return sections


def build_health_recommendations(
    *,
    configuration_present: bool,
    configuration_snapshot: dict,
    vector_health: dict,
    operations_summary: dict,
    retrieval_metrics: dict,
    ai_usage: dict,
    readiness: dict,
) -> list[HealthRecommendation]:
    items: list[HealthRecommendation] = []

    if not configuration_present:
        items.append(
            HealthRecommendation(
                code="configuration_missing",
                severity="warning",
                message="RAG não configurado para este tenant.",
                action="Cadastre origem e limites na configuração RAG.",
            )
        )

    if configuration_snapshot.get("operations_dry_run"):
        items.append(
            HealthRecommendation(
                code="operations_dry_run",
                severity="info",
                message="Operações RAG estão em modo simulação (dry-run).",
                action="Execução real permanece bloqueada enquanto o dry-run estiver ativo.",
            )
        )

    if not configuration_snapshot.get("operations_enabled"):
        items.append(
            HealthRecommendation(
                code="operations_disabled",
                severity="info",
                message="Solicitações operacionais estão desabilitadas globalmente.",
                action="Habilite LIVIA_RAG_OPERATIONS_ENABLED apenas após autorização.",
            )
        )

    if operations_summary.get("stale_running", 0) > 0:
        items.append(
            HealthRecommendation(
                code="stale_operations",
                severity="critical",
                message="Há execuções running com lease expirado.",
                action="Execute recover-stale via worker controlado e revise o journal.",
            )
        )

    if vector_health.get("status_label") == "REINDEX_REQUIRED":
        items.append(
            HealthRecommendation(
                code="embeddings_incompatible",
                severity="warning",
                message="Embeddings incompatíveis ou inválidos detectados.",
                action="Revise vector health antes de solicitar reindexação.",
            )
        )

    if retrieval_metrics.get("has_data") and retrieval_metrics.get("executed", 0) > 0:
        hit_rate = retrieval_metrics.get("hit_rate")
        if hit_rate is not None and hit_rate < 0.2:
            items.append(
                HealthRecommendation(
                    code="retrieval_empty_elevated",
                    severity="warning",
                    message="Taxa de hit de retrieval baixa no período.",
                    action="Revise corpus, threshold efetivo e consultas de teste.",
                )
            )

    if ai_usage.get("has_data") and ai_usage.get("failure", 0) > 0:
        items.append(
            HealthRecommendation(
                code="ai_failures_recent",
                severity="warning",
                message="Falhas recentes de IA registradas.",
                action="Verifique provider, limites e fallback determinístico.",
            )
        )

    if readiness.get("database", {}).get("status") == "NOT_READY":
        items.append(
            HealthRecommendation(
                code="database_not_ready",
                severity="critical",
                message="Há migrations pendentes no ambiente.",
                action="Aplique migrations no ambiente alvo antes de operar.",
            )
        )

    return items


def classify_overall_health(
    *,
    configuration_present: bool,
    retrieval_metrics: dict,
    ai_usage: dict,
    operations_summary: dict,
    readiness: dict,
    recommendations: list[HealthRecommendation],
) -> OverallHealth:
    if any(item.severity == "critical" for item in recommendations):
        return OverallHealth(
            label="BLOQUEADO",
            tone="danger",
            readiness_code="NOT_READY",
            detail="Existem condições críticas que exigem ação imediata.",
        )

    if not configuration_present:
        return OverallHealth(
            label="SEM DADOS",
            tone="secondary",
            readiness_code="NOT_READY",
            detail="Configure a base de conhecimento para iniciar observabilidade completa.",
        )

    has_activity = bool(
        retrieval_metrics.get("has_data")
        or ai_usage.get("has_data")
        or operations_summary.get("pending", 0)
        or operations_summary.get("running", 0)
    )
    if not has_activity:
        return OverallHealth(
            label="SEM DADOS",
            tone="secondary",
            readiness_code=readiness.get("overall", {}).get("status", "READY_WITH_WARNINGS"),
            detail="Nenhum evento operacional no período selecionado.",
        )

    overall_status = readiness.get("overall", {}).get("status", "READY")
    if any(item.severity == "warning" for item in recommendations) or overall_status == "READY_WITH_WARNINGS":
        return OverallHealth(
            label="ATENÇÃO",
            tone="warning",
            readiness_code=overall_status,
            detail="Há sinais operacionais que merecem revisão.",
        )

    return OverallHealth(
        label="SAUDÁVEL",
        tone="success",
        readiness_code=overall_status,
        detail="Indicadores principais dentro do esperado para o período.",
    )


def _overall_from_sections(sections: dict) -> dict:
    statuses = [
        sections["environment"]["status"],
        sections["database"]["status"],
        sections["rag_operations"]["status"],
        sections["vector"]["status"],
    ]
    if "NOT_READY" in statuses:
        status = "NOT_READY"
    elif "READY_WITH_WARNINGS" in statuses:
        status = "READY_WITH_WARNINGS"
    else:
        status = "READY"
    return {"status": status}
