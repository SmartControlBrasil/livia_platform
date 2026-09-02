"""Thresholds operacionais do painel Qualidade da Lívia (sem IA)."""

from __future__ import annotations

# Status: green | warning | degraded
FALLBACK_RATE_WARNING = 0.20
FALLBACK_RATE_DEGRADED = 0.40

RAG_HIT_RATE_WARNING = 0.40
RAG_HIT_RATE_DEGRADED = 0.20

DEAD_LETTER_DEGRADED = 1  # qualquer dead letter > 0

EMBEDDINGS_PENDING_WARNING = 1

CHAT_ERROR_RATE_WARNING = 0.05
CHAT_ERROR_RATE_DEGRADED = 0.15

PROCESSING_STUCK_MINUTES = 5

LATENCY_P95_WARNING_MS = 5000
LATENCY_P95_DEGRADED_MS = 15000

QUALITY_SCORE_WEIGHTS = {
    "chat_health": 20,
    "fallback": 20,
    "rag_hit": 20,
    "retrieval_quality": 10,
    "sync_health": 15,
    "notification_health": 10,
    "dead_letters": 5,
}


def status_from_rate(*, rate: float | None, warning: float, degraded: float, higher_is_worse: bool = True) -> str:
    if rate is None:
        return "green"
    if higher_is_worse:
        if rate >= degraded:
            return "degraded"
        if rate >= warning:
            return "warning"
        return "green"
    if rate <= degraded:
        return "degraded"
    if rate <= warning:
        return "warning"
    return "green"


def tone_for_status(status: str) -> str:
    return {
        "green": "success",
        "warning": "warning",
        "degraded": "danger",
    }.get(status, "secondary")
