from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from knowledge_base.models import TenantRagChunkEmbedding
from knowledge_base.rag.embeddings import (
    EmbeddingConfig,
    EmbeddingProvider,
    build_embedding_provider,
    cosine_similarity,
    load_embedding_config,
)
from tenants.models import Tenant


class TenantRagAdminSearchError(Exception):
    pass


@dataclass(frozen=True)
class AdminSearchHit:
    embedding_id: int
    chunk_id: int
    manifest_id: int
    score: float
    chunk_sha256: str
    provider: str
    model: str
    dimension: int


def admin_vector_search(
    *,
    tenant: Tenant | None,
    query_text: str,
    limit: int = 10,
    provider: EmbeddingProvider | None = None,
    config: EmbeddingConfig | None = None,
) -> list[AdminSearchHit]:
    """
    Busca vetorial administrativa isolada por tenant.

    Segurança: o conjunto candidato é filtrado pelo tenant ANTES do cálculo
    de similaridade. O fallback em memória (cosseno) é apenas para
    desenvolvimento/testes (SQLite) e não é solução de produção.
    """
    if tenant is None:
        raise TenantRagAdminSearchError("tenant is required for administrative vector search.")

    text = (query_text or "").strip()
    if not text:
        raise TenantRagAdminSearchError("query_text is required.")

    max_results = int(limit or 0)
    if max_results <= 0:
        raise TenantRagAdminSearchError("limit must be a positive integer.")
    hard_cap = int(getattr(settings, "LIVIA_RAG_ADMIN_SEARCH_MAX_RESULTS", 20) or 0)
    if hard_cap <= 0:
        raise TenantRagAdminSearchError("LIVIA_RAG_ADMIN_SEARCH_MAX_RESULTS must be a positive integer.")
    max_results = min(max_results, hard_cap)

    cfg = config or load_embedding_config()
    embedder = provider or build_embedding_provider(cfg)
    query_vector = embedder.embed_texts([text], config=cfg)[0]

    candidates = list(
        TenantRagChunkEmbedding.objects.filter(
            tenant=tenant,
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            embedding_config_signature=cfg.signature,
            dimension=cfg.dimension,
        )
        .only(
            "id",
            "chunk_id",
            "manifest_id",
            "vector",
            "chunk_sha256",
            "provider",
            "model",
            "dimension",
        )
        .order_by("id")
    )

    scored: list[AdminSearchHit] = []
    for item in candidates:
        vector = item.vector or []
        if not isinstance(vector, list) or len(vector) != cfg.dimension:
            continue
        score = cosine_similarity(query_vector, vector)
        scored.append(
            AdminSearchHit(
                embedding_id=item.id,
                chunk_id=item.chunk_id,
                manifest_id=item.manifest_id,
                score=score,
                chunk_sha256=item.chunk_sha256,
                provider=item.provider,
                model=item.model,
                dimension=item.dimension,
            )
        )

    scored.sort(key=lambda hit: (-hit.score, hit.chunk_id, hit.embedding_id))
    return scored[:max_results]
