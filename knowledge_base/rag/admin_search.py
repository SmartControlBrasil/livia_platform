from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from knowledge_base.rag.embeddings import (
    EmbeddingConfig,
    EmbeddingProvider,
    build_embedding_provider,
    load_embedding_config,
)
from knowledge_base.rag.vector_search import get_vector_search_backend
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

    Delega ao backend (pgvector no PostgreSQL; in-memory no SQLite).
    O tenant restringe o conjunto candidato antes da similaridade.
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
    if len(query_vector) != cfg.dimension:
        raise TenantRagAdminSearchError(
            f"Query embedding dimension {len(query_vector)} != configured {cfg.dimension}."
        )

    backend = get_vector_search_backend()
    hits = backend.search_similar_chunks(
        tenant=tenant,
        query_vector=query_vector,
        config=cfg,
        limit=max_results,
    )
    return [
        AdminSearchHit(
            embedding_id=hit.embedding.id,
            chunk_id=hit.embedding.chunk_id,
            manifest_id=hit.embedding.manifest_id,
            score=hit.score,
            chunk_sha256=hit.embedding.chunk_sha256,
            provider=hit.embedding.provider,
            model=hit.embedding.model,
            dimension=hit.embedding.dimension,
        )
        for hit in hits
    ]
