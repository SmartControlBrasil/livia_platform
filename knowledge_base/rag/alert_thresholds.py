from django.conf import settings


def retrieval_min_executed() -> int:
    return max(1, int(getattr(settings, "LIVIA_RAG_ALERT_RETRIEVAL_MIN_EXECUTED", 10)))


def retrieval_empty_rate_threshold() -> float:
    return float(getattr(settings, "LIVIA_RAG_ALERT_RETRIEVAL_EMPTY_RATE", 0.8))


def ai_failure_min_count() -> int:
    return max(1, int(getattr(settings, "LIVIA_RAG_ALERT_AI_FAILURE_MIN", 3)))


def token_usage_warning_threshold() -> int:
    return max(1, int(getattr(settings, "LIVIA_RAG_ALERT_TOKEN_WARNING", 50000)))


def operation_failed_window_days() -> int:
    return max(1, int(getattr(settings, "LIVIA_RAG_ALERT_OPERATION_FAILED_WINDOW_DAYS", 7)))
