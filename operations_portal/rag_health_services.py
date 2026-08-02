from __future__ import annotations

from django.core.paginator import Paginator

from knowledge_base.models import RagRetrievalEvent, TenantRagConfiguration
from knowledge_base.rag.operational_diagnostics import (
    build_consolidated_readiness,
    build_health_recommendations,
    classify_overall_health,
)
from knowledge_base.rag.operational_metrics import (
    DEFAULT_PERIOD,
    build_ai_usage_summary,
    build_operations_summary,
    build_rag_configuration_snapshot,
    build_retrieval_metrics,
    build_vector_health_summary,
    parse_health_period,
)
from operations_portal.knowledge_base_selectors import (
    PAGE_SIZE,
    get_operation_request_list,
    serialize_operation_request,
    serialize_retrieval_event,
)


def build_rag_health_dashboard(*, tenant, period: str | None = None) -> dict:
    normalized_period = parse_health_period(period)
    configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    config_snapshot = build_rag_configuration_snapshot(tenant=tenant, configuration=configuration)
    from operations_portal.knowledge_base_services import compute_effective_rag_limits

    config_snapshot = {**config_snapshot, "limits": compute_effective_rag_limits(configuration=configuration)}
    vector_health = build_vector_health_summary(tenant=tenant)
    operations_summary = build_operations_summary(tenant=tenant)
    retrieval_metrics = build_retrieval_metrics(tenant=tenant, period=normalized_period)
    ai_usage = build_ai_usage_summary(tenant=tenant, period=normalized_period)
    readiness = build_consolidated_readiness(tenant=tenant)
    recommendations = build_health_recommendations(
        configuration_present=config_snapshot["configuration_present"],
        configuration_snapshot=config_snapshot,
        vector_health=vector_health,
        operations_summary=operations_summary,
        retrieval_metrics=retrieval_metrics,
        ai_usage=ai_usage,
        readiness=readiness,
    )
    overall = classify_overall_health(
        configuration_present=config_snapshot["configuration_present"],
        retrieval_metrics=retrieval_metrics,
        ai_usage=ai_usage,
        operations_summary=operations_summary,
        readiness=readiness,
        recommendations=recommendations,
    )

    period_label = {
        "24h": "Últimas 24 horas",
        "7d": "Últimos 7 dias",
        "30d": "Últimos 30 dias",
    }[normalized_period]

    scorecards = _build_scorecards(
        config_snapshot=config_snapshot,
        vector_health=vector_health,
        operations_summary=operations_summary,
        retrieval_metrics=retrieval_metrics,
        ai_usage=ai_usage,
        period_label=period_label,
    )

    from django.conf import settings
    from knowledge_base.rag.operational_notification_metrics import build_notification_metrics

    notification_metrics = build_notification_metrics(tenant=tenant)

    return {
        "period": normalized_period,
        "period_label": period_label,
        "overall": overall,
        "scorecards": scorecards,
        "configuration": config_snapshot,
        "vector_health": vector_health,
        "operations_summary": operations_summary,
        "retrieval_metrics": retrieval_metrics,
        "ai_usage": ai_usage,
        "readiness": readiness,
        "recommendations": recommendations,
        "notification_metrics": notification_metrics,
        "notification_email": {
            "enabled": bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED", False)),
            "dry_run": bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN", True)),
        },
        "links": {
            "operations": "operations_portal:knowledge_base_operations",
            "diagnostic": "operations_portal:knowledge_base_diagnostic",
            "config": "operations_portal:knowledge_base_config",
            "events": "operations_portal:knowledge_base_events",
        },
    }


def get_health_operation_page(*, tenant, page_number=1):
    return get_operation_request_list(tenant=tenant, page_number=page_number)


def get_health_retrieval_page(*, tenant, period: str, page_number=1):
    from knowledge_base.rag.operational_metrics import period_window

    _, since = period_window(period=parse_health_period(period))
    queryset = (
        RagRetrievalEvent.objects.filter(tenant=tenant, created_at__gte=since)
        .order_by("-created_at", "-id")
    )
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    page.object_list = [serialize_retrieval_event(event) for event in page.object_list]
    return page


def get_health_ai_usage_page(*, tenant, period: str, page_number=1):
    from assistant_core.models import AiUsageEvent
    from knowledge_base.rag.operational_metrics import period_window

    _, since = period_window(period=parse_health_period(period))
    queryset = (
        AiUsageEvent.objects.filter(tenant=tenant, created_at__gte=since)
        .order_by("-created_at", "-id")
    )
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    page.object_list = [
        {
            "id": item.pk,
            "operation": item.operation,
            "model": item.model or "-",
            "success": item.success,
            "error_category": item.error_type or "-",
            "total_tokens": item.total_tokens,
            "latency_ms": item.latency_ms,
            "created_at": item.created_at,
        }
        for item in page.object_list
    ]
    return page


def _build_scorecards(
    *,
    config_snapshot: dict,
    vector_health: dict,
    operations_summary: dict,
    retrieval_metrics: dict,
    ai_usage: dict,
    period_label: str,
) -> list[dict]:
    cards: list[dict] = []

    config_status = "info"
    if not config_snapshot["configuration_present"]:
        config_status = "warning"
    elif config_snapshot["rag_semantic_active"]:
        config_status = "success"
    cards.append(
        {
            "title": "Configuração RAG",
            "value": "Ativa" if config_snapshot["rag_semantic_active"] else "Inativa",
            "status": config_status,
            "period": "Efetiva agora",
            "detail": "Gates globais + tenant",
        }
    )

    vector_status = "success" if vector_health["status_label"] == "OK" else "warning"
    if vector_health["status_label"] == "REINDEX_REQUIRED":
        vector_status = "danger"
    cards.append(
        {
            "title": "Vector health",
            "value": vector_health["status_label"],
            "status": vector_status,
            "period": "Instantâneo",
            "detail": f"Cobertura {vector_health['coverage']['coverage'] * 100:.1f}%",
        }
    )

    cards.append(
        {
            "title": "Operações ativas",
            "value": operations_summary["pending"] + operations_summary["running"],
            "status": "warning" if operations_summary["stale_running"] else "info",
            "period": "Fila atual",
            "detail": f"Stale: {operations_summary['stale_running']}",
        }
    )

    if retrieval_metrics["has_data"]:
        hit_rate = retrieval_metrics["hit_rate"]
        cards.append(
            {
                "title": "Retrieval",
                "value": f"{int((hit_rate or 0) * 100)}% hit",
                "status": "success" if hit_rate and hit_rate >= 0.3 else "warning",
                "period": period_label,
                "detail": f"{retrieval_metrics['executed']} execuções",
            }
        )
    else:
        cards.append(
            {
                "title": "Retrieval",
                "value": "Sem dados",
                "status": "secondary",
                "period": period_label,
                "detail": "Nenhum evento no período",
            }
        )

    if ai_usage["has_data"]:
        cards.append(
            {
                "title": "Tokens IA",
                "value": ai_usage["total_tokens"],
                "status": "info",
                "period": period_label,
                "detail": f"{ai_usage['requests']} requests",
            }
        )
    else:
        cards.append(
            {
                "title": "Tokens IA",
                "value": "Sem dados",
                "status": "secondary",
                "period": period_label,
                "detail": "Nenhum AiUsageEvent no período",
            }
        )

    cards.append(
        {
            "title": "Falhas IA",
            "value": ai_usage["failure"] if ai_usage["has_data"] else "Sem dados",
            "status": "warning" if ai_usage.get("failure") else "success",
            "period": period_label,
            "detail": "Categorias sanitizadas abaixo",
        }
    )

    return cards
