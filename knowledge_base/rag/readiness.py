from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from knowledge_base.models import TenantRagChunkEmbedding
from knowledge_base.rag.embeddings import load_embedding_config
from knowledge_base.rag.embedding_profile import load_embedding_profile
from knowledge_base.rag.vector_search import (
    BACKEND_IN_MEMORY,
    BACKEND_POSTGRES_PGVECTOR,
    get_vector_search_backend,
    pgvector_extension_available,
    resolve_vector_backend_name,
)


VECTOR_HNSW_INDEX = "knowledge_base_rag_embedding_hnsw_cosine"


@dataclass(frozen=True)
class RagReadinessCheck:
    code: str
    ok: bool
    detail: str


def inspect_rag_vector_readiness() -> list[RagReadinessCheck]:
    """Inspecao readonly de readiness RAG/pgvector. Nao altera o banco."""
    checks: list[RagReadinessCheck] = []
    vendor = connection.vendor
    checks.append(
        RagReadinessCheck(
            code="database_vendor",
            ok=vendor in {"postgresql", "sqlite"},
            detail=f"vendor={vendor}",
        )
    )

    configured_backend = resolve_vector_backend_name()
    checks.append(
        RagReadinessCheck(
            code="vector_backend_setting",
            ok=True,
            detail=f"LIVIA_RAG_VECTOR_BACKEND={configured_backend}",
        )
    )

    if vendor != "postgresql":
        checks.append(
            RagReadinessCheck(
                code="pgvector_extension",
                ok=True,
                detail="not_applicable_on_sqlite",
            )
        )
        checks.append(
            RagReadinessCheck(
                code="active_backend",
                ok=True,
                detail=BACKEND_IN_MEMORY,
            )
        )
        return checks

    checks.append(
        RagReadinessCheck(
            code="postgresql_detected",
            ok=True,
            detail="PostgreSQL detected",
        )
    )

    extension_ok = pgvector_extension_available()
    extension_version = ""
    if extension_ok:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cursor.fetchone()
            extension_version = str(row[0]) if row else ""
    checks.append(
        RagReadinessCheck(
            code="pgvector_extension",
            ok=extension_ok,
            detail=f"extversion={extension_version or 'missing'}",
        )
    )

    dimensions = int(getattr(settings, "LIVIA_RAG_EMBEDDING_DIMENSION", 1536) or 1536)
    vector_udt = ""
    vector_typmod = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.typname, a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_type t ON t.oid = a.atttypid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'knowledge_base_tenantragchunkembedding'
                  AND a.attname = 'vector'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """
            )
            row = cursor.fetchone()
            if row:
                vector_udt = str(row[0])
                vector_typmod = str(row[1])
    except Exception as exc:  # noqa: BLE001
        checks.append(
            RagReadinessCheck(
                code="vector_column",
                ok=False,
                detail=f"inspect_failed:{exc.__class__.__name__}",
            )
        )
    else:
        schema_dim = None
        if vector_udt == "vector" and vector_typmod and str(vector_typmod).lstrip("-").isdigit():
            parsed = int(vector_typmod)
            if parsed > 0:
                schema_dim = parsed
        dimension_match = schema_dim is None or schema_dim == dimensions
        checks.append(
            RagReadinessCheck(
                code="vector_column",
                ok=vector_udt == "vector" and dimension_match,
                detail=(
                    f"type={vector_udt or 'missing'} typmod={vector_typmod or 'n/a'} "
                    f"configured_dimensions={dimensions} schema_dimension={schema_dim or 'n/a'}"
                ),
            )
        )

    index_present = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM pg_indexes
                WHERE indexname = %s
                """,
                [VECTOR_HNSW_INDEX],
            )
            index_present = cursor.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        checks.append(
            RagReadinessCheck(
                code="vector_index",
                ok=False,
                detail=f"inspect_failed:{exc.__class__.__name__}",
            )
        )
    else:
        checks.append(
            RagReadinessCheck(
                code="vector_index",
                ok=index_present,
                detail=f"index={VECTOR_HNSW_INDEX} present={index_present}",
            )
        )

    try:
        profile = load_embedding_profile(validate_schema=True)
        cfg = load_embedding_config()
        checks.append(
            RagReadinessCheck(
                code="embedding_profile",
                ok=True,
                detail=f"profile={profile.profile_key} signature={profile.signature[:12]}...",
            )
        )
        checks.append(
            RagReadinessCheck(
                code="embedding_config",
                ok=True,
                detail=f"provider={cfg.provider} model={cfg.model} dimension={cfg.dimension}",
            )
        )
        indexed = TenantRagChunkEmbedding.objects.filter(
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            provider=cfg.provider,
            model=cfg.model,
            dimension=cfg.dimension,
            embedding_config_signature=cfg.signature,
        ).count()
        incompatible = TenantRagChunkEmbedding.objects.filter(
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
        ).exclude(
            provider=cfg.provider,
            model=cfg.model,
            dimension=cfg.dimension,
            embedding_config_signature=cfg.signature,
        ).count()
        checks.append(
            RagReadinessCheck(
                code="indexed_embeddings",
                ok=incompatible == 0,
                detail=f"compatible_active_embeddings={indexed} incompatible_active_embeddings={incompatible}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            RagReadinessCheck(
                code="embedding_profile",
                ok=False,
                detail=f"invalid:{exc.__class__.__name__}:{str(exc)[:120]}",
            )
        )

    try:
        backend = get_vector_search_backend()
        checks.append(
            RagReadinessCheck(
                code="active_backend",
                ok=backend.name in {BACKEND_IN_MEMORY, BACKEND_POSTGRES_PGVECTOR},
                detail=backend.name,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            RagReadinessCheck(
                code="active_backend",
                ok=False,
                detail=f"resolve_failed:{exc.__class__.__name__}",
            )
        )

    return checks
