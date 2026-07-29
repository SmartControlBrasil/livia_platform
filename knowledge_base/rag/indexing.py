from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from knowledge_base.models import (
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagIndexRun,
)
from knowledge_base.rag.embeddings import (
    EmbeddingConfig,
    EmbeddingConfigurationError,
    EmbeddingProvider,
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    build_embedding_provider,
    load_embedding_config,
    sanitize_embedding_error,
)
from tenants.models import Tenant

logger = logging.getLogger(__name__)


class TenantRagIndexingError(Exception):
    pass


@dataclass
class IndexCounters:
    documents: int = 0
    chunks: int = 0
    pending: int = 0
    indexed: int = 0
    reindexed: int = 0
    unchanged: int = 0
    deactivated: int = 0
    skipped: int = 0
    failed: int = 0
    batches: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "pending": self.pending,
            "indexed": self.indexed,
            "reindexed": self.reindexed,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "skipped": self.skipped,
            "failed": self.failed,
            "batches": self.batches,
        }


@dataclass(frozen=True)
class IndexOutcome:
    mode: str
    status: str
    run_id: str
    dry_run: bool
    counters: IndexCounters
    provider: str
    model: str
    dimension: int
    embedding_config_signature: str


@dataclass
class _PendingItem:
    chunk: TenantRagDocumentChunk
    action: str  # index | reindex
    previous: TenantRagChunkEmbedding | None = None


def acquire_tenant_index_lock(*, tenant: Tenant, mode: str, run_id: str) -> TenantRagConfiguration:
    timeout_seconds = int(getattr(settings, "LIVIA_RAG_INDEX_RUNNING_TIMEOUT_SECONDS", 1800) or 1800)
    timeout_seconds = max(timeout_seconds, 60)
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=timeout_seconds)

    with transaction.atomic():
        configuration = (
            TenantRagConfiguration.objects.select_for_update()
            .select_related("tenant")
            .filter(tenant=tenant)
            .first()
        )
        if configuration is None:
            raise TenantRagIndexingError("Tenant RAG configuration not found. Run configure_tenant_rag first.")
        if (
            configuration.last_index_status == TenantRagConfiguration.InventoryStatus.RUNNING
            and configuration.last_index_started_at
            and configuration.last_index_started_at > stale_cutoff
        ):
            raise TenantRagIndexingError("Another indexing run for this tenant is already running.")

        configuration.last_index_status = TenantRagConfiguration.InventoryStatus.RUNNING
        configuration.last_index_started_at = now
        configuration.last_index_mode = mode
        configuration.last_index_run_id = run_id
        configuration.last_index_error = ""
        configuration.save(
            update_fields=[
                "last_index_status",
                "last_index_started_at",
                "last_index_mode",
                "last_index_run_id",
                "last_index_error",
                "updated_at",
            ]
        )
        return configuration


def mark_index_failed(*, configuration: TenantRagConfiguration, run: TenantRagIndexRun | None, error: str) -> None:
    safe_error = sanitize_embedding_error(Exception(error), fallback="indexing_failed")[:500]
    now = timezone.now()
    configuration.last_index_status = TenantRagConfiguration.InventoryStatus.FAILED
    configuration.last_index_at = now
    configuration.last_index_error = safe_error
    configuration.save(
        update_fields=["last_index_status", "last_index_at", "last_index_error", "updated_at"]
    )
    if run is None and configuration.last_index_run_id:
        run = (
            TenantRagIndexRun.objects.filter(
                tenant_id=configuration.tenant_id,
                run_id=configuration.last_index_run_id,
                status=TenantRagIndexRun.Status.RUNNING,
            )
            .order_by("-started_at")
            .first()
        )
    if run is not None:
        run.status = TenantRagIndexRun.Status.FAILED
        run.last_error = safe_error
        run.finished_at = now
        run.save(update_fields=["status", "last_error", "finished_at", "updated_at"])


def _compatible_active_embedding(
    *,
    chunk: TenantRagDocumentChunk,
    config: EmbeddingConfig,
) -> TenantRagChunkEmbedding | None:
    return (
        TenantRagChunkEmbedding.objects.filter(
            tenant_id=chunk.tenant_id,
            chunk_id=chunk.id,
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            embedding_config_signature=config.signature,
            chunk_sha256=chunk.chunk_sha256,
            chunk_config_signature=chunk.chunk_config_signature,
            provider=config.provider,
            model=config.model,
            dimension=config.dimension,
        )
        .order_by("-updated_at", "-id")
        .first()
    )


def _decide_pending(
    *,
    tenant: Tenant,
    config: EmbeddingConfig,
) -> tuple[list[_PendingItem], IndexCounters, list[TenantRagChunkEmbedding]]:
    counters = IndexCounters()
    active_chunks = list(
        TenantRagDocumentChunk.objects.filter(
            tenant=tenant,
            is_active=True,
            status=TenantRagDocumentChunk.Status.ACTIVE,
        )
        .select_related("manifest")
        .order_by("id")
    )
    counters.chunks = len(active_chunks)
    counters.documents = len({chunk.manifest_id for chunk in active_chunks})

    pending: list[_PendingItem] = []
    active_chunk_ids = {chunk.id for chunk in active_chunks}

    for chunk in active_chunks:
        compatible = _compatible_active_embedding(chunk=chunk, config=config)
        if compatible is not None:
            counters.unchanged += 1
            continue

        previous = (
            TenantRagChunkEmbedding.objects.filter(
                tenant_id=chunk.tenant_id,
                chunk_id=chunk.id,
                is_active=True,
                status=TenantRagChunkEmbedding.Status.ACTIVE,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
        action = "reindex" if previous is not None else "index"
        pending.append(_PendingItem(chunk=chunk, action=action, previous=previous))
        counters.pending += 1

    to_deactivate = list(
        TenantRagChunkEmbedding.objects.filter(
            tenant=tenant,
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
        )
        .exclude(chunk_id__in=active_chunk_ids)
        .order_by("id")
    )
    return pending, counters, to_deactivate


def _persist_embedding(
    *,
    chunk: TenantRagDocumentChunk,
    config: EmbeddingConfig,
    vector: list[float],
    previous: TenantRagChunkEmbedding | None,
    now,
) -> None:
    with transaction.atomic():
        embedding, created = TenantRagChunkEmbedding.objects.select_for_update().get_or_create(
            tenant_id=chunk.tenant_id,
            chunk_id=chunk.id,
            embedding_config_signature=config.signature,
            defaults={
                "manifest_id": chunk.manifest_id,
                "chunk_sha256": chunk.chunk_sha256,
                "chunk_config_signature": chunk.chunk_config_signature,
                "provider": config.provider,
                "model": config.model,
                "dimension": config.dimension,
                "vector": vector,
                "status": TenantRagChunkEmbedding.Status.ACTIVE,
                "is_active": True,
                "first_indexed_at": now,
                "last_indexed_at": now,
                "last_error": "",
            },
        )
        if not created:
            embedding.manifest_id = chunk.manifest_id
            embedding.chunk_sha256 = chunk.chunk_sha256
            embedding.chunk_config_signature = chunk.chunk_config_signature
            embedding.provider = config.provider
            embedding.model = config.model
            embedding.dimension = config.dimension
            embedding.vector = vector
            embedding.status = TenantRagChunkEmbedding.Status.ACTIVE
            embedding.is_active = True
            embedding.last_indexed_at = now
            embedding.last_error = ""
            if embedding.first_indexed_at is None:
                embedding.first_indexed_at = now
            embedding.save()

        if previous is not None and previous.pk != embedding.pk and previous.is_active:
            previous.is_active = False
            previous.status = TenantRagChunkEmbedding.Status.REPLACED
            previous.save(update_fields=["is_active", "status", "updated_at"])

        # Deactivate any other active embeddings for the same chunk under different configs.
        (
            TenantRagChunkEmbedding.objects.filter(
                tenant_id=chunk.tenant_id,
                chunk_id=chunk.id,
                is_active=True,
            )
            .exclude(pk=embedding.pk)
            .update(
                is_active=False,
                status=TenantRagChunkEmbedding.Status.REPLACED,
                updated_at=now,
            )
        )


def _deactivate_embeddings(*, embeddings: list[TenantRagChunkEmbedding], dry_run: bool) -> int:
    if not embeddings:
        return 0
    if dry_run:
        return len(embeddings)
    now = timezone.now()
    ids = [item.pk for item in embeddings]
    updated = TenantRagChunkEmbedding.objects.filter(pk__in=ids, tenant_id=embeddings[0].tenant_id).update(
        is_active=False,
        status=TenantRagChunkEmbedding.Status.REPLACED,
        updated_at=now,
    )
    return updated


def run_index_for_tenant(
    *,
    configuration: TenantRagConfiguration,
    dry_run: bool = False,
    provider: EmbeddingProvider | None = None,
    config: EmbeddingConfig | None = None,
    run_id: str | None = None,
) -> IndexOutcome:
    tenant = configuration.tenant
    cfg = config or load_embedding_config()
    mode = "dry_run" if dry_run else "index"
    operational_run_id = run_id or configuration.last_index_run_id or str(uuid.uuid4())
    now = timezone.now()

    if not dry_run and not cfg.indexing_enabled:
        raise TenantRagIndexingError(
            "Indexing is disabled. Set LIVIA_RAG_INDEXING_ENABLED=True only after explicit authorization."
        )

    if provider is None:
        if dry_run:
            provider = FakeEmbeddingProvider()
        else:
            provider = build_embedding_provider(cfg)

    run = TenantRagIndexRun.objects.create(
        tenant=tenant,
        run_id=operational_run_id,
        mode=mode,
        provider=cfg.provider,
        model=cfg.model,
        dimension=cfg.dimension,
        embedding_config_signature=cfg.signature,
        status=TenantRagIndexRun.Status.RUNNING,
        dry_run=dry_run,
        started_at=now,
    )

    pending, counters, to_deactivate = _decide_pending(tenant=tenant, config=cfg)
    counters.deactivated = _deactivate_embeddings(embeddings=to_deactivate, dry_run=dry_run)

    logger.info(
        "tenant_rag_index_start tenant=%s run_id=%s mode=%s provider=%s model=%s dry_run=%s pending=%s",
        tenant.slug,
        operational_run_id,
        mode,
        cfg.provider,
        cfg.model,
        dry_run,
        counters.pending,
    )

    batch_size = cfg.batch_size
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        counters.batches += 1
        if dry_run:
            for item in batch:
                if item.action == "reindex":
                    counters.reindexed += 1
                else:
                    counters.indexed += 1
            continue

        texts = [item.chunk.chunk_text for item in batch]
        try:
            vectors = provider.embed_texts(texts, config=cfg)
        except (EmbeddingProviderError, EmbeddingConfigurationError) as exc:
            safe = sanitize_embedding_error(exc)
            counters.failed += len(batch)
            logger.warning(
                "tenant_rag_index_batch_failed tenant=%s run_id=%s batch=%s failed=%s error=%s",
                tenant.slug,
                operational_run_id,
                counters.batches,
                len(batch),
                safe,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            safe = sanitize_embedding_error(exc)
            counters.failed += len(batch)
            logger.warning(
                "tenant_rag_index_batch_failed tenant=%s run_id=%s batch=%s failed=%s error=%s",
                tenant.slug,
                operational_run_id,
                counters.batches,
                len(batch),
                safe,
            )
            continue

        indexed_at = timezone.now()
        for item, vector in zip(batch, vectors):
            try:
                _persist_embedding(
                    chunk=item.chunk,
                    config=cfg,
                    vector=vector,
                    previous=item.previous,
                    now=indexed_at,
                )
                if item.action == "reindex":
                    counters.reindexed += 1
                else:
                    counters.indexed += 1
            except Exception as exc:  # noqa: BLE001
                counters.failed += 1
                safe = sanitize_embedding_error(exc)
                logger.warning(
                    "tenant_rag_index_chunk_failed tenant=%s run_id=%s chunk_id=%s error=%s",
                    tenant.slug,
                    operational_run_id,
                    item.chunk.id,
                    safe,
                )

    if counters.failed and (counters.indexed or counters.reindexed or counters.unchanged or counters.deactivated):
        status = TenantRagConfiguration.InventoryStatus.PARTIAL
        run_status = TenantRagIndexRun.Status.PARTIAL
    elif counters.failed and not (counters.indexed or counters.reindexed or counters.unchanged):
        status = TenantRagConfiguration.InventoryStatus.FAILED
        run_status = TenantRagIndexRun.Status.FAILED
    else:
        status = TenantRagConfiguration.InventoryStatus.SUCCESS
        run_status = TenantRagIndexRun.Status.SUCCESS

    finished_at = timezone.now()
    configuration.last_index_status = status
    configuration.last_index_at = finished_at
    configuration.last_index_error = "" if status != TenantRagConfiguration.InventoryStatus.FAILED else "indexing_failed"
    configuration.save(
        update_fields=[
            "last_index_status",
            "last_index_at",
            "last_index_error",
            "updated_at",
        ]
    )

    run.status = run_status
    run.documents = counters.documents
    run.chunks = counters.chunks
    run.pending = counters.pending
    run.indexed = counters.indexed
    run.reindexed = counters.reindexed
    run.unchanged = counters.unchanged
    run.deactivated = counters.deactivated
    run.skipped = counters.skipped
    run.failed = counters.failed
    run.batches = counters.batches
    run.finished_at = finished_at
    if run_status == TenantRagIndexRun.Status.FAILED:
        run.last_error = configuration.last_index_error
    run.save()

    logger.info(
        "tenant_rag_index_done tenant=%s run_id=%s mode=%s status=%s documents=%s chunks=%s pending=%s "
        "indexed=%s reindexed=%s unchanged=%s deactivated=%s skipped=%s failed=%s batches=%s",
        tenant.slug,
        operational_run_id,
        mode,
        status,
        counters.documents,
        counters.chunks,
        counters.pending,
        counters.indexed,
        counters.reindexed,
        counters.unchanged,
        counters.deactivated,
        counters.skipped,
        counters.failed,
        counters.batches,
    )

    return IndexOutcome(
        mode=mode,
        status=status,
        run_id=operational_run_id,
        dry_run=dry_run,
        counters=counters,
        provider=cfg.provider,
        model=cfg.model,
        dimension=cfg.dimension,
        embedding_config_signature=cfg.signature,
    )
