from __future__ import annotations

import logging

from knowledge_base.models import RagRetrievalEvent

logger = logging.getLogger(__name__)


def record_retrieval_event(
    *,
    tenant,
    conversation=None,
    status: str,
    reason: str = "",
    backend: str = "",
    provider: str = "",
    model: str = "",
    duration_ms: int = 0,
    candidate_count: int = 0,
    result_count: int = 0,
    max_score: float = 0.0,
    threshold: float = 0.0,
    threshold_source: str = "global_default",
    dry_run: bool = False,
) -> RagRetrievalEvent | None:
    """
    Persiste metrica operacional de retrieval.

    Nao armazena pergunta, documento, embedding ou segredo.
    Falha de metrica nao deve derrubar o chat.
    """
    if tenant is None:
        return None
    hit = status == RagRetrievalEvent.Status.COMPLETED and int(result_count or 0) > 0
    try:
        return RagRetrievalEvent.objects.create(
            tenant=tenant,
            conversation_id=getattr(conversation, "id", None),
            status=status,
            reason=str(reason or "")[:80],
            backend=str(backend or "")[:40],
            provider=str(provider or "")[:40],
            model=str(model or "")[:120],
            duration_ms=max(0, int(duration_ms or 0)),
            candidate_count=max(0, int(candidate_count or 0)),
            result_count=max(0, int(result_count or 0)),
            max_score=float(max_score or 0.0),
            threshold=float(threshold or 0.0),
            threshold_source=str(threshold_source or "global_default")[:30],
            dry_run=bool(dry_run),
            hit=hit,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "rag.retrieval.metric_failed tenant_id=%s status=%s",
            getattr(tenant, "id", None),
            status,
        )
        return None
