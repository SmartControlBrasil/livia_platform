from __future__ import annotations

import math
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from audit.models import (
    ACTION_TENANT_RAG_INDEX_COMPLETED,
    ACTION_TENANT_RAG_INDEX_FAILED,
    ACTION_TENANT_RAG_INDEX_STARTED,
    AuditEvent,
)
from knowledge_base.models import (
    KnowledgeDocument,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
    TenantRagIndexRun,
)
from knowledge_base.rag.admin_search import TenantRagAdminSearchError, admin_vector_search
from knowledge_base.rag.embeddings import (
    EmbeddingConfig,
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    OpenAIEmbeddingProvider,
    load_embedding_config,
    validate_embedding_vectors,
)
from knowledge_base.rag.indexing import acquire_tenant_index_lock, run_index_for_tenant
from knowledge_base.rag.retriever import retrieve_relevant_knowledge
from tenants.models import Tenant


def _make_config(
    *,
    provider: str = "fake",
    model: str = "fake-embed-v1",
    dimension: int = 8,
    batch_size: int = 2,
    indexing_enabled: bool = True,
    signature: str | None = None,
) -> EmbeddingConfig:
    base = EmbeddingConfig(
        provider=provider,
        model=model,
        dimension=dimension,
        batch_size=batch_size,
        timeout_seconds=5,
        max_retries=1,
        retry_backoff_seconds=0.0,
        indexing_enabled=indexing_enabled,
        api_key_configured=False,
        signature=signature or f"{provider}:{model}:{dimension}:{batch_size}",
    )
    return base


@override_settings(
    LIVIA_RAG_INDEXING_ENABLED=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_EMBEDDING_BATCH_SIZE=2,
    LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS=5,
    LIVIA_RAG_EMBEDDING_MAX_RETRIES=1,
    LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS=0,
    LIVIA_RAG_EMBEDDING_API_KEY="",
    LIVIA_RAG_ADMIN_SEARCH_MAX_RESULTS=5,
    LIVIA_RAG_INDEX_RUNNING_TIMEOUT_SECONDS=1800,
)
class RagIndexingPhase4Tests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Granimarmores Pitondo", slug="granimarmores-pitondo")
        self.other_tenant = Tenant.objects.create(name="Outro", slug="outro-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            sync_enabled=True,
        )
        self.other_config = TenantRagConfiguration.objects.create(
            tenant=self.other_tenant,
            approved_folder_id="outro-folder",
            sync_enabled=True,
        )
        self.provider = FakeEmbeddingProvider()
        self.embedding_config = load_embedding_config()

    def _create_chunk(
        self,
        *,
        tenant,
        configuration,
        file_id="doc-1",
        text="Conteúdo de teste com acentuação.",
        ordinal=0,
        is_active=True,
        status=TenantRagDocumentChunk.Status.ACTIVE,
    ):
        manifest = TenantRagDriveFileManifest.objects.filter(tenant=tenant, drive_file_id=file_id).first()
        if manifest is None:
            manifest = TenantRagDriveFileManifest.objects.create(
                tenant=tenant,
                configuration=configuration,
                drive_file_id=file_id,
                name=file_id,
                mime_type="application/vnd.google-apps.document",
                status=TenantRagDriveFileManifest.Status.EXPORTED,
                is_active=True,
            )
        staging = getattr(manifest, "text_staging", None)
        if staging is None:
            staging = TenantRagDriveTextStaging.objects.create(
                tenant=tenant,
                manifest=manifest,
                normalized_text=text,
                normalized_text_sha256="a" * 64,
                normalized_text_char_count=len(text),
                normalized_text_byte_count=len(text.encode("utf-8")),
                exported_at=timezone.now(),
            )
        return TenantRagDocumentChunk.objects.create(
            tenant=tenant,
            manifest=manifest,
            staging=staging,
            ordinal=ordinal,
            chunk_text=text,
            chunk_sha256=f"{file_id}-{ordinal}-{len(text):04d}".ljust(64, "0")[:64],
            source_text_sha256=staging.normalized_text_sha256,
            chunk_config_signature="chunk-cfg-v1",
            char_count=len(text),
            byte_count=len(text.encode("utf-8")),
            start_char=0,
            end_char=len(text),
            status=status,
            is_active=is_active,
        )

    def test_fake_provider_is_deterministic(self):
        vectors_a = self.provider.embed_texts(["mesmo texto"], config=self.embedding_config)
        vectors_b = self.provider.embed_texts(["mesmo texto"], config=self.embedding_config)
        self.assertEqual(vectors_a, vectors_b)
        self.assertEqual(len(vectors_a[0]), self.embedding_config.dimension)

    def test_embeddings_isolated_by_tenant_in_persistence(self):
        chunk_a = self._create_chunk(tenant=self.tenant, configuration=self.config, text="texto compartilhado")
        chunk_b = self._create_chunk(
            tenant=self.other_tenant,
            configuration=self.other_config,
            text="texto compartilhado",
        )
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        call_command("index_tenant_rag", "--tenant", self.other_tenant.slug)
        emb_a = TenantRagChunkEmbedding.objects.get(tenant=self.tenant, chunk=chunk_a, is_active=True)
        emb_b = TenantRagChunkEmbedding.objects.get(tenant=self.other_tenant, chunk=chunk_b, is_active=True)
        self.assertNotEqual(emb_a.pk, emb_b.pk)
        self.assertEqual(emb_a.tenant_id, self.tenant.id)
        self.assertEqual(emb_b.tenant_id, self.other_tenant.id)

    def test_new_chunk_is_indexed(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        emb = TenantRagChunkEmbedding.objects.get(chunk=chunk, is_active=True)
        self.assertEqual(emb.provider, "fake")
        self.assertEqual(emb.dimension, 8)
        self.assertEqual(len(emb.vector), 8)

    def test_unchanged_chunk_skips_provider_call(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        with patch.object(FakeEmbeddingProvider, "embed_texts", wraps=self.provider.embed_texts) as mocked:
            with patch(
                "knowledge_base.rag.indexing.build_embedding_provider",
                return_value=self.provider,
            ):
                out = StringIO()
                call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        mocked.assert_not_called()
        self.assertIn("unchanged=1", out.getvalue())
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).count(), 1)

    def test_changed_chunk_reindexes(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config, text="versão 1")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        old = TenantRagChunkEmbedding.objects.get(chunk=chunk, is_active=True)
        chunk.chunk_text = "versão 2 alterada"
        chunk.chunk_sha256 = "b" * 64
        chunk.save()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        new = TenantRagChunkEmbedding.objects.get(chunk=chunk, is_active=True)
        self.assertEqual(new.chunk_sha256, "b" * 64)
        self.assertNotEqual(old.vector, new.vector)

    def test_model_change_forces_reindex(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        with override_settings(LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v2"):
            out = StringIO()
            call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        self.assertIn("reindexed=1", out.getvalue())
        active = TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().model, "fake-embed-v2")
        self.assertEqual(
            TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=False).count(),
            1,
        )

    def test_dimension_change_forces_reindex(self):
        from django.db import connection

        if connection.vendor == "postgresql":
            self.skipTest(
                "PostgreSQL/pgvector fixa vector(n) no schema; mudar dimensão exige "
                "nova migration/coluna, não apenas override de settings."
            )
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        with override_settings(LIVIA_RAG_EMBEDDING_DIMENSION=16):
            call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        active = TenantRagChunkEmbedding.objects.get(tenant=self.tenant, is_active=True)
        self.assertEqual(active.dimension, 16)
        self.assertEqual(len(active.vector), 16)

    def test_config_signature_change_forces_reindex(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        with override_settings(LIVIA_RAG_EMBEDDING_BATCH_SIZE=4):
            out = StringIO()
            call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        self.assertIn("reindexed=1", out.getvalue())

    def test_inactive_chunk_deactivates_embedding(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        chunk.is_active = False
        chunk.status = TenantRagDocumentChunk.Status.REPLACED
        chunk.save()
        out = StringIO()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        self.assertIn("deactivated=1", out.getvalue())
        emb = TenantRagChunkEmbedding.objects.get(chunk=chunk)
        self.assertFalse(emb.is_active)

    def test_restored_chunk_reindexes_or_reactivates(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        emb = TenantRagChunkEmbedding.objects.get(chunk=chunk, is_active=True)
        emb.is_active = False
        emb.status = TenantRagChunkEmbedding.Status.REPLACED
        emb.save()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        emb.refresh_from_db()
        self.assertTrue(emb.is_active)
        self.assertEqual(emb.status, TenantRagChunkEmbedding.Status.ACTIVE)

    def test_batch_limit_and_multiple_chunks(self):
        for idx in range(3):
            self._create_chunk(
                tenant=self.tenant,
                configuration=self.config,
                file_id=f"doc-{idx}",
                text=f"texto {idx}",
                ordinal=0,
            )
        out = StringIO()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        self.assertIn("indexed=3", out.getvalue())
        self.assertIn("batches=2", out.getvalue())
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=True).count(), 3)

    def test_validate_wrong_dimension(self):
        with self.assertRaises(EmbeddingProviderError):
            validate_embedding_vectors([[0.1, 0.2]], expected_count=1, expected_dimension=8)

    def test_validate_wrong_count(self):
        with self.assertRaises(EmbeddingProviderError):
            validate_embedding_vectors([[0.1] * 8, [0.2] * 8], expected_count=1, expected_dimension=8)

    def test_validate_empty_vector(self):
        with self.assertRaises(EmbeddingProviderError):
            validate_embedding_vectors([[]], expected_count=1, expected_dimension=8)

    def test_validate_nan_and_infinity(self):
        with self.assertRaises(EmbeddingProviderError):
            validate_embedding_vectors([[math.nan] + [0.1] * 7], expected_count=1, expected_dimension=8)
        with self.assertRaises(EmbeddingProviderError):
            validate_embedding_vectors([[math.inf] + [0.1] * 7], expected_count=1, expected_dimension=8)

    def test_openai_timeout_and_retry(self):
        import requests

        cfg = _make_config(provider="openai", model="text-embedding-3-small", dimension=8)
        provider = OpenAIEmbeddingProvider()
        with override_settings(LIVIA_RAG_EMBEDDING_API_KEY="test-key"):
            with patch("requests.post", side_effect=requests.Timeout("timed out")) as mocked:
                with self.assertRaises(EmbeddingProviderError) as ctx:
                    provider.embed_texts(["x"], config=cfg)
        self.assertEqual(str(ctx.exception), "embedding_timeout")
        self.assertEqual(mocked.call_count, 2)

    def test_openai_error_is_sanitized(self):
        cfg = _make_config(provider="openai", model="text-embedding-3-small", dimension=8)
        provider = OpenAIEmbeddingProvider()

        class _Resp:
            status_code = 401

            def json(self):
                return {"error": {"message": "Invalid API key sk-secret"}}

        with override_settings(LIVIA_RAG_EMBEDDING_API_KEY="sk-secret"):
            with patch("requests.post", return_value=_Resp()):
                with self.assertRaises(EmbeddingProviderError) as ctx:
                    provider.embed_texts(["x"], config=cfg)
        self.assertEqual(str(ctx.exception), "embedding_http_401")

    def test_batch_failure_preserves_previous_embedding(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config, text="original")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        previous = TenantRagChunkEmbedding.objects.get(chunk=chunk, is_active=True)
        previous_vector = list(previous.vector)
        chunk.chunk_text = "novo texto"
        chunk.chunk_sha256 = "c" * 64
        chunk.save()

        class FailingProvider(FakeEmbeddingProvider):
            def embed_texts(self, texts, *, config):
                raise EmbeddingProviderError("forced_batch_failure")

        with patch("knowledge_base.rag.indexing.build_embedding_provider", return_value=FailingProvider()):
            with self.assertRaises(CommandError):
                call_command("index_tenant_rag", "--tenant", self.tenant.slug)

        previous.refresh_from_db()
        self.assertTrue(previous.is_active)
        self.assertEqual(previous.vector, previous_vector)
        self.assertNotEqual(previous.chunk_sha256, "c" * 64)

    def test_idempotency_second_run(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        out = StringIO()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug, stdout=out)
        self.assertIn("unchanged=1", out.getvalue())
        self.assertIn("indexed=0", out.getvalue())
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).count(), 1)

    def test_partial_execution_exit_nonzero(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config, file_id="ok", text="ok")
        self._create_chunk(tenant=self.tenant, configuration=self.config, file_id="fail", text="fail")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)

        # Force one chunk to need reindex and fail only that batch item via provider
        fail_chunk = TenantRagDocumentChunk.objects.get(manifest__drive_file_id="fail")
        fail_chunk.chunk_sha256 = "d" * 64
        fail_chunk.save()

        class SelectiveFail(FakeEmbeddingProvider):
            def embed_texts(self, texts, *, config):
                if any("fail" in text for text in texts):
                    raise EmbeddingProviderError("selective_fail")
                return super().embed_texts(texts, config=config)

        with patch("knowledge_base.rag.indexing.build_embedding_provider", return_value=SelectiveFail()):
            with self.assertRaises(CommandError):
                call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        run = TenantRagIndexRun.objects.filter(tenant=self.tenant).latest("started_at")
        self.assertEqual(run.status, TenantRagIndexRun.Status.PARTIAL)

    def test_concurrency_same_tenant(self):
        self.config.last_index_status = TenantRagConfiguration.InventoryStatus.RUNNING
        self.config.last_index_started_at = timezone.now()
        self.config.save(update_fields=["last_index_status", "last_index_started_at", "updated_at"])
        with self.assertRaises(CommandError):
            call_command("index_tenant_rag", "--tenant", self.tenant.slug)

    def test_concurrency_independent_tenants(self):
        self.config.last_index_status = TenantRagConfiguration.InventoryStatus.RUNNING
        self.config.last_index_started_at = timezone.now()
        self.config.save(update_fields=["last_index_status", "last_index_started_at", "updated_at"])
        self._create_chunk(tenant=self.other_tenant, configuration=self.other_config)
        call_command("index_tenant_rag", "--tenant", self.other_tenant.slug)
        self.assertEqual(
            TenantRagChunkEmbedding.objects.filter(tenant=self.other_tenant, is_active=True).count(),
            1,
        )

    def test_cross_tenant_relation_rejected(self):
        chunk = self._create_chunk(tenant=self.tenant, configuration=self.config)
        other_manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.other_tenant,
            configuration=self.other_config,
            drive_file_id="other-doc",
            name="other",
            mime_type="application/vnd.google-apps.document",
        )
        with self.assertRaises(ValidationError):
            TenantRagChunkEmbedding(
                tenant=self.tenant,
                chunk=chunk,
                manifest=other_manifest,
                chunk_sha256=chunk.chunk_sha256,
                chunk_config_signature=chunk.chunk_config_signature,
                provider="fake",
                model="fake",
                dimension=8,
                embedding_config_signature="sig",
                vector=[0.1] * 8,
            ).save()

    def test_admin_search_requires_tenant(self):
        with self.assertRaises(TenantRagAdminSearchError):
            admin_vector_search(tenant=None, query_text=" magra", provider=self.provider, config=self.embedding_config)

    def test_admin_search_isolates_tenant_and_tiebreak(self):
        chunk_a = self._create_chunk(tenant=self.tenant, configuration=self.config, file_id="a", text="pedra mármore")
        chunk_b = self._create_chunk(tenant=self.tenant, configuration=self.config, file_id="b", text="pedra mármore")
        self._create_chunk(tenant=self.other_tenant, configuration=self.other_config, text="pedra mármore")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        call_command("index_tenant_rag", "--tenant", self.other_tenant.slug)
        hits = admin_vector_search(
            tenant=self.tenant,
            query_text="pedra mármore",
            limit=10,
            provider=self.provider,
            config=self.embedding_config,
        )
        self.assertTrue(hits)
        self.assertTrue(all(hit.chunk_id in {chunk_a.id, chunk_b.id} for hit in hits))
        self.assertEqual(hits[0].score, hits[1].score)
        self.assertLessEqual(hits[0].chunk_id, hits[1].chunk_id)

    def test_admin_search_respects_max_results(self):
        for idx in range(6):
            self._create_chunk(
                tenant=self.tenant,
                configuration=self.config,
                file_id=f"doc-{idx}",
                text=f"conteúdo similar {idx}",
            )
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        hits = admin_vector_search(
            tenant=self.tenant,
            query_text="conteúdo similar",
            limit=10,
            provider=self.provider,
            config=self.embedding_config,
        )
        self.assertEqual(len(hits), 5)

    def test_dry_run_does_not_call_provider_or_persist(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        with patch.object(FakeEmbeddingProvider, "embed_texts") as mocked:
            out = StringIO()
            call_command("index_tenant_rag", "--tenant", self.tenant.slug, "--dry-run", stdout=out)
        mocked.assert_not_called()
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).count(), 0)
        self.assertIn("indexed=1", out.getvalue())
        run = TenantRagIndexRun.objects.get(tenant=self.tenant)
        self.assertTrue(run.dry_run)

    def test_command_requires_tenant(self):
        with self.assertRaises(CommandError):
            call_command("index_tenant_rag")

    def test_indexing_disabled_fails_closed(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        with override_settings(LIVIA_RAG_INDEXING_ENABLED=False):
            with self.assertRaises(CommandError):
                call_command("index_tenant_rag", "--tenant", self.tenant.slug)

    def test_no_google_drive_or_openai_or_public_retriever(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config, text="segredo interno")
        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Doc publico",
            slug="doc-publico",
            content="conteudo publico",
            status=KnowledgeDocument.Status.ACTIVE,
        )
        with patch("knowledge_base.rag.google_drive_inventory.build_google_drive_readonly_service") as drive:
            with patch("requests.post") as openai_post:
                with self.assertLogs("knowledge_base.rag.indexing", level="INFO") as logs:
                    call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        drive.assert_not_called()
        openai_post.assert_not_called()
        joined = " ".join(logs.output)
        self.assertNotIn("segredo interno", joined)
        self.assertNotIn("sk-", joined)
        snippets = retrieve_relevant_knowledge(self.tenant, "conteudo publico")
        self.assertTrue(snippets)
        self.assertFalse(
            any("segredo" in (snippet.excerpt or "").lower() for snippet in snippets)
        )
        # Public retriever still only uses KnowledgeDocument
        self.assertEqual(
            TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).count(),
            1,
        )

    def test_audit_events_are_safe(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config, text="texto secreto audit")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        actions = set(
            AuditEvent.objects.filter(tenant=self.tenant).values_list("action", flat=True)
        )
        self.assertIn(ACTION_TENANT_RAG_INDEX_STARTED, actions)
        self.assertIn(ACTION_TENANT_RAG_INDEX_COMPLETED, actions)
        self.assertNotIn(ACTION_TENANT_RAG_INDEX_FAILED, actions)
        for event in AuditEvent.objects.filter(tenant=self.tenant):
            blob = f"{event.metadata}{event.before_data}{event.after_data}"
            self.assertNotIn("texto secreto audit", blob)

    def test_previous_modes_still_require_exclusive_flags(self):
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug)
        with self.assertRaises(CommandError):
            call_command(
                "sync_tenant_rag",
                "--tenant",
                self.tenant.slug,
                "--inventory-only",
                "--build-chunks",
            )

    def test_stale_running_lock_can_recover(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config)
        self.config.last_index_status = TenantRagConfiguration.InventoryStatus.RUNNING
        self.config.last_index_started_at = timezone.now() - timedelta(seconds=10000)
        self.config.save(update_fields=["last_index_status", "last_index_started_at", "updated_at"])
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).count(), 1)

    def test_only_stale_skips_chunks_without_embedding(self):
        self._create_chunk(tenant=self.tenant, configuration=self.config, file_id="stale-old", text="versão 1")
        call_command("index_tenant_rag", "--tenant", self.tenant.slug)
        chunk_new = self._create_chunk(
            tenant=self.tenant,
            configuration=self.config,
            file_id="only-new",
            text="chunk novo sem embedding",
        )
        stale = TenantRagDocumentChunk.objects.get(manifest__drive_file_id="stale-old")
        stale.chunk_text = "versão 2"
        stale.chunk_sha256 = "e" * 64
        stale.save()
        with override_settings(LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v2"):
            out = StringIO()
            call_command(
                "index_tenant_rag",
                "--tenant",
                self.tenant.slug,
                "--only-stale",
                stdout=out,
            )
        self.assertIn("reindexed=1", out.getvalue())
        self.assertFalse(
            TenantRagChunkEmbedding.objects.filter(chunk=chunk_new, is_active=True).exists()
        )

    def test_limit_caps_pending_chunks(self):
        for idx in range(4):
            self._create_chunk(
                tenant=self.tenant,
                configuration=self.config,
                file_id=f"lim-{idx}",
                text=f"texto limit {idx}",
            )
        out = StringIO()
        call_command("index_tenant_rag", "--tenant", self.tenant.slug, "--limit", "2", stdout=out)
        self.assertIn("indexed=2", out.getvalue())
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=True).count(), 2)

    def test_acquire_lock_requires_configuration(self):
        lonely = Tenant.objects.create(name="Sem config", slug="sem-config")
        with self.assertRaises(Exception):
            acquire_tenant_index_lock(tenant=lonely, mode="index", run_id="x")
