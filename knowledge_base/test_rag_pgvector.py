from __future__ import annotations

import json
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
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
    BACKEND_IN_MEMORY,
    BACKEND_POSTGRES_PGVECTOR,
    InMemoryVectorSearchBackend,
    RagVectorSearchError,
    get_vector_search_backend,
)
from knowledge_base.testing.rag_dimensions import (
    RagTestDimensionMixin,
    rag_test_embedding_dimension,
    rag_test_zero_vector,
)
from tenants.models import Tenant


@override_settings(
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=False,
    LIVIA_RAG_VECTOR_BACKEND="in_memory",
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_BATCH_SIZE=4,
    LIVIA_RAG_MIN_SIMILARITY_SCORE=0.10,
    LIVIA_RAG_MAX_RETRIEVED_CHUNKS=3,
    LIVIA_RAG_MAX_CONTEXT_CHARS=500,
    LIVIA_RAG_MAX_CHUNKS_PER_MANIFEST=2,
    LIVIA_RAG_VECTOR_CANDIDATE_LIMIT=10,
    LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=True,
)
class RagPgvectorPhase6Tests(RagTestDimensionMixin, TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Grani", slug="granimarmores-pitondo")
        self.other = Tenant.objects.create(name="Outro", slug="outro-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        TenantRagConfiguration.objects.create(
            tenant=self.other,
            approved_folder_id="folder-b",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.provider = FakeEmbeddingProvider()
        self.embedding_config = load_embedding_config()

    def _index_text(self, *, tenant, configuration, file_id: str, text: str, ordinal: int = 0):
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=tenant,
            configuration=configuration,
            drive_file_id=file_id,
            name=f"Doc {file_id}",
            mime_type="application/vnd.google-apps.document",
            relative_path=f"/{file_id}",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=tenant,
            manifest=manifest,
            normalized_text=text,
            normalized_text_sha256=f"{file_id}-src".ljust(64, "0")[:64],
            normalized_text_char_count=len(text),
            normalized_text_byte_count=len(text.encode("utf-8")),
            exported_at=timezone.now(),
        )
        chunk = TenantRagDocumentChunk.objects.create(
            tenant=tenant,
            manifest=manifest,
            staging=staging,
            ordinal=ordinal,
            chunk_text=text,
            chunk_sha256=f"{file_id}-{ordinal}".ljust(64, "0")[:64],
            source_text_sha256=staging.normalized_text_sha256,
            chunk_config_signature="chunk-cfg",
            char_count=len(text),
            byte_count=len(text.encode("utf-8")),
            start_char=0,
            end_char=len(text),
            status=TenantRagDocumentChunk.Status.ACTIVE,
            is_active=True,
        )
        vector = self.provider.embed_texts([text], config=self.embedding_config)[0]
        TenantRagChunkEmbedding.objects.create(
            tenant=tenant,
            chunk=chunk,
            manifest=manifest,
            chunk_sha256=chunk.chunk_sha256,
            chunk_config_signature=chunk.chunk_config_signature,
            provider=self.embedding_config.provider,
            model=self.embedding_config.model,
            dimension=self.embedding_config.dimension,
            embedding_config_signature=self.embedding_config.signature,
            vector=vector,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            is_active=True,
            first_indexed_at=timezone.now(),
            last_indexed_at=timezone.now(),
        )
        return chunk

    def test_auto_backend_uses_in_memory_on_sqlite(self):
        with override_settings(LIVIA_RAG_VECTOR_BACKEND="auto"):
            backend = get_vector_search_backend()
        if connection.vendor == "sqlite":
            self.assertEqual(backend.name, BACKEND_IN_MEMORY)
            self.assertIsInstance(backend, InMemoryVectorSearchBackend)

    def test_forced_postgres_backend_fails_on_sqlite(self):
        if connection.vendor != "sqlite":
            self.skipTest("sqlite-only assertion")
        with self.assertRaises(RagVectorSearchError):
            get_vector_search_backend(BACKEND_POSTGRES_PGVECTOR)

    def test_invalid_backend_fails_closed(self):
        with override_settings(LIVIA_RAG_VECTOR_BACKEND="nope"):
            with self.assertRaises(RagVectorSearchError):
                get_vector_search_backend()

    def test_wrong_dimension_rejected_by_backend(self):
        self._index_text(
            tenant=self.tenant,
            configuration=self.config,
            file_id="a",
            text="mármore branco",
        )
        backend = InMemoryVectorSearchBackend()
        with self.assertRaises(RagVectorSearchError):
            backend.search_similar_chunks(
                tenant=self.tenant,
                query_vector=[0.1, 0.2],
                config=self.embedding_config,
                limit=5,
            )

    def test_provider_model_isolation(self):
        self._index_text(
            tenant=self.tenant,
            configuration=self.config,
            file_id="a",
            text="mármore branco para bancada",
        )
        other_chunk = self._index_text(
            tenant=self.tenant,
            configuration=self.config,
            file_id="b",
            text="conteudo de outro modelo",
        )
        TenantRagChunkEmbedding.objects.filter(chunk=other_chunk).update(
            model="other-model",
            embedding_config_signature="other-signature",
        )
        hits = InMemoryVectorSearchBackend().search_similar_chunks(
            tenant=self.tenant,
            query_vector=self.provider.embed_texts(
                ["mármore branco para bancada"],
                config=self.embedding_config,
            )[0],
            config=self.embedding_config,
            limit=10,
        )
        self.assertTrue(hits)
        self.assertTrue(all(hit.embedding.model == "fake-embed-v1" for hit in hits))
        self.assertTrue(all(hit.embedding.embedding_config_signature == self.embedding_config.signature for hit in hits))

    def test_ranking_limit_and_threshold(self):
        text = "mármore branco para bancada"
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="a", text=text)
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="b", text="granito preto")
        result = retrieve_context(
            tenant=self.tenant,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
            limit=1,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.chunks), 1)
        self.assertIn("mármore", result.chunks[0].text.lower())
        self.assertEqual(result.backend, BACKEND_IN_MEMORY)

    def test_multi_tenant_isolation(self):
        text = "segredo compartilhado mármore"
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="a", text=text)
        other_config = TenantRagConfiguration.objects.get(tenant=self.other)
        self._index_text(tenant=self.other, configuration=other_config, file_id="a", text=text)
        result_a = retrieve_context(
            tenant=self.tenant,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        result_b = retrieve_context(
            tenant=self.other,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        ids_a = {item.chunk_id for item in result_a.chunks}
        ids_b = {item.chunk_id for item in result_b.chunks}
        self.assertTrue(ids_a)
        self.assertTrue(ids_b)
        self.assertTrue(ids_a.isdisjoint(ids_b))

    def test_metrics_hit_empty_and_no_sensitive_payload(self):
        text = "mármore para escada"
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="a", text=text)
        retrieve_context(
            tenant=self.tenant,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        event = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        self.assertTrue(event.hit)
        self.assertEqual(event.status, RagRetrievalEvent.Status.COMPLETED)
        self.assertEqual(event.backend, BACKEND_IN_MEMORY)
        blob = f"{event.reason}{event.backend}{event.provider}{event.model}"
        self.assertNotIn(text, blob)

        retrieve_context(
            tenant=self.tenant,
            query="assunto completamente sem match xyzzy",
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        # Pode ser empty ou completed com score baixo; garante evento sem texto.
        latest = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        self.assertIn(latest.status, {RagRetrievalEvent.Status.EMPTY, RagRetrievalEvent.Status.COMPLETED})

    def test_tenant_threshold_override_and_event_metadata(self):
        text = "mármore para bancada premium"
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="thr", text=text)
        self.config.min_similarity_score = 0.35
        self.config.save(update_fields=["min_similarity_score", "updated_at"])
        result = retrieve_context(
            tenant=self.tenant,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        self.assertEqual(result.threshold_source, "tenant")
        self.assertAlmostEqual(result.threshold, 0.35, places=6)

        event = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        self.assertEqual(event.threshold_source, "tenant")
        self.assertAlmostEqual(event.threshold, 0.35, places=6)
        self.assertFalse(event.dry_run)

    def test_vector_backend_exception_records_failed_event(self):
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="fail", text="granito")
        with patch(
            "knowledge_base.rag.conversation_retrieval.get_vector_search_backend",
            side_effect=RuntimeError("forced_backend_failure"),
        ):
            result = retrieve_context(
                tenant=self.tenant,
                query="granito",
                provider=self.provider,
                config=self.embedding_config,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "vector_backend")
        event = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        self.assertEqual(event.status, RagRetrievalEvent.Status.FAILED)
        self.assertEqual(event.reason, "vector_backend")

    @override_settings(LIVIA_RAG_DRY_RUN=True)
    def test_dry_run_event_flag_is_persisted(self):
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="dry", text="granito branco")
        retrieve_context(
            tenant=self.tenant,
            query="granito branco",
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=InMemoryVectorSearchBackend(),
        )
        event = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        self.assertTrue(event.dry_run)

    def test_skipped_metric_and_idempotent_replay_no_extra_retrieval(self):
        with override_settings(LIVIA_RAG_ENABLED=False):
            retrieve_context(
                tenant=self.tenant,
                query="qualquer",
                provider=self.provider,
                config=self.embedding_config,
            )
        skipped = RagRetrievalEvent.objects.filter(
            tenant=self.tenant,
            status=RagRetrievalEvent.Status.SKIPPED,
        ).latest("created_at")
        self.assertEqual(skipped.reason, "global_disabled")
        self.assertFalse(skipped.hit)

        payload = {
            "tenant": self.tenant.slug,
            "session_id": "rag-metric-idempotent",
            "request_id": "55555555-5555-4555-8555-555555555555",
            "message": "Tem mármore?",
        }
        before = RagRetrievalEvent.objects.filter(tenant=self.tenant).count()
        with patch(
            "assistant_core.services.chat_processing.build_knowledge_context",
            return_value="",
        ):
            first = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")
            second = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.headers.get("X-Livia-Idempotent-Replay"), "true")
        after = RagRetrievalEvent.objects.filter(tenant=self.tenant).count()
        # build_knowledge_context patched => retrieve_context nao roda no chat.
        self.assertEqual(after, before)

    def test_vector_length_validation_on_model(self):
        chunk = self._index_text(
            tenant=self.tenant,
            configuration=self.config,
            file_id="dim",
            text="ok",
        )
        emb = TenantRagChunkEmbedding.objects.get(chunk=chunk)
        expected_dim = rag_test_embedding_dimension()
        emb.vector = [0.1, 0.2]
        emb.dimension = expected_dim
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            emb.save()

    def test_rag_vector_field_preps_text_for_postgresql(self):
        """Evita regressão: Vector() bruto quebra psycopg3 sem register_vector."""
        from knowledge_base.rag.vector_field import RagVectorField

        field = RagVectorField(dimensions=rag_test_embedding_dimension())
        dim = rag_test_embedding_dimension()
        prepared = field.get_db_prep_value([1.0] + rag_test_zero_vector(dim - 1), connection)
        if connection.vendor == "postgresql":
            self.assertIsInstance(prepared, str)
            self.assertTrue(prepared.startswith("["))
            self.assertNotEqual(type(prepared).__name__, "Vector")
        else:
            # JSONField pode devolver lista ou texto JSON serializado.
            self.assertTrue(isinstance(prepared, (list, str)))

    def test_postgres_pgvector_backend_when_available(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL + pgvector")
        with override_settings(LIVIA_RAG_VECTOR_BACKEND="postgres_pgvector"):
            backend = get_vector_search_backend()
        self.assertEqual(backend.name, BACKEND_POSTGRES_PGVECTOR)
        text = "mármore branco para bancada"
        self._index_text(tenant=self.tenant, configuration=self.config, file_id="pg-a", text=text)
        other_config = TenantRagConfiguration.objects.get(tenant=self.other)
        self._index_text(tenant=self.other, configuration=other_config, file_id="pg-a", text=text)
        result = retrieve_context(
            tenant=self.tenant,
            query=text,
            provider=self.provider,
            config=self.embedding_config,
            vector_backend=backend,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.backend, BACKEND_POSTGRES_PGVECTOR)
        self.assertTrue(result.chunks)
        ids = {item.chunk_id for item in result.chunks}
        foreign = TenantRagChunkEmbedding.objects.filter(
            tenant=self.other,
            chunk_id__in=ids,
        ).exists()
        self.assertFalse(foreign)
        self.assertTrue(
            TenantRagChunkEmbedding.objects.filter(
                tenant=self.tenant,
                chunk_id__in=ids,
            ).exists()
        )
