"""Agregações SQL do painel Qualidade da Lívia — reutiliza models existentes."""

from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from urllib.parse import urlparse

from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, Prefetch, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from assistant_core.services.deterministic_synthesis import is_generic_fallback_reply
from conversations.models import ChatRequest, Conversation, HandoffRequest, Message
from integrations.models import OutboxEvent
from knowledge_base.models import (
    KnowledgeDocument,
    RagRetrievalEvent,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
)
from knowledge_base.rag.embedding_profile import embedding_coverage_breakdown
from leads.models import LeadDraft
from tenants.models import Tenant

from .quality_thresholds import (
    CHAT_ERROR_RATE_DEGRADED,
    CHAT_ERROR_RATE_WARNING,
    DEAD_LETTER_DEGRADED,
    EMBEDDINGS_PENDING_WARNING,
    FALLBACK_RATE_DEGRADED,
    FALLBACK_RATE_WARNING,
    LATENCY_P95_DEGRADED_MS,
    LATENCY_P95_WARNING_MS,
    PROCESSING_STUCK_MINUTES,
    QUALITY_SCORE_WEIGHTS,
    RAG_HIT_RATE_DEGRADED,
    RAG_HIT_RATE_WARNING,
    status_from_rate,
    tone_for_status,
)

PERIOD_CHOICES = ("today", "7d", "30d", "custom")
FALLBACK_PREFIX = "Entendi. Pode me explicar um pouco mais"
SCORE_BUCKETS = (
    ("0.00–0.30", 0.0, 0.30),
    ("0.30–0.45", 0.30, 0.45),
    ("0.45–0.60", 0.45, 0.60),
    ("0.60–0.75", 0.60, 0.75),
    ("0.75+", 0.75, None),
)
PAGE_SIZE = 25
STOPWORDS = {
    "a",
    "o",
    "os",
    "as",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "em",
    "um",
    "uma",
    "para",
    "por",
    "com",
    "que",
    "qual",
    "quais",
    "como",
    "meu",
    "minha",
    "seu",
    "sua",
    "no",
    "na",
    "nos",
    "nas",
    "me",
    "te",
    "se",
    "eu",
    "voce",
    "você",
}


@dataclass(frozen=True)
class QualityPeriod:
    key: str
    start: datetime
    end: datetime
    label: str


def resolve_quality_period(
    *,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> QualityPeriod:
    current_tz = timezone.get_current_timezone()
    now = timezone.now()
    today = timezone.localdate()
    key = (period or "7d").strip().lower()
    if key not in PERIOD_CHOICES:
        key = "7d"

    if key == "today":
        start_dt = timezone.make_aware(datetime.combine(today, time.min), current_tz)
        end_dt = now
        label = f"Hoje ({today:%d/%m/%Y})"
    elif key == "7d":
        start_date = today - timedelta(days=6)
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
        end_dt = now
        label = f"Últimos 7 dias ({start_date:%d/%m/%Y}–{today:%d/%m/%Y})"
    elif key == "30d":
        start_date = today - timedelta(days=29)
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
        end_dt = now
        label = f"Últimos 30 dias ({start_date:%d/%m/%Y}–{today:%d/%m/%Y})"
    else:
        try:
            start_date = datetime.strptime(str(start), "%Y-%m-%d").date()
            end_date = datetime.strptime(str(end), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            start_date = today - timedelta(days=6)
            end_date = today
            key = "7d"
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
        end_dt = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), current_tz)
        label = f"Custom ({start_date:%d/%m/%Y}–{end_date:%d/%m/%Y})"
        key = "custom"

    return QualityPeriod(key=key, start=start_dt, end=end_dt, label=label)


def _scope(qs, tenant):
    if tenant is None:
        return qs
    return qs.filter(tenant=tenant)


def _scope_message(qs, tenant):
    if tenant is None:
        return qs
    return qs.filter(conversation__tenant=tenant)


def _pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)


def _percentile(values: list[int | float], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return int(ordered[0])
    rank = (pct / 100) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return int(ordered[low] * (1 - weight) + ordered[high] * weight)


def _mask_folder(folder_id: str) -> str:
    value = str(folder_id or "").strip()
    if not value:
        return "—"
    if len(value) <= 8:
        return value[:2] + "…" + value[-2:]
    return value[:4] + "…" + value[-4:]


def _source_page_label(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "(sem origem)"
    try:
        parsed = urlparse(raw)
        path = (parsed.path or "/").rstrip("/") or "/"
        host = parsed.netloc or ""
        if path == "/":
            return f"{host}/" if host else "homepage"
        leaf = path.split("/")[-1] or path
        return leaf[:80]
    except Exception:  # noqa: BLE001
        return raw[:80]


def _normalize_question(text: str) -> str:
    value = str(text or "").lower().strip()
    value = re.sub(r"[^\w\sÀ-ÿ]", " ", value, flags=re.UNICODE)
    tokens = [t for t in value.split() if t and t not in STOPWORDS and len(t) > 2]
    return " ".join(tokens[:12])


def _question_fingerprint(text: str) -> str:
    normalized = _normalize_question(text)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def build_quality_dashboard(*, tenant=None, period: QualityPeriod, accessible_tenant_ids=None) -> dict:
    conversation_qs = _scope(Conversation.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    message_qs = _scope_message(Message.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    chat_qs = _scope(ChatRequest.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    lead_qs = _scope(LeadDraft.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    handoff_qs = _scope(HandoffRequest.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    outbox_qs = _scope(OutboxEvent.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    retrieval_qs = _scope(RagRetrievalEvent.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)

    today_start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min), timezone.get_current_timezone())
    conversations_today = _scope(Conversation.objects.all(), tenant).filter(created_at__gte=today_start).count()
    messages_today = _scope_message(Message.objects.all(), tenant).filter(created_at__gte=today_start).count()
    leads_today = _scope(LeadDraft.objects.all(), tenant).filter(created_at__gte=today_start).count()
    handoffs_today = _scope(HandoffRequest.objects.all(), tenant).filter(created_at__gte=today_start).count()

    active_tenants = Tenant.objects.filter(is_active=True)
    if accessible_tenant_ids is not None:
        active_tenants = active_tenants.filter(pk__in=list(accessible_tenant_ids))
    if tenant is not None:
        active_tenants = active_tenants.filter(pk=tenant.pk)

    fallback = build_fallback_metrics(tenant=tenant, period=period)
    rag = build_rag_metrics(tenant=tenant, period=period)
    latency = build_latency_metrics(tenant=tenant, period=period)
    errors = build_error_metrics(tenant=tenant, period=period)
    chat_requests = build_chat_request_metrics(tenant=tenant, period=period)
    leads = build_lead_metrics(tenant=tenant, period=period)
    handoffs = build_handoff_metrics(tenant=tenant, period=period)
    outbox = build_outbox_metrics(tenant=tenant, period=period)
    emails = build_email_metrics(tenant=tenant, period=period)
    corpus = build_corpus_metrics(tenant=tenant)
    drive = build_drive_status(tenant=tenant)
    funnel = build_consultative_funnel(tenant=tenant, period=period)
    intents = build_intent_distribution(tenant=tenant, period=period)
    top_questions = build_top_questions(tenant=tenant, period=period, limit=10)
    top_sources = build_top_source_pages(tenant=tenant, period=period, limit=10)
    alerts = build_visual_alerts(
        fallback=fallback,
        rag=rag,
        errors=errors,
        outbox=outbox,
        corpus=corpus,
        drive=drive,
        latency=latency,
        chat_requests=chat_requests,
    )
    quality_score = build_tenant_quality_score(
        fallback=fallback,
        rag=rag,
        errors=errors,
        outbox=outbox,
        corpus=corpus,
        drive=drive,
        emails=emails,
    )

    cards = [
        {"key": "conversations_today", "label": "Conversas hoje", "value": conversations_today, "hint": f"Período: {conversation_qs.count()}"},
        {"key": "messages_today", "label": "Mensagens hoje", "value": messages_today, "hint": f"Período: {message_qs.count()}"},
        {"key": "active_tenants", "label": "Tenants ativos", "value": active_tenants.count(), "hint": "Escopo atual"},
        {"key": "leads_today", "label": "Leads hoje", "value": leads_today, "hint": f"Período: {leads['total']}"},
        {"key": "handoffs_today", "label": "Handoffs hoje", "value": handoffs_today, "hint": f"Período: {handoffs['total']}"},
        {
            "key": "fallback_rate",
            "label": "Fallback rate",
            "value": _format_rate(fallback["rate"]),
            "hint": f"{fallback['fallback_count']}/{fallback['assistant_message_count']}",
            "status": fallback["status"],
        },
        {
            "key": "rag_hit_rate",
            "label": "RAG hit rate",
            "value": _format_rate(rag["hit_rate"]),
            "hint": f"{rag['hits']}/{rag['attempted']}",
            "status": rag["status"],
        },
        {
            "key": "chat_errors",
            "label": "Erros de chat",
            "value": errors["chat_failed"],
            "hint": f"4xx={errors['http_4xx']} · 5xx={errors['http_5xx']}",
            "status": errors["status"],
        },
        {
            "key": "latency_p95",
            "label": "Latência P95",
            "value": f"{latency['p95_ms']} ms",
            "hint": f"P50={latency['p50_ms']} · P99={latency['p99_ms']}",
            "status": latency["status"],
        },
        {"key": "emails_sent", "label": "E-mails enviados", "value": emails["sent"], "hint": f"Falhas: {emails['failed']}"},
        {
            "key": "dead_letters",
            "label": "Dead letters",
            "value": outbox["dead_letter"],
            "hint": f"Pending={outbox['pending']}",
            "status": outbox["status"],
        },
        {
            "key": "drive_sync",
            "label": "Drive sync",
            "value": drive["summary_status"] if isinstance(drive, dict) else "—",
            "hint": drive.get("summary_hint", "") if isinstance(drive, dict) else "",
            "status": drive.get("status", "green") if isinstance(drive, dict) else "green",
        },
        {"key": "documents_active", "label": "Documentos ativos", "value": corpus["documents_active"], "hint": f"Total: {corpus['documents_total']}"},
        {"key": "chunks", "label": "Chunks", "value": corpus["chunks_active"], "hint": f"Indexáveis: {corpus['chunks_indexable']}"},
        {
            "key": "embeddings_pending",
            "label": "Embeddings pendentes",
            "value": corpus["embeddings_pending"],
            "hint": f"Coverage: {_format_rate(corpus['embedding_coverage'])}",
            "status": "warning" if corpus["embeddings_pending"] >= EMBEDDINGS_PENDING_WARNING else "green",
        },
    ]

    return {
        "period": {
            "key": period.key,
            "label": period.label,
            "start": period.start,
            "end": period.end,
            "options": PERIOD_CHOICES,
        },
        "cards": cards,
        "fallback": fallback,
        "rag": rag,
        "latency": latency,
        "errors": errors,
        "chat_requests": chat_requests,
        "leads": leads,
        "handoffs": handoffs,
        "outbox": outbox,
        "emails": emails,
        "corpus": corpus,
        "drive": drive,
        "funnel": funnel,
        "intents": intents,
        "top_questions": top_questions,
        "top_sources": top_sources,
        "alerts": alerts,
        "quality_score": quality_score,
        "counts": {
            "conversations": conversation_qs.count(),
            "messages": message_qs.count(),
            "chat_requests": chat_qs.count(),
            "leads": lead_qs.count(),
            "handoffs": handoff_qs.count(),
            "outbox": outbox_qs.count(),
            "retrieval": retrieval_qs.count(),
        },
    }


def _format_rate(rate: float | None) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def build_fallback_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    assistant_qs = _scope_message(
        Message.objects.filter(role=Message.Role.ASSISTANT),
        tenant,
    ).filter(created_at__gte=period.start, created_at__lt=period.end)

    assistant_message_count = assistant_qs.count()
    # Agregação SQL barata pelo prefixo característico; detector Python confirma amostra.
    candidates = list(
        assistant_qs.filter(content__istartswith=FALLBACK_PREFIX)
        .select_related("conversation", "conversation__tenant")
        .order_by("-created_at")[:500]
    )
    fallback_messages = [m for m in candidates if is_generic_fallback_reply(m.content)]
    # Contagem aproximada via SQL quando volume > amostra
    sql_fallback = assistant_qs.filter(content__istartswith=FALLBACK_PREFIX).count()
    fallback_count = sql_fallback if assistant_message_count > 500 else len(fallback_messages)
    rate = _pct(fallback_count, assistant_message_count)
    status = status_from_rate(rate=rate or 0.0, warning=FALLBACK_RATE_WARNING, degraded=FALLBACK_RATE_DEGRADED)

    by_tenant_rows = (
        assistant_qs.filter(content__istartswith=FALLBACK_PREFIX)
        .values("conversation__tenant__slug")
        .annotate(fallback_count=Count("id"))
        .order_by("-fallback_count")
    )
    assistant_by_tenant = {
        row["conversation__tenant__slug"]: row["total"]
        for row in assistant_qs.values("conversation__tenant__slug").annotate(total=Count("id"))
    }
    by_tenant = []
    for row in by_tenant_rows:
        slug = row["conversation__tenant__slug"]
        total = assistant_by_tenant.get(slug, 0)
        by_tenant.append(
            {
                "tenant": slug,
                "fallback_count": row["fallback_count"],
                "assistant_message_count": total,
                "rate": _pct(row["fallback_count"], total),
            }
        )

    by_day_rows = (
        assistant_qs.filter(content__istartswith=FALLBACK_PREFIX)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(fallback_count=Count("id"))
        .order_by("day")
    )
    assistant_by_day = {
        row["day"]: row["total"]
        for row in assistant_qs.annotate(day=TruncDate("created_at")).values("day").annotate(total=Count("id"))
    }
    by_day = []
    for row in by_day_rows:
        day = row["day"]
        total = assistant_by_day.get(day, 0)
        by_day.append(
            {
                "day": day,
                "fallback_count": row["fallback_count"],
                "assistant_message_count": total,
                "rate": _pct(row["fallback_count"], total),
            }
        )

    conversations = []
    seen = set()
    for message in fallback_messages[:50]:
        conv = message.conversation
        if conv.pk in seen:
            continue
        seen.add(conv.pk)
        conversations.append(
            {
                "id": conv.pk,
                "tenant": conv.tenant.slug,
                "session_id": conv.session_id,
                "created_at": message.created_at,
                "source_page": conv.source_page,
            }
        )

    return {
        "fallback_count": fallback_count,
        "assistant_message_count": assistant_message_count,
        "rate": rate,
        "status": status,
        "tone": tone_for_status(status),
        "by_tenant": by_tenant,
        "by_day": by_day,
        "conversations": conversations,
    }


def build_rag_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    events = _scope(RagRetrievalEvent.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    attempted_qs = events.exclude(status=RagRetrievalEvent.Status.SKIPPED)
    attempted = attempted_qs.count()
    hits = attempted_qs.filter(hit=True).count()
    misses = attempted_qs.filter(hit=False).count()
    skipped = events.filter(status=RagRetrievalEvent.Status.SKIPPED).count()
    empty = attempted_qs.filter(status=RagRetrievalEvent.Status.EMPTY).count()
    failed = attempted_qs.filter(status=RagRetrievalEvent.Status.FAILED).count()
    aggregates = attempted_qs.aggregate(
        avg_score=Avg("max_score"),
        avg_chunks=Avg("result_count"),
        avg_latency=Avg("duration_ms"),
    )
    hit_rate = _pct(hits, attempted)
    status = status_from_rate(
        rate=hit_rate if hit_rate is not None else 1.0,
        warning=RAG_HIT_RATE_WARNING,
        degraded=RAG_HIT_RATE_DEGRADED,
        higher_is_worse=False,
    )

    buckets = []
    for label, low, high in SCORE_BUCKETS:
        q = Q(max_score__gte=low)
        if high is not None:
            q &= Q(max_score__lt=high)
        buckets.append({"label": label, "count": attempted_qs.filter(q).count()})

    by_tenant = []
    for row in (
        attempted_qs.values("tenant__slug")
        .annotate(attempted=Count("id"), hits=Count("id", filter=Q(hit=True)), avg_score=Avg("max_score"), avg_chunks=Avg("result_count"))
        .order_by("-attempted")
    ):
        by_tenant.append(
            {
                "tenant": row["tenant__slug"],
                "attempted": row["attempted"],
                "hits": row["hits"],
                "misses": row["attempted"] - row["hits"],
                "hit_rate": _pct(row["hits"], row["attempted"]),
                "avg_score": round(float(row["avg_score"] or 0), 3),
                "avg_chunks": round(float(row["avg_chunks"] or 0), 2),
            }
        )

    return {
        "attempted": attempted,
        "hits": hits,
        "misses": misses,
        "empty": empty,
        "failed": failed,
        "skipped": skipped,
        "hit_rate": hit_rate,
        "avg_max_score": round(float(aggregates["avg_score"] or 0), 3),
        "avg_chunks_used": round(float(aggregates["avg_chunks"] or 0), 2),
        "avg_latency_ms": round(float(aggregates["avg_latency"] or 0), 1),
        "score_buckets": buckets,
        "by_tenant": by_tenant,
        "status": status,
        "tone": tone_for_status(status),
    }


def build_latency_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    chat_qs = (
        _scope(ChatRequest.objects.all(), tenant)
        .filter(
            created_at__gte=period.start,
            created_at__lt=period.end,
            status=ChatRequest.Status.COMPLETED,
            completed_at__isnull=False,
        )
        .annotate(latency=ExpressionWrapper(F("completed_at") - F("created_at"), output_field=DurationField()))
    )
    latencies_ms = []
    for delta in chat_qs.values_list("latency", flat=True).iterator(chunk_size=500):
        if delta is None:
            continue
        latencies_ms.append(int(delta.total_seconds() * 1000))

    retrieval_latencies = list(
        _scope(RagRetrievalEvent.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .exclude(status=RagRetrievalEvent.Status.SKIPPED)
        .values_list("duration_ms", flat=True)[:5000]
    )
    retrieval_latencies = [int(v or 0) for v in retrieval_latencies]

    # Observabilidade leve a partir do payload (quando instrumentado).
    decision_ms: list[int] = []
    synthesis_ms: list[int] = []
    notification_ms: list[int] = []
    payload_rows = (
        _scope(ChatRequest.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, status=ChatRequest.Status.COMPLETED)
        .values_list("response_payload", flat=True)[:2000]
    )
    for payload in payload_rows:
        obs = (payload or {}).get("observability") or {}
        if obs.get("decision_ms") is not None:
            decision_ms.append(int(obs["decision_ms"]))
        if obs.get("synthesis_ms") is not None:
            synthesis_ms.append(int(obs["synthesis_ms"]))
        if obs.get("notification_ms") is not None:
            notification_ms.append(int(obs["notification_ms"]))

    p50 = _percentile(latencies_ms, 50)
    p95 = _percentile(latencies_ms, 95)
    p99 = _percentile(latencies_ms, 99)
    if p95 >= LATENCY_P95_DEGRADED_MS:
        status = "degraded"
    elif p95 >= LATENCY_P95_WARNING_MS:
        status = "warning"
    else:
        status = "green"

    by_tenant = []
    for row in (
        _scope(ChatRequest.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, status=ChatRequest.Status.COMPLETED, completed_at__isnull=False)
        .values("tenant__slug")
        .annotate(total=Count("id"))
        .order_by("-total")
    ):
        slug = row["tenant__slug"]
        tenant_latencies = []
        t_qs = ChatRequest.objects.filter(
            tenant__slug=slug,
            created_at__gte=period.start,
            created_at__lt=period.end,
            status=ChatRequest.Status.COMPLETED,
            completed_at__isnull=False,
        ).annotate(latency=ExpressionWrapper(F("completed_at") - F("created_at"), output_field=DurationField()))
        for delta in t_qs.values_list("latency", flat=True).iterator(chunk_size=500):
            if delta is not None:
                tenant_latencies.append(int(delta.total_seconds() * 1000))
        by_tenant.append(
            {
                "tenant": slug,
                "count": row["total"],
                "p50_ms": _percentile(tenant_latencies, 50),
                "p95_ms": _percentile(tenant_latencies, 95),
                "p99_ms": _percentile(tenant_latencies, 99),
            }
        )

    return {
        "sample_size": len(latencies_ms),
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "avg_ms": int(statistics.mean(latencies_ms)) if latencies_ms else 0,
        "retrieval_p50_ms": _percentile(retrieval_latencies, 50),
        "retrieval_p95_ms": _percentile(retrieval_latencies, 95),
        "decision_p50_ms": _percentile(decision_ms, 50),
        "synthesis_p50_ms": _percentile(synthesis_ms, 50),
        "notification_p50_ms": _percentile(notification_ms, 50),
        "by_tenant": by_tenant,
        "status": status,
        "tone": tone_for_status(status),
    }


def build_error_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    chat_qs = _scope(ChatRequest.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    total = chat_qs.count()
    failed = chat_qs.filter(status=ChatRequest.Status.FAILED).count()
    http_4xx = chat_qs.filter(response_status_code__gte=400, response_status_code__lt=500).count()
    http_5xx = chat_qs.filter(response_status_code__gte=500).count()
    timeouts = chat_qs.filter(Q(error_code__icontains="timeout") | Q(response_payload__error__icontains="timeout")).count()
    retrieval_failed = (
        _scope(RagRetrievalEvent.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, status=RagRetrievalEvent.Status.FAILED)
        .count()
    )
    drive_errors = (
        _scope(TenantRagConfiguration.objects.all(), tenant)
        .filter(last_inventory_status=TenantRagConfiguration.InventoryStatus.FAILED)
        .count()
    )
    outbox_dead = (
        _scope(OutboxEvent.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, status=OutboxEvent.Status.DEAD_LETTER)
        .count()
    )
    smtp_errors = (
        _scope(OutboxEvent.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .filter(Q(last_error_code__icontains="smtp") | Q(last_error_message__icontains="smtp") | Q(last_error_code__icontains="email"))
        .count()
    )
    error_rate = _pct(failed, total) or 0.0
    status = status_from_rate(rate=error_rate, warning=CHAT_ERROR_RATE_WARNING, degraded=CHAT_ERROR_RATE_DEGRADED)
    top_errors = list(
        chat_qs.exclude(error_code="")
        .values("error_code")
        .annotate(total=Count("id"), last_seen=Max("created_at"))
        .order_by("-total")[:10]
    )
    return {
        "chat_failed": failed,
        "http_4xx": http_4xx,
        "http_5xx": http_5xx,
        "timeouts": timeouts,
        "retrieval_failed": retrieval_failed,
        "drive_errors": drive_errors,
        "smtp_errors": smtp_errors,
        "outbox_dead_letter": outbox_dead,
        "error_rate": error_rate,
        "top_errors": top_errors,
        "status": status,
        "tone": tone_for_status(status),
    }


def build_chat_request_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    qs = _scope(ChatRequest.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    stuck_before = timezone.now() - timedelta(minutes=PROCESSING_STUCK_MINUTES)
    processing = qs.filter(status=ChatRequest.Status.PROCESSING).count()
    stuck = qs.filter(status=ChatRequest.Status.PROCESSING, updated_at__lt=stuck_before).count()
    completed = qs.filter(status=ChatRequest.Status.COMPLETED).count()
    failed = qs.filter(status=ChatRequest.Status.FAILED).count()
    # Replay/conflict são inferidos por fingerprint duplicado e códigos.
    conflicts = qs.filter(Q(error_code__icontains="conflict") | Q(response_payload__error__icontains="conflict")).count()
    replays = qs.filter(Q(response_payload__replay=True) | Q(error_code__icontains="replay")).count()
    status = "degraded" if stuck > 0 else ("warning" if processing > 0 else "green")
    return {
        "completed": completed,
        "failed": failed,
        "processing": processing,
        "stuck_processing": stuck,
        "replay": replays,
        "conflict": conflicts,
        "status": status,
        "tone": tone_for_status(status),
        "stuck_threshold_minutes": PROCESSING_STUCK_MINUTES,
    }


def build_lead_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    qs = _scope(LeadDraft.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    totals = qs.aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=LeadDraft.Status.DRAFT)),
        qualified=Count("id", filter=Q(status=LeadDraft.Status.QUALIFIED)),
        sent=Count("id", filter=Q(status=LeadDraft.Status.SENT_TO_CRM)),
        failed=Count("id", filter=Q(status=LeadDraft.Status.FAILED)),
        notified=Count("id", filter=Q(qualification_data__has_key="lead_notification_sent_at")),
    )
    by_tenant = list(
        qs.values("tenant__slug")
        .annotate(
            total=Count("id"),
            qualified=Count("id", filter=Q(status=LeadDraft.Status.QUALIFIED)),
            sent=Count("id", filter=Q(status=LeadDraft.Status.SENT_TO_CRM)),
            failed=Count("id", filter=Q(status=LeadDraft.Status.FAILED)),
        )
        .order_by("-total")
    )
    return {
        "total": totals["total"] or 0,
        "draft": totals["draft"] or 0,
        "qualified": totals["qualified"] or 0,
        "sent": totals["sent"] or 0,
        "failed": totals["failed"] or 0,
        "notified": totals["notified"] or 0,
        "by_tenant": by_tenant,
    }


def build_handoff_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    qs = _scope(HandoffRequest.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    totals = qs.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=HandoffRequest.Status.PENDING)),
        sent=Count("id", filter=Q(status=HandoffRequest.Status.SENT)),
        resolved=Count("id", filter=Q(status=HandoffRequest.Status.RESOLVED)),
        cancelled=Count("id", filter=Q(status=HandoffRequest.Status.CANCELLED)),
        dispatch_failed=Count("id", filter=Q(dispatch_state=HandoffRequest.DispatchState.FAILED)),
        dispatch_delivered=Count("id", filter=Q(dispatch_state=HandoffRequest.DispatchState.DELIVERED)),
    )
    notification_deltas = []
    for meta, created_at in qs.exclude(metadata={}).values_list("metadata", "created_at")[:500]:
        sent_at = (meta or {}).get("handoff_notification_sent_at")
        if not sent_at or not created_at:
            continue
        try:
            sent_dt = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
            if timezone.is_naive(sent_dt):
                sent_dt = timezone.make_aware(sent_dt, timezone.utc)
            notification_deltas.append(int((sent_dt - created_at).total_seconds() * 1000))
        except Exception:  # noqa: BLE001
            continue
    by_tenant = list(
        qs.values("tenant__slug")
        .annotate(
            total=Count("id"),
            pending=Count("id", filter=Q(status=HandoffRequest.Status.PENDING)),
            sent=Count("id", filter=Q(status=HandoffRequest.Status.SENT)),
            failed=Count("id", filter=Q(dispatch_state=HandoffRequest.DispatchState.FAILED)),
        )
        .order_by("-total")
    )
    return {
        "total": totals["total"] or 0,
        "pending": totals["pending"] or 0,
        "sent": totals["sent"] or 0,
        "resolved": totals["resolved"] or 0,
        "cancelled": totals["cancelled"] or 0,
        "failed": totals["dispatch_failed"] or 0,
        "delivered": totals["dispatch_delivered"] or 0,
        "notification_latency_p50_ms": _percentile(notification_deltas, 50),
        "notification_latency_p95_ms": _percentile(notification_deltas, 95),
        "by_tenant": by_tenant,
    }


def build_outbox_metrics(*, tenant=None, period: QualityPeriod, event_type: str | None = None) -> dict:
    qs = _scope(OutboxEvent.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    if event_type:
        qs = qs.filter(event_type=event_type)
    totals = qs.aggregate(
        pending=Count("id", filter=Q(status=OutboxEvent.Status.PENDING)),
        processing=Count("id", filter=Q(status=OutboxEvent.Status.PROCESSING)),
        succeeded=Count("id", filter=Q(status=OutboxEvent.Status.SUCCEEDED)),
        retry=Count("id", filter=Q(status=OutboxEvent.Status.RETRY)),
        dead_letter=Count("id", filter=Q(status=OutboxEvent.Status.DEAD_LETTER)),
        skipped=Count("id", filter=Q(status=OutboxEvent.Status.SKIPPED)),
        total=Count("id"),
    )
    by_type = list(qs.values("event_type").annotate(total=Count("id")).order_by("-total"))
    status = "degraded" if (totals["dead_letter"] or 0) >= DEAD_LETTER_DEGRADED else "green"
    return {
        **{k: totals[k] or 0 for k in ("pending", "processing", "succeeded", "retry", "dead_letter", "skipped", "total")},
        "delivered": totals["succeeded"] or 0,
        "failed": (totals["dead_letter"] or 0) + (totals["retry"] or 0),
        "by_type": by_type,
        "status": status,
        "tone": tone_for_status(status),
    }


def build_email_metrics(*, tenant=None, period: QualityPeriod) -> dict:
    leads_notified = (
        _scope(LeadDraft.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, qualification_data__has_key="lead_notification_sent_at")
        .count()
    )
    handoffs_notified = 0
    for meta in (
        _scope(HandoffRequest.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .values_list("metadata", flat=True)[:2000]
    ):
        if (meta or {}).get("handoff_notification_sent_at"):
            handoffs_notified += 1
    outbox_email_failures = (
        _scope(OutboxEvent.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .filter(Q(last_error_message__icontains="smtp") | Q(last_error_code__icontains="smtp") | Q(status=OutboxEvent.Status.DEAD_LETTER))
        .count()
    )
    rows = []
    for lead in (
        _scope(LeadDraft.objects.select_related("tenant"), tenant)
        .filter(qualification_data__has_key="lead_notification_sent_at", created_at__gte=period.start, created_at__lt=period.end)
        .order_by("-updated_at")[:30]
    ):
        rows.append(
            {
                "tenant": lead.tenant.slug,
                "event": "lead.qualified",
                "recipient": _mask_recipient(getattr(lead, "email", "")),
                "status": "sent",
                "timestamp": (lead.qualification_data or {}).get("lead_notification_sent_at") or lead.updated_at,
            }
        )
    for handoff in (
        _scope(HandoffRequest.objects.select_related("tenant"), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .order_by("-updated_at")[:30]
    ):
        sent_at = (handoff.metadata or {}).get("handoff_notification_sent_at")
        if not sent_at:
            continue
        rows.append(
            {
                "tenant": handoff.tenant.slug,
                "event": "handoff.requested",
                "recipient": _mask_recipient(handoff.visitor_email),
                "status": "sent",
                "timestamp": sent_at,
            }
        )
    rows.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    return {
        "sent": leads_notified + handoffs_notified,
        "leads_sent": leads_notified,
        "handoffs_sent": handoffs_notified,
        "failed": outbox_email_failures,
        "rows": rows[:40],
    }


def _mask_recipient(value: str) -> str:
    text = str(value or "").strip()
    if not text or "@" not in text:
        return "—"
    local, _, domain = text.partition("@")
    if len(local) <= 2:
        masked = local[:1] + "*"
    else:
        masked = local[:2] + "***"
    return f"{masked}@{domain}"


def build_corpus_metrics(*, tenant=None) -> dict:
    docs = _scope(KnowledgeDocument.objects.all(), tenant)
    chunks = _scope(TenantRagDocumentChunk.objects.all(), tenant)
    embeddings = _scope(TenantRagChunkEmbedding.objects.all(), tenant)
    documents_total = docs.count()
    documents_active = docs.filter(status=KnowledgeDocument.Status.ACTIVE).count()
    chunks_active = chunks.filter(is_active=True, status=TenantRagDocumentChunk.Status.ACTIVE).count()
    embeddings_active = embeddings.filter(is_active=True, status=TenantRagChunkEmbedding.Status.ACTIVE).count()

    pending = 0
    coverage = None
    if tenant is not None:
        try:
            breakdown = embedding_coverage_breakdown(tenant=tenant)
            pending = int(breakdown.get("missing_embedding") or 0)
            coverage = float(breakdown.get("coverage") or 0.0)
        except Exception:  # noqa: BLE001
            pending = max(0, chunks_active - embeddings_active)
    else:
        # Global: aproximação barata.
        pending = max(0, chunks_active - embeddings_active)

    duplicates = []
    checksum_dupes = (
        docs.exclude(content_sha256="")
        .values("tenant__slug", "content_sha256")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total")[:20]
    )
    for row in checksum_dupes:
        duplicates.append(
            {
                "tenant": row["tenant__slug"],
                "kind": "checksum",
                "key": row["content_sha256"][:12] + "…",
                "count": row["total"],
            }
        )

    return {
        "documents_total": documents_total,
        "documents_active": documents_active,
        "chunks_active": chunks_active,
        "chunks_indexable": chunks_active,
        "embeddings_active": embeddings_active,
        "embeddings_pending": pending,
        "embedding_coverage": coverage,
        "duplicates": duplicates,
    }


def build_drive_status(*, tenant=None) -> dict:
    configs = list(
        _scope(TenantRagConfiguration.objects.select_related("tenant"), tenant).order_by("tenant__slug")
    )
    rows = []
    worst = "green"
    for config in configs:
        manifests = TenantRagDriveFileManifest.objects.filter(tenant=config.tenant)
        files_seen = manifests.count()
        files_imported = manifests.filter(
            status__in=[
                TenantRagDriveFileManifest.Status.EXPORTED,
                TenantRagDriveFileManifest.Status.UPDATED,
                TenantRagDriveFileManifest.Status.UNCHANGED,
            ]
        ).count()
        files_updated = manifests.filter(status=TenantRagDriveFileManifest.Status.UPDATED).count()
        files_failed = manifests.filter(status=TenantRagDriveFileManifest.Status.FAILED).count()
        sync_status = config.last_inventory_status or TenantRagConfiguration.InventoryStatus.IDLE
        row_status = "green"
        if config.uses_google_drive and config.sync_enabled and sync_status == TenantRagConfiguration.InventoryStatus.FAILED:
            row_status = "degraded"
            worst = "degraded"
        elif sync_status == TenantRagConfiguration.InventoryStatus.PARTIAL:
            row_status = "warning"
            if worst == "green":
                worst = "warning"
        rows.append(
            {
                "tenant": config.tenant.slug,
                "source_mode": config.source_mode,
                "sync_enabled": config.sync_enabled,
                "folder_id_masked": _mask_folder(config.approved_folder_id),
                "last_sync_at": config.last_inventory_at,
                "last_sync_status": sync_status,
                "files_seen": files_seen,
                "files_imported": files_imported,
                "files_updated": files_updated,
                "files_failed": files_failed,
                "status": row_status,
                "tone": tone_for_status(row_status),
            }
        )
    if not rows:
        return {
            "rows": [],
            "summary_status": "n/d",
            "summary_hint": "Sem configuração RAG",
            "status": "green",
            "tone": "secondary",
        }
    if tenant is not None and rows:
        row = rows[0]
        return {
            "rows": rows,
            "summary_status": row["last_sync_status"],
            "summary_hint": f"seen={row['files_seen']} failed={row['files_failed']}",
            "status": worst,
            "tone": tone_for_status(worst),
            **row,
        }
    return {
        "rows": rows,
        "summary_status": worst.upper(),
        "summary_hint": f"{len(rows)} tenants",
        "status": worst,
        "tone": tone_for_status(worst),
    }


def build_consultative_funnel(*, tenant=None, period: QualityPeriod) -> dict:
    conversations = _scope(Conversation.objects.all(), tenant).filter(created_at__gte=period.start, created_at__lt=period.end)
    started = conversations.count()
    answered = conversations.filter(messages__role=Message.Role.ASSISTANT).distinct().count()
    commercial_sessions = {
        cid
        for cid in _scope(ChatRequest.objects.all(), tenant)
        .filter(
            created_at__gte=period.start,
            created_at__lt=period.end,
            response_payload__intent__in=["commercial_interest", "quote_request", "budget"],
        )
        .values_list("conversation_id", flat=True)
        if cid
    }
    commercial = len(commercial_sessions) or conversations.filter(
        lead_state__in=[
            Conversation.LeadState.OFFER_HANDOFF,
            Conversation.LeadState.COLLECT_NEED,
            Conversation.LeadState.COLLECT_NAME_COMPANY,
            Conversation.LeadState.COLLECT_CONTACT,
            Conversation.LeadState.QUALIFIED,
        ]
    ).count()
    collection_started = conversations.filter(
        lead_state__in=[
            Conversation.LeadState.COLLECT_NEED,
            Conversation.LeadState.COLLECT_NAME_COMPANY,
            Conversation.LeadState.COLLECT_CONTACT,
            Conversation.LeadState.QUALIFIED,
        ]
    ).count()
    qualified = conversations.filter(Q(is_qualified=True) | Q(lead_state=Conversation.LeadState.QUALIFIED)).count()
    notified = (
        _scope(LeadDraft.objects.all(), tenant)
        .filter(
            created_at__gte=period.start,
            created_at__lt=period.end,
            qualification_data__has_key="lead_notification_sent_at",
        )
        .count()
    )
    steps = [
        {"key": "started", "label": "Conversation started", "count": started},
        {"key": "answered", "label": "Question answered", "count": answered},
        {"key": "commercial", "label": "Commercial trigger", "count": commercial},
        {"key": "collection", "label": "Collection started", "count": collection_started},
        {"key": "qualified", "label": "Qualified", "count": qualified},
        {"key": "notified", "label": "Notification sent", "count": notified},
    ]
    rate = _pct(qualified, started)
    return {"steps": steps, "conversion_conversation_to_lead": None if rate is None else round(rate * 100, 1)}


def build_intent_distribution(*, tenant=None, period: QualityPeriod) -> list[dict]:
    rows = (
        _scope(ChatRequest.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, status=ChatRequest.Status.COMPLETED)
        .exclude(response_payload={})
        .values("response_payload")
    )
    counter: Counter[str] = Counter()
    for row in rows.iterator(chunk_size=500):
        intent = str((row.get("response_payload") or {}).get("intent") or "unknown").strip() or "unknown"
        counter[intent] += 1
    return [{"intent": intent, "count": count} for intent, count in counter.most_common(20)]


def build_top_questions(*, tenant=None, period: QualityPeriod, limit: int = 10) -> list[dict]:
    qs = (
        _scope_message(Message.objects.filter(role=Message.Role.USER), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .order_by("-created_at")
        .values_list("content", flat=True)[:2000]
    )
    counter: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for content in qs:
        fingerprint = _question_fingerprint(content)
        if not fingerprint:
            continue
        counter[fingerprint] += 1
        samples.setdefault(fingerprint, str(content)[:160])
    return [
        {"question": samples[fp], "count": count, "fingerprint": fp}
        for fp, count in counter.most_common(limit)
    ]


def build_top_source_pages(*, tenant=None, period: QualityPeriod, limit: int = 10) -> list[dict]:
    rows = (
        _scope(Conversation.objects.all(), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end)
        .values("source_page")
        .annotate(total=Count("id"))
        .order_by("-total")[:limit]
    )
    return [
        {"source_page": row["source_page"] or "", "label": _source_page_label(row["source_page"]), "count": row["total"]}
        for row in rows
    ]


def build_knowledge_gaps(*, tenant=None, period: QualityPeriod, page: int = 1, page_size: int = PAGE_SIZE) -> dict:
    """Fila operacional de lacunas: fallback, retrieval empty/failed, score baixo."""
    items = []

    # 1) Fallbacks com pergunta anterior.
    fallback_messages = (
        _scope_message(Message.objects.filter(role=Message.Role.ASSISTANT), tenant)
        .filter(created_at__gte=period.start, created_at__lt=period.end, content__istartswith=FALLBACK_PREFIX)
        .select_related("conversation", "conversation__tenant")
        .order_by("-created_at")[:300]
    )
    for assistant_msg in fallback_messages:
        if not is_generic_fallback_reply(assistant_msg.content):
            continue
        question = (
            Message.objects.filter(
                conversation_id=assistant_msg.conversation_id,
                role=Message.Role.USER,
                created_at__lte=assistant_msg.created_at,
            )
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
        retrieval = None
        if assistant_msg.conversation_id:
            retrieval = (
                RagRetrievalEvent.objects.filter(
                    tenant_id=assistant_msg.conversation.tenant_id,
                    conversation_id=assistant_msg.conversation_id,
                    created_at__lte=assistant_msg.created_at,
                )
                .order_by("-created_at")
                .first()
            )
        items.append(
            {
                "tenant": assistant_msg.conversation.tenant.slug,
                "question": (question or "")[:300],
                "date": assistant_msg.created_at,
                "conversation_id": assistant_msg.conversation_id,
                "session_id": assistant_msg.conversation.session_id,
                "retrieval_status": getattr(retrieval, "status", "unknown"),
                "max_score": getattr(retrieval, "max_score", None),
                "reply": assistant_msg.content[:300],
                "source_page": assistant_msg.conversation.source_page,
                "reason": "fallback",
            }
        )

    # 2) Retrieval empty/failed com conversation_id.
    empty_events = (
        _scope(RagRetrievalEvent.objects.all(), tenant)
        .filter(
            created_at__gte=period.start,
            created_at__lt=period.end,
            status__in=[RagRetrievalEvent.Status.EMPTY, RagRetrievalEvent.Status.FAILED],
            conversation_id__isnull=False,
        )
        .select_related("tenant")
        .order_by("-created_at")[:300]
    )
    seen_keys = {(i["conversation_id"], i["date"].isoformat() if hasattr(i["date"], "isoformat") else str(i["date"])) for i in items}
    for event in empty_events:
        question = (
            Message.objects.filter(conversation_id=event.conversation_id, role=Message.Role.USER, created_at__lte=event.created_at)
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
        reply = (
            Message.objects.filter(conversation_id=event.conversation_id, role=Message.Role.ASSISTANT, created_at__gte=event.created_at)
            .order_by("created_at")
            .values_list("content", flat=True)
            .first()
        )
        conversation = Conversation.objects.filter(pk=event.conversation_id).select_related("tenant").first()
        key = (event.conversation_id, event.created_at.isoformat())
        if key in seen_keys:
            continue
        items.append(
            {
                "tenant": event.tenant.slug,
                "question": (question or "")[:300],
                "date": event.created_at,
                "conversation_id": event.conversation_id,
                "session_id": getattr(conversation, "session_id", ""),
                "retrieval_status": event.status,
                "max_score": event.max_score,
                "reply": (reply or "")[:300],
                "source_page": getattr(conversation, "source_page", ""),
                "reason": "retrieval_miss",
            }
        )

    items.sort(key=lambda row: row["date"], reverse=True)
    paginator = Paginator(items, page_size)
    page_obj = paginator.get_page(page)
    clusters = _cluster_knowledge_gaps(items)
    return {
        "page_obj": page_obj,
        "total": len(items),
        "clusters": clusters[:20],
    }


def _cluster_knowledge_gaps(items: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        fp = _question_fingerprint(item.get("question") or "")
        if not fp:
            continue
        groups[fp].append(item)
    clusters = []
    for fp, rows in groups.items():
        if len(rows) < 2:
            continue
        samples = sorted({(r.get("question") or "").strip() for r in rows if r.get("question")})
        clusters.append(
            {
                "fingerprint": fp,
                "count": len(rows),
                "samples": samples[:5],
                "tenants": sorted({r["tenant"] for r in rows}),
            }
        )
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def build_documents_without_embedding(*, tenant=None, page: int = 1, page_size: int = PAGE_SIZE) -> dict:
    active_chunks = _scope(
        TenantRagDocumentChunk.objects.filter(is_active=True, status=TenantRagDocumentChunk.Status.ACTIVE),
        tenant,
    ).select_related("tenant", "manifest")
    embedded_ids = set(
        _scope(
            TenantRagChunkEmbedding.objects.filter(is_active=True, status=TenantRagChunkEmbedding.Status.ACTIVE),
            tenant,
        ).values_list("chunk_id", flat=True)
    )
    missing = [chunk for chunk in active_chunks.order_by("-updated_at")[:500] if chunk.id not in embedded_ids]
    paginator = Paginator(missing, page_size)
    page_obj = paginator.get_page(page)
    rows = []
    for chunk in page_obj.object_list:
        manifest = chunk.manifest
        rows.append(
            {
                "tenant": chunk.tenant.slug,
                "chunk_id": chunk.id,
                "document_title": getattr(manifest, "name", None) or f"chunk:{chunk.id}",
                "source": getattr(manifest, "relative_path", "") or getattr(manifest, "drive_file_id", "") or "",
                "updated_at": chunk.updated_at,
            }
        )
    return {"page_obj": page_obj, "rows": rows, "total": len(missing)}


def build_conversation_quality_list(
    *,
    tenant=None,
    period: QualityPeriod,
    intent: str | None = None,
    lead_status: str | None = None,
    handoff_status: str | None = None,
    source_page: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict:
    qs = (
        _scope(Conversation.objects.select_related("tenant", "lead_draft"), tenant)
        .filter(updated_at__gte=period.start, updated_at__lt=period.end)
        .annotate(
            message_count=Count("messages", distinct=True),
            last_message_at=Max("messages__created_at"),
        )
        .prefetch_related(
            Prefetch(
                "handoff_requests",
                queryset=HandoffRequest.objects.order_by("-created_at"),
                to_attr="prefetched_handoffs",
            )
        )
        .order_by("-updated_at")
    )
    if source_page:
        qs = qs.filter(source_page__icontains=source_page)
    if lead_status:
        qs = qs.filter(lead_draft__status=lead_status)
    if handoff_status:
        qs = qs.filter(handoff_requests__status=handoff_status)
    if intent:
        conv_ids = (
            _scope(ChatRequest.objects.all(), tenant)
            .filter(created_at__gte=period.start, created_at__lt=period.end, response_payload__intent=intent)
            .values_list("conversation_id", flat=True)
            .distinct()
        )
        qs = qs.filter(pk__in=conv_ids)

    paginator = Paginator(qs.distinct(), page_size)
    page_obj = paginator.get_page(page)
    rows = []
    for conversation in page_obj.object_list:
        latest_intent = (
            ChatRequest.objects.filter(conversation=conversation, status=ChatRequest.Status.COMPLETED)
            .order_by("-created_at")
            .values_list("response_payload", flat=True)
            .first()
        )
        handoffs = getattr(conversation, "prefetched_handoffs", []) or []
        latest_handoff = handoffs[0] if handoffs else None
        rows.append(
            {
                "id": conversation.pk,
                "tenant": conversation.tenant.slug,
                "session_id": conversation.session_id,
                "started_at": conversation.created_at,
                "last_message_at": conversation.last_message_at,
                "message_count": conversation.message_count,
                "intent": (latest_intent or {}).get("intent", ""),
                "lead_status": getattr(getattr(conversation, "lead_draft", None), "status", ""),
                "handoff_status": getattr(latest_handoff, "status", ""),
                "source_page": conversation.source_page,
                "lead_state": conversation.lead_state,
            }
        )
    return {"page_obj": page_obj, "rows": rows}


def build_conversation_rag_debug(*, conversation) -> list[dict]:
    events = list(
        RagRetrievalEvent.objects.filter(tenant=conversation.tenant, conversation_id=conversation.pk).order_by("created_at")[:100]
    )
    chat_obs = []
    for payload, created_at in ChatRequest.objects.filter(conversation=conversation).order_by("created_at").values_list(
        "response_payload", "created_at"
    )[:100]:
        obs = (payload or {}).get("observability") or {}
        if obs:
            chat_obs.append({"created_at": created_at, **obs})
    return {"retrieval_events": events, "message_observability": chat_obs}


def build_visual_alerts(*, fallback, rag, errors, outbox, corpus, drive, latency, chat_requests) -> list[dict]:
    alerts = []
    def add(status, title, detail):
        alerts.append({"status": status, "tone": tone_for_status(status), "title": title, "detail": detail})

    if fallback.get("rate") is not None and fallback["rate"] >= FALLBACK_RATE_WARNING:
        add(fallback["status"], "Fallback elevado", f"Taxa {_format_rate(fallback['rate'])}")
    if rag.get("hit_rate") is not None and rag["hit_rate"] <= RAG_HIT_RATE_WARNING:
        add(rag["status"], "RAG hit rate baixo", f"Taxa {_format_rate(rag['hit_rate'])}")
    if outbox.get("dead_letter", 0) > 0:
        add("degraded", "Dead letters", f"{outbox['dead_letter']} evento(s)")
    if corpus.get("embeddings_pending", 0) >= EMBEDDINGS_PENDING_WARNING:
        add("warning", "Embeddings pendentes", f"{corpus['embeddings_pending']} chunk(s)")
    if drive.get("status") == "degraded":
        add("degraded", "Drive sync falhou", drive.get("summary_hint", ""))
    if latency.get("status") in {"warning", "degraded"}:
        add(latency["status"], "Latência alta", f"P95={latency['p95_ms']}ms")
    if chat_requests.get("stuck_processing", 0) > 0:
        add("degraded", "ChatRequests presos", f"{chat_requests['stuck_processing']} em processing")
    if errors.get("status") in {"warning", "degraded"}:
        add(errors["status"], "Erros de chat", f"failed={errors['chat_failed']}")
    if not alerts:
        add("green", "Operação saudável", "Nenhum alerta acima dos thresholds")
    return alerts


def build_tenant_quality_score(*, fallback, rag, errors, outbox, corpus, drive, emails) -> dict:
    """Score 0–100 transparente, sem IA."""
    components = {}

    chat_health = 100.0
    if errors.get("error_rate"):
        chat_health = max(0.0, 100.0 - (errors["error_rate"] * 400))
    components["chat_health"] = round(chat_health, 1)

    fb_rate = fallback.get("rate")
    if fb_rate is None:
        components["fallback"] = 100.0
    else:
        components["fallback"] = round(max(0.0, 100.0 - (fb_rate * 200)), 1)

    hit = rag.get("hit_rate")
    if hit is None:
        components["rag_hit"] = 70.0
    else:
        components["rag_hit"] = round(hit * 100, 1)

    avg_score = float(rag.get("avg_max_score") or 0)
    components["retrieval_quality"] = round(min(100.0, avg_score * 100), 1)

    if drive.get("status") == "degraded":
        components["sync_health"] = 20.0
    elif drive.get("status") == "warning":
        components["sync_health"] = 60.0
    else:
        components["sync_health"] = 100.0

    sent = emails.get("sent", 0)
    failed = emails.get("failed", 0)
    if sent + failed == 0:
        components["notification_health"] = 80.0
    else:
        components["notification_health"] = round(100.0 * sent / max(1, sent + failed), 1)

    components["dead_letters"] = 0.0 if outbox.get("dead_letter", 0) > 0 else 100.0

    if corpus.get("embeddings_pending", 0) > 0:
        components["retrieval_quality"] = round(components["retrieval_quality"] * 0.8, 1)

    total_weight = sum(QUALITY_SCORE_WEIGHTS.values())
    score = 0.0
    for key, weight in QUALITY_SCORE_WEIGHTS.items():
        score += components.get(key, 0.0) * weight
    score = round(score / total_weight, 1)
    if score >= 80:
        status = "green"
    elif score >= 60:
        status = "warning"
    else:
        status = "degraded"
    return {
        "score": score,
        "components": components,
        "weights": QUALITY_SCORE_WEIGHTS,
        "status": status,
        "tone": tone_for_status(status),
    }


def build_tenant_quality_detail(*, tenant, period: QualityPeriod) -> dict:
    dashboard = build_quality_dashboard(tenant=tenant, period=period)
    gaps = build_knowledge_gaps(tenant=tenant, period=period, page=1, page_size=10)
    docs_missing = build_documents_without_embedding(tenant=tenant, page=1, page_size=10)
    conversations = build_conversation_quality_list(tenant=tenant, period=period, page=1, page_size=10)
    return {
        **dashboard,
        "knowledge_gaps_preview": list(gaps["page_obj"].object_list),
        "knowledge_gap_clusters": gaps["clusters"][:8],
        "documents_missing_preview": docs_missing["rows"],
        "conversations_preview": conversations["rows"],
        "tenant": tenant,
    }
