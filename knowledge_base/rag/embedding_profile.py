from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.db import connection

from knowledge_base.models import TenantRagChunkEmbedding
from knowledge_base.rag.embeddings import EmbeddingConfig, EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.vector_field import configured_embedding_dimensions


class EmbeddingOperationalState(str, Enum):
    """Estado calculado de um embedding em relação ao profile ativo."""

    CURRENT = "current"
    STALE = "stale"
    REINDEX_REQUIRED = "reindex_required"
    INVALID = "invalid"


@dataclass(frozen=True)
class EmbeddingProfile:
    """Profile efetivo de embeddings (fonte única após settings)."""

    provider: str
    model: str
    dimension: int
    signature: str
    batch_size: int
    schema_vector_dimension: int | None

    @property
    def profile_key(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimension}"

    @property
    def schema_matches(self) -> bool:
        if self.schema_vector_dimension is None:
            return True
        return int(self.schema_vector_dimension) == int(self.dimension)


def database_vector_column_dimension() -> int | None:
    """
    Dimensão tipada da coluna vector no banco.

    PostgreSQL: atttypmod de vector(n).
    SQLite: None (coluna JSON; dimensão vem do profile/settings).
    """
    if connection.vendor != "postgresql":
        return None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.atttypmod
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_type t ON t.oid = a.atttypid
                WHERE c.relname = 'knowledge_base_tenantragchunkembedding'
                  AND a.attname = 'vector'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND t.typname = 'vector'
                """
            )
            row = cursor.fetchone()
            if not row or row[0] is None or int(row[0]) < 0:
                return None
            return int(row[0])
    except Exception:  # noqa: BLE001
        return None


def ensure_profile_schema_compatible(profile: EmbeddingProfile) -> None:
    if profile.schema_vector_dimension is None:
        return
    if not profile.schema_matches:
        raise EmbeddingConfigurationError(
            "Embedding profile dimension "
            f"{profile.dimension} does not match PostgreSQL vector column dimension "
            f"{profile.schema_vector_dimension}. "
            "Changing LIVIA_RAG_EMBEDDING_DIMENSION alone is insufficient; "
            "apply a migration and reindex embeddings."
        )


def ensure_config_schema_compatible(config: EmbeddingConfig) -> None:
    schema_dim = database_vector_column_dimension()
    if schema_dim is None:
        return
    if int(schema_dim) != int(config.dimension):
        raise EmbeddingConfigurationError(
            f"Configured embedding dimension {config.dimension} != database vector({schema_dim})."
        )


def load_embedding_profile(*, validate_schema: bool = True) -> EmbeddingProfile:
    from django.conf import settings as django_settings

    cfg = load_embedding_config()
    schema_dim = database_vector_column_dimension()
    profile = EmbeddingProfile(
        provider=cfg.provider,
        model=cfg.model,
        dimension=cfg.dimension,
        signature=cfg.signature,
        batch_size=cfg.batch_size,
        schema_vector_dimension=schema_dim,
    )
    if validate_schema and not getattr(django_settings, "RUNNING_TESTS", False):
        ensure_profile_schema_compatible(profile)
    return profile


def embedding_config_from_profile(profile: EmbeddingProfile) -> EmbeddingConfig:
    """Reconstrói EmbeddingConfig completo a partir do profile (settings atuais)."""
    return load_embedding_config()


def classify_embedding(
    embedding: TenantRagChunkEmbedding,
    *,
    profile: EmbeddingProfile,
) -> EmbeddingOperationalState:
    if not embedding.is_active or embedding.status != TenantRagChunkEmbedding.Status.ACTIVE:
        return EmbeddingOperationalState.INVALID
    vector = embedding.vector
    if vector is None or (isinstance(vector, list) and len(vector) == 0):
        return EmbeddingOperationalState.INVALID
    if isinstance(vector, list) and len(vector) != profile.dimension:
        return EmbeddingOperationalState.INVALID
    if (
        embedding.provider == profile.provider
        and embedding.model == profile.model
        and int(embedding.dimension) == profile.dimension
        and embedding.embedding_config_signature == profile.signature
    ):
        return EmbeddingOperationalState.CURRENT
    if (
        embedding.provider == profile.provider
        and embedding.model == profile.model
        and int(embedding.dimension) == profile.dimension
    ):
        return EmbeddingOperationalState.STALE
    return EmbeddingOperationalState.REINDEX_REQUIRED


@dataclass(frozen=True)
class TenantEmbeddingHealth:
    tenant_slug: str
    profile: EmbeddingProfile
    total: int
    compatible: int
    null_vectors: int
    wrong_model: int
    wrong_dimension: int
    wrong_signature: int
    inactive: int
    stale: int
    reindex_required: int
    invalid: int

    @property
    def status_label(self) -> str:
        if self.reindex_required > 0 or self.invalid > 0:
            return "REINDEX_REQUIRED"
        if self.stale > 0:
            return "STALE"
        return "OK"

    @property
    def incompatible(self) -> int:
        return self.total - self.compatible - self.inactive


def inspect_tenant_embedding_health(*, tenant) -> TenantEmbeddingHealth:
    profile = load_embedding_profile(validate_schema=True)
    qs = TenantRagChunkEmbedding.objects.filter(tenant=tenant)
    total = qs.count()
    compatible = 0
    null_vectors = 0
    wrong_model = 0
    wrong_dimension = 0
    wrong_signature = 0
    inactive = 0
    stale = 0
    reindex_required = 0
    invalid = 0

    for emb in qs.iterator():
        if not emb.is_active or emb.status != TenantRagChunkEmbedding.Status.ACTIVE:
            inactive += 1
            continue
        vector = emb.vector
        if vector is None or (isinstance(vector, list) and len(vector) == 0):
            null_vectors += 1
            invalid += 1
            continue
        if isinstance(vector, list) and len(vector) != profile.dimension:
            wrong_dimension += 1
            invalid += 1
            continue
        if emb.provider != profile.provider or emb.model != profile.model:
            wrong_model += 1
            reindex_required += 1
            continue
        if int(emb.dimension) != profile.dimension:
            wrong_dimension += 1
            reindex_required += 1
            continue
        if emb.embedding_config_signature != profile.signature:
            wrong_signature += 1
            stale += 1
            continue
        compatible += 1

    return TenantEmbeddingHealth(
        tenant_slug=tenant.slug,
        profile=profile,
        total=total,
        compatible=compatible,
        null_vectors=null_vectors,
        wrong_model=wrong_model,
        wrong_dimension=wrong_dimension,
        wrong_signature=wrong_signature,
        inactive=inactive,
        stale=stale,
        reindex_required=reindex_required,
        invalid=invalid,
    )


def indexable_active_chunks_count(*, tenant) -> int:
    from knowledge_base.models import TenantRagDocumentChunk

    return TenantRagDocumentChunk.objects.filter(
        tenant=tenant,
        is_active=True,
        status=TenantRagDocumentChunk.Status.ACTIVE,
    ).count()


def embedding_coverage_ratio(*, tenant, profile: EmbeddingProfile | None = None) -> float:
    breakdown = embedding_coverage_breakdown(tenant=tenant, profile=profile)
    return float(breakdown["coverage"])


def embedding_coverage_breakdown(*, tenant, profile: EmbeddingProfile | None = None) -> dict[str, int | float]:
    from knowledge_base.models import TenantRagDocumentChunk

    prof = profile or load_embedding_profile(validate_schema=False)
    chunk_ids = list(
        TenantRagDocumentChunk.objects.filter(
            tenant=tenant,
            is_active=True,
            status=TenantRagDocumentChunk.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    indexable = len(chunk_ids)
    if indexable <= 0:
        return {
            "indexable_chunks": 0,
            "compatible": 0,
            "missing_embedding": 0,
            "incompatible_embedding": 0,
            "stale": 0,
            "coverage": 0.0,
        }

    compatible_ids: set[int] = set()
    stale_ids: set[int] = set()
    incompatible_ids: set[int] = set()

    for emb in TenantRagChunkEmbedding.objects.filter(
        tenant=tenant,
        chunk_id__in=chunk_ids,
        is_active=True,
        status=TenantRagChunkEmbedding.Status.ACTIVE,
    ).iterator():
        state = classify_embedding(emb, profile=prof)
        cid = int(emb.chunk_id)
        if state == EmbeddingOperationalState.CURRENT:
            compatible_ids.add(cid)
        elif state == EmbeddingOperationalState.STALE:
            stale_ids.add(cid)
        else:
            incompatible_ids.add(cid)

    chunks_with_any_embedding: set[int] = compatible_ids | stale_ids | incompatible_ids
    missing_embedding = indexable - len(chunks_with_any_embedding)
    incompatible_embedding = len(stale_ids | incompatible_ids)
    compatible = len(compatible_ids)

    return {
        "indexable_chunks": indexable,
        "compatible": compatible,
        "missing_embedding": missing_embedding,
        "incompatible_embedding": incompatible_embedding,
        "stale": len(stale_ids),
        "coverage": compatible / indexable,
    }


def configured_embedding_dimensions_for_schema() -> int:
    """Alias documentado: dimensão esperada pelo profile/settings."""
    return configured_embedding_dimensions()
