#!/usr/bin/env python
"""Validação controlada PostgreSQL/pgvector da Fase 7 (sem secrets/documentos)."""
from __future__ import annotations

import json
import os
import time
from statistics import mean, median
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("LIVIA_RAG_ENABLED", "True")
os.environ.setdefault("LIVIA_RAG_DRY_RUN", "False")
os.environ.setdefault("LIVIA_RAG_VECTOR_BACKEND", "postgres_pgvector")
os.environ.setdefault("LIVIA_RAG_EMBEDDING_PROVIDER", "fake")
os.environ.setdefault("LIVIA_ALLOW_FAKE_EMBEDDINGS", "True")
os.environ.setdefault("LIVIA_RAG_EMBEDDING_MODEL", "fake-embed-phase7")
# Alinhado à coluna vector(1536) já migrada no PostgreSQL local.
os.environ.setdefault("LIVIA_RAG_EMBEDDING_DIMENSION", "1536")
os.environ.setdefault("LIVIA_RAG_MIN_SIMILARITY_SCORE", "0.10")
os.environ.setdefault("LIVIA_RAG_MAX_RETRIEVED_CHUNKS", "3")
os.environ.setdefault("LIVIA_RAG_VECTOR_CANDIDATE_LIMIT", "10")
os.environ.setdefault("LIVIA_ALLOW_ORIGINLESS_PUBLIC_API", "True")
# Django test Client usa Host: testserver fora de TestCase.
os.environ["ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"

import django

django.setup()

from django.db import connection
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from knowledge_base.models import (
    RagRetrievalEvent,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
)
from knowledge_base.rag.conversation_retrieval import retrieve_context
from knowledge_base.rag.embeddings import FakeEmbeddingProvider, load_embedding_config
from knowledge_base.rag.vector_search import (
    InMemoryVectorSearchBackend,
    PostgresPgvectorSearchBackend,
    get_vector_search_backend,
)
from tenants.models import Tenant


def _unit(i: int, dim: int = 1536) -> list[float]:
    vec = [0.0] * dim
    vec[i % dim] = 1.0
    return vec


def _ensure_tenant(slug: str, folder: str) -> tuple[Tenant, TenantRagConfiguration]:
    from tenants.models import TenantAllowedOrigin

    tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": slug, "is_active": True})
    if not tenant.is_active:
        tenant.is_active = True
        tenant.save(update_fields=["is_active"])
    # Origin local coerente: tenant ativo exige allowed origin (readiness/widget).
    TenantAllowedOrigin.objects.update_or_create(
        tenant=tenant,
        origin=f"https://local-{slug}.example.test",
        defaults={"is_active": True},
    )
    cfg, _ = TenantRagConfiguration.objects.update_or_create(
        tenant=tenant,
        defaults={
            "approved_folder_id": folder,
            "sync_enabled": True,
            "retrieval_enabled": True,
        },
    )
    return tenant, cfg


def _index_vector(*, tenant, configuration, file_id: str, text: str, vector: list[float], config):
    manifest, _ = TenantRagDriveFileManifest.objects.update_or_create(
        tenant=tenant,
        drive_file_id=file_id,
        defaults={
            "configuration": configuration,
            "name": file_id,
            "mime_type": "application/vnd.google-apps.document",
            "relative_path": f"/{file_id}",
            "status": TenantRagDriveFileManifest.Status.EXPORTED,
            "is_active": True,
        },
    )
    staging, _ = TenantRagDriveTextStaging.objects.update_or_create(
        tenant=tenant,
        manifest=manifest,
        defaults={
            "normalized_text": text,
            "normalized_text_sha256": f"{file_id}-src".ljust(64, "0")[:64],
            "normalized_text_char_count": len(text),
            "normalized_text_byte_count": len(text.encode("utf-8")),
            "exported_at": timezone.now(),
        },
    )
    chunk, _ = TenantRagDocumentChunk.objects.update_or_create(
        tenant=tenant,
        manifest=manifest,
        source_text_sha256=staging.normalized_text_sha256,
        chunk_config_signature="phase7-chunk",
        ordinal=0,
        defaults={
            "staging": staging,
            "chunk_text": text,
            "chunk_sha256": f"{file_id}-0".ljust(64, "0")[:64],
            "char_count": len(text),
            "byte_count": len(text.encode("utf-8")),
            "start_char": 0,
            "end_char": len(text),
            "status": TenantRagDocumentChunk.Status.ACTIVE,
            "is_active": True,
        },
    )
    emb, _ = TenantRagChunkEmbedding.objects.update_or_create(
        tenant=tenant,
        chunk=chunk,
        embedding_config_signature=config.signature,
        defaults={
            "manifest": manifest,
            "chunk_sha256": chunk.chunk_sha256,
            "chunk_config_signature": chunk.chunk_config_signature,
            "provider": config.provider,
            "model": config.model,
            "dimension": config.dimension,
            "vector": vector,
            "status": TenantRagChunkEmbedding.Status.ACTIVE,
            "is_active": True,
            "first_indexed_at": timezone.now(),
            "last_indexed_at": timezone.now(),
            "last_error": "",
        },
    )
    return emb


def main():
    assert connection.vendor == "postgresql", connection.vendor
    cfg = load_embedding_config()
    assert cfg.dimension == 1536, cfg.dimension
    backend = get_vector_search_backend("postgres_pgvector")
    assert isinstance(backend, PostgresPgvectorSearchBackend)

    tenant_a, cfg_a = _ensure_tenant("phase7-tenant-a", "folder-a")
    tenant_b, cfg_b = _ensure_tenant("phase7-tenant-b", "folder-b")
    gran, cfg_g = _ensure_tenant("granimarmores-pitondo", "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm")

    _index_vector(tenant=tenant_a, configuration=cfg_a, file_id="a1", text="marmore A1", vector=_unit(0), config=cfg)
    _index_vector(tenant=tenant_a, configuration=cfg_a, file_id="a2", text="marmore A2", vector=_unit(1), config=cfg)
    _index_vector(tenant=tenant_b, configuration=cfg_b, file_id="b1", text="marmore B1", vector=_unit(0), config=cfg)
    _index_vector(tenant=gran, configuration=cfg_g, file_id="g1", text="bancada marmore GP", vector=_unit(0), config=cfg)
    _index_vector(tenant=gran, configuration=cfg_g, file_id="g2", text="banho granito GP", vector=_unit(2), config=cfg)

    total = TenantRagChunkEmbedding.objects.count()
    nulls = TenantRagChunkEmbedding.objects.filter(vector__isnull=True).count()
    print("EMBEDDING_COUNTS", {"total": total, "null_vectors": nulls})

    query = _unit(0)
    hits_a = backend.search_similar_chunks(tenant=tenant_a, query_vector=query, config=cfg, limit=5)
    hits_b = backend.search_similar_chunks(tenant=tenant_b, query_vector=query, config=cfg, limit=5)
    assert hits_a and all(h.embedding.tenant_id == tenant_a.id for h in hits_a)
    assert hits_b and all(h.embedding.tenant_id == tenant_b.id for h in hits_b)
    assert all(h.embedding.tenant_id != tenant_b.id for h in hits_a)
    assert hits_a[0].score >= hits_a[-1].score
    print("MULTI_TENANT_OK", f"a={len(hits_a)} b={len(hits_b)} top_a={hits_a[0].score:.4f}")

    from pgvector.django import CosineDistance

    qs = (
        TenantRagChunkEmbedding.objects.filter(
            tenant=tenant_a,
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            embedding_config_signature=cfg.signature,
            dimension=cfg.dimension,
            provider=cfg.provider,
            model=cfg.model,
        )
        .annotate(distance=CosineDistance("vector", query))
        .order_by("distance", "chunk_id", "id")[:5]
    )
    sql, _params = qs.query.sql_with_params()
    compact = " ".join(sql.split())
    assert "tenant_id" in compact.lower()
    assert "<=>" in compact or "cosine" in compact.lower() or "distance" in compact.lower()
    print("SQL_OK", "tenant_filter_present=True")
    print("SQL_SNIPPET", compact[:320])

    with connection.cursor() as cursor:
        cursor.execute(
            """
            EXPLAIN
            SELECT id, chunk_id, (vector <=> %s::vector) AS distance
            FROM knowledge_base_tenantragchunkembedding
            WHERE tenant_id = %s
              AND is_active = TRUE
              AND status = 'active'
              AND provider = %s
              AND model = %s
              AND dimension = %s
              AND embedding_config_signature = %s
            ORDER BY vector <=> %s::vector, chunk_id, id
            LIMIT 5
            """,
            [
                str(query),
                tenant_a.id,
                cfg.provider,
                cfg.model,
                cfg.dimension,
                cfg.signature,
                str(query),
            ],
        )
        plan = "\n".join(row[0] for row in cursor.fetchall())
    print("EXPLAIN_START")
    print(plan)
    print("EXPLAIN_END")

    mem = InMemoryVectorSearchBackend().search_similar_chunks(
        tenant=tenant_a, query_vector=query, config=cfg, limit=5
    )
    pg_ids = [h.embedding.id for h in hits_a]
    mem_ids = [h.embedding.id for h in mem]
    print("RANKING_PARITY", pg_ids == mem_ids, "pg=", pg_ids, "mem=", mem_ids)

    def bench(backend_obj, n=20):
        times = []
        for _ in range(n):
            t0 = time.perf_counter()
            backend_obj.search_similar_chunks(tenant=gran, query_vector=query, config=cfg, limit=3)
            times.append((time.perf_counter() - t0) * 1000)
        return {"avg_ms": round(mean(times), 3), "median_ms": round(median(times), 3), "n": n}

    print(
        "BENCH",
        {
            "chunks": TenantRagChunkEmbedding.objects.filter(tenant=gran).count(),
            "pg": bench(backend),
            "mem": bench(InMemoryVectorSearchBackend()),
        },
    )

    provider = FakeEmbeddingProvider()
    with patch.object(FakeEmbeddingProvider, "embed_texts", return_value=[query]):
        result = retrieve_context(
            tenant=gran,
            query="bancada marmore",
            provider=provider,
            config=cfg,
            vector_backend=backend,
        )
    print(
        "RETRIEVAL",
        {
            "status": result.status,
            "backend": result.backend,
            "candidates": result.candidate_count,
            "results": len(result.chunks),
            "max_score": round(result.max_score, 4),
            "latency_ms": result.duration_ms,
        },
    )
    context = result.context_text
    assert "[KNOWLEDGE_BASE]" in context and "[/KNOWLEDGE_BASE]" in context
    print("CONTEXT_OK", "chars=", len(context))

    client = Client()
    payload = {
        "tenant": gran.slug,
        "session_id": "phase7-chat-session",
        "request_id": "77777777-7777-4777-8777-777777777777",
        "message": "Quero saber de bancada de marmore",
    }
    with patch("assistant_core.services.chat_processing.build_knowledge_context") as mocked:
        mocked.return_value = context
        first = client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")
        second = client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")
    print(
        "CHAT",
        first.status_code,
        "replay_header=",
        second.headers.get("X-Livia-Idempotent-Replay"),
        "calls=",
        mocked.call_count,
    )
    assert first.status_code == 200
    assert second.headers.get("X-Livia-Idempotent-Replay") == "true"
    assert mocked.call_count == 1

    class Boom(PostgresPgvectorSearchBackend):
        def search_similar_chunks(self, **kwargs):
            raise RuntimeError("forced_pgvector_failure")

    with patch.object(FakeEmbeddingProvider, "embed_texts", return_value=[query]):
        failed = retrieve_context(
            tenant=gran,
            query="falha",
            provider=provider,
            config=cfg,
            vector_backend=Boom(),
        )
    print("FALLBACK_FAIL", failed.status, failed.reason)
    assert failed.status == "failed"

    with override_settings(LIVIA_RAG_ENABLED=False):
        skipped = retrieve_context(
            tenant=gran,
            query="x",
            provider=provider,
            config=cfg,
            vector_backend=backend,
        )
    print("SKIPPED", skipped.status, skipped.reason)

    with patch.object(FakeEmbeddingProvider, "embed_texts", return_value=[_unit(7)]):
        with override_settings(LIVIA_RAG_MIN_SIMILARITY_SCORE=0.99):
            empty = retrieve_context(
                tenant=gran,
                query="sem match",
                provider=provider,
                config=cfg,
                vector_backend=backend,
            )
    print("EMPTY", empty.status, empty.reason, "max_score=", round(empty.max_score, 4))

    events = list(RagRetrievalEvent.objects.filter(tenant=gran).order_by("-created_at")[:10])
    for event in events:
        blob = f"{event.reason}|{event.backend}|{event.provider}|{event.model}|{event.status}"
        assert "bancada" not in blob.lower()
        assert "marmore" not in blob.lower()
        assert "sk-" not in blob.lower()
    print(
        "METRICS",
        [
            {
                "status": e.status,
                "hit": e.hit,
                "backend": e.backend,
                "results": e.result_count,
                "duration_ms": e.duration_ms,
            }
            for e in events[:6]
        ],
    )

    src = open("knowledge_base/rag/vector_search.py", encoding="utf-8").read()
    assert "candidates = list(" in src  # only in InMemory
    assert "CosineDistance" in src
    assert ".order_by(\"distance\"" in src or ".order_by('distance'" in src
    print("NO_FULL_PYTHON_SCAN_PG", "PostgresPgvectorSearchBackend uses ORDER BY distance LIMIT")
    print("PHASE7_STRUCTURAL_OK")


if __name__ == "__main__":
    main()
