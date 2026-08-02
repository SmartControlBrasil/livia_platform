from __future__ import annotations

import logging

from django.db import DatabaseError, OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def record_ai_usage(
    *,
    tenant,
    operation: str,
    model: str,
    success: bool,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    error_type: str = "",
    metadata: dict | None = None,
) -> None:
    """Persiste telemetria mínima de IA; falhas não interrompem o chat."""
    try:
        from assistant_core.models import AiUsageEvent

        AiUsageEvent.objects.create(
            tenant=tenant,
            operation=str(operation or "")[:40],
            model=str(model or "")[:80],
            success=bool(success),
            prompt_tokens=max(int(prompt_tokens or 0), 0),
            completion_tokens=max(int(completion_tokens or 0), 0),
            total_tokens=max(int(total_tokens or 0), 0),
            latency_ms=max(int(latency_ms or 0), 0),
            error_type=str(error_type or "")[:80],
            metadata=dict(metadata or {}),
        )
    except (ProgrammingError, OperationalError, DatabaseError):
        logger.debug("ai.telemetry.skipped reason=schema_unavailable operation=%s", operation)
    except Exception:  # noqa: BLE001
        logger.exception("ai.telemetry.failed operation=%s", operation)
