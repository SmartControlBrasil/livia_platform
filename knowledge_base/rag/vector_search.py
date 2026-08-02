from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from knowledge_base.models import TenantRagChunkEmbedding
from knowledge_base.rag.embeddings import EmbeddingConfig, cosine_similarity

logger = logging.getLogger(__name__)

BACKEND_IN_MEMORY = "in_memory"
BACKEND_POSTGRES_PGVECTOR = "postgres_pgvector"


class RagVectorSearchError(Exception):
    pass


@dataclass(frozen=True)
class VectorSearchHit:
    embedding: TenantRagChunkEmbedding
    score: float
    distance: float | None = None


class RagVectorSearchBackend(ABC):
    name: str

    @abstractmethod
    def search_similar_chunks(
        self,
        *,
        tenant,
        query_vector: list[float],
        config: EmbeddingConfig,
        limit: int,
    ) -> list[VectorSearchHit]:
        raise NotImplementedError


class InMemoryVectorSearchBackend(RagVectorSearchBackend):
    """Backend de desenvolvimento/testes (SQLite). Nao e solucao de producao."""

    name = BACKEND_IN_MEMORY

    def search_similar_chunks(
        self,
        *,
        tenant,
        query_vector: list[float],
        config: EmbeddingConfig,
        limit: int,
    ) -> list[VectorSearchHit]:
        if tenant is None:
            raise RagVectorSearchError("tenant is required for vector search.")
        if len(query_vector) != config.dimension:
            raise RagVectorSearchError(
                f"Query vector dimension {len(query_vector)} != configured {config.dimension}."
            )

        candidates = list(
            TenantRagChunkEmbedding.objects.filter(
                tenant=tenant,
                is_active=True,
                status=TenantRagChunkEmbedding.Status.ACTIVE,
                embedding_config_signature=config.signature,
                dimension=config.dimension,
                provider=config.provider,
                model=config.model,
            )
            .select_related("chunk", "manifest")
            .order_by("id")
        )

        scored: list[VectorSearchHit] = []
        for item in candidates:
            vector = item.vector or []
            if not isinstance(vector, list) or len(vector) != config.dimension:
                continue
            score = cosine_similarity(query_vector, vector)
            scored.append(VectorSearchHit(embedding=item, score=score, distance=1.0 - score))

        scored.sort(key=lambda hit: (-hit.score, hit.embedding.chunk_id, hit.embedding.id))
        return scored[: max(1, int(limit))]


class PostgresPgvectorSearchBackend(RagVectorSearchBackend):
    """
    Recuperacao nativa no PostgreSQL via pgvector.

    Semantica:
    - distance (<=> cosine distance): menor = melhor
    - score = 1 - distance: maior = melhor
    """

    name = BACKEND_POSTGRES_PGVECTOR

    def search_similar_chunks(
        self,
        *,
        tenant,
        query_vector: list[float],
        config: EmbeddingConfig,
        limit: int,
    ) -> list[VectorSearchHit]:
        if tenant is None:
            raise RagVectorSearchError("tenant is required for vector search.")
        if connection.vendor != "postgresql":
            raise RagVectorSearchError("postgres_pgvector backend requires PostgreSQL.")
        if len(query_vector) != config.dimension:
            raise RagVectorSearchError(
                f"Query vector dimension {len(query_vector)} != configured {config.dimension}."
            )

        try:
            from pgvector.django import CosineDistance
        except ImportError as exc:  # pragma: no cover - dependency boundary
            raise RagVectorSearchError("pgvector package is not installed.") from exc

        # Tenant e versao/config entram no filtro ORM ANTES da ordenacao vetorial.
        queryset = (
            TenantRagChunkEmbedding.objects.filter(
                tenant=tenant,
                is_active=True,
                status=TenantRagChunkEmbedding.Status.ACTIVE,
                embedding_config_signature=config.signature,
                dimension=config.dimension,
                provider=config.provider,
                model=config.model,
            )
            .annotate(distance=CosineDistance("vector", query_vector))
            .select_related("chunk", "manifest")
            .order_by("distance", "chunk_id", "id")[: max(1, int(limit))]
        )

        hits: list[VectorSearchHit] = []
        for item in queryset:
            distance = float(getattr(item, "distance", 0.0) or 0.0)
            score = max(0.0, min(1.0, 1.0 - distance))
            hits.append(VectorSearchHit(embedding=item, score=score, distance=distance))
        return hits


def resolve_vector_backend_name() -> str:
    configured = str(getattr(settings, "LIVIA_RAG_VECTOR_BACKEND", "auto") or "auto").strip().lower()
    if configured in {BACKEND_IN_MEMORY, BACKEND_POSTGRES_PGVECTOR, "auto"}:
        return configured
    raise RagVectorSearchError(
        "LIVIA_RAG_VECTOR_BACKEND must be one of: auto, in_memory, postgres_pgvector."
    )


def pgvector_extension_available() -> bool:
    if connection.vendor != "postgresql":
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            return cursor.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def get_vector_search_backend(name: str | None = None) -> RagVectorSearchBackend:
    selected = (name or resolve_vector_backend_name()).strip().lower()
    if selected == "auto":
        if connection.vendor == "postgresql" and pgvector_extension_available():
            return PostgresPgvectorSearchBackend()
        return InMemoryVectorSearchBackend()
    if selected == BACKEND_IN_MEMORY:
        return InMemoryVectorSearchBackend()
    if selected == BACKEND_POSTGRES_PGVECTOR:
        if connection.vendor != "postgresql":
            raise RagVectorSearchError("postgres_pgvector backend requires PostgreSQL connection.")
        if not pgvector_extension_available():
            raise RagVectorSearchError("PostgreSQL extension 'vector' is not available.")
        return PostgresPgvectorSearchBackend()
    raise RagVectorSearchError(f"Unsupported vector backend: {selected}")
