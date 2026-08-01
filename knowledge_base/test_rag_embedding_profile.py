from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from knowledge_base.models import (
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
)
from knowledge_base.rag.conversation_retrieval import retrieve_context
from knowledge_base.rag.embeddings import FakeEmbeddingProvider, load_embedding_config
from knowledge_base.rag.embedding_profile import (
    EmbeddingOperationalState,
    classify_embedding,
    ensure_config_schema_compatible,
    load_embedding_profile,
)
from knowledge_base.rag.embeddings import EmbeddingConfigurationError
from knowledge_base.rag.eval.runner import EvalCase, run_eval_for_tenant
from tenants.models import Tenant


@override_settings(
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_EMBEDDING_BATCH_SIZE=4,
)
class EmbeddingProfileTests(TestCase):
    def test_profile_key_and_signature_deterministic(self):
        first = load_embedding_profile(validate_schema=False)
        second = load_embedding_profile(validate_schema=False)
        self.assertEqual(first.profile_key, "fake:fake-embed-v1:8")
        self.assertEqual(first.signature, second.signature)
        self.assertEqual(first.signature, load_embedding_config().signature)

    @patch("knowledge_base.rag.embedding_profile.database_vector_column_dimension", return_value=1536)
    def test_schema_mismatch_fails_closed(self, _mock_dim):
        with self.assertRaises(EmbeddingConfigurationError):
            ensure_config_schema_compatible(load_embedding_config())

    @patch("knowledge_base.rag.embedding_profile.database_vector_column_dimension", return_value=8)
    def test_schema_match_allows_operations(self, _mock_dim):
        ensure_config_schema_compatible(load_embedding_config())

    def test_classify_stale_signature(self):
        profile = load_embedding_profile(validate_schema=False)
        emb = TenantRagChunkEmbedding(
            provider=profile.provider,
            model=profile.model,
            dimension=profile.dimension,
            embedding_config_signature="other",
            vector=[0.1] * profile.dimension,
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
        )
        self.assertEqual(classify_embedding(emb, profile=profile), EmbeddingOperationalState.STALE)


@override_settings(
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=False,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_MIN_SIMILARITY_SCORE=0.01,
    LIVIA_RAG_MAX_RETRIEVED_CHUNKS=3,
    LIVIA_RAG_VECTOR_CANDIDATE_LIMIT=10,
)
class RagEvalRunnerTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Grani", slug="granimarmores-pitondo")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.provider = FakeEmbeddingProvider()
        self.embedding_config = load_embedding_config()
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=self.config,
            drive_file_id="doc",
            name="Doc materiais",
            mime_type="application/vnd.google-apps.document",
            relative_path="/doc",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            normalized_text="mármore travertino para bancada e granito preto",
            normalized_text_sha256="doc-src".ljust(64, "0")[:64],
            normalized_text_char_count=40,
            normalized_text_byte_count=40,
            exported_at=timezone.now(),
        )
        chunk = TenantRagDocumentChunk.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            staging=staging,
            ordinal=0,
            chunk_text="mármore travertino para bancada e granito preto",
            chunk_sha256="doc-0".ljust(64, "0")[:64],
            source_text_sha256=staging.normalized_text_sha256,
            chunk_config_signature="chunk-cfg",
            char_count=40,
            byte_count=40,
            start_char=0,
            end_char=40,
            status=TenantRagDocumentChunk.Status.ACTIVE,
            is_active=True,
        )
        vector = self.provider.embed_texts([chunk.chunk_text], config=self.embedding_config)[0]
        TenantRagChunkEmbedding.objects.create(
            tenant=self.tenant,
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

    def test_eval_expected_hit_and_empty(self):
        cases = [
            EvalCase(
                case_id="hit",
                query="mármore travertino para bancada e granito preto",
                expect="hit",
                expected_source_contains=["mármore"],
            ),
            EvalCase(case_id="empty", query="distancia terra marte astronomia", expect="empty"),
        ]
        report = run_eval_for_tenant(tenant=self.tenant, cases=cases, provider=self.provider)
        hit_case = next(item for item in report.results if item.case_id == "hit")
        self.assertEqual(hit_case.status, "completed")
        self.assertTrue(hit_case.hit)

    def test_tenant_retrieval_disabled_skips(self):
        TenantRagConfiguration.objects.filter(tenant=self.tenant).update(retrieval_enabled=False)
        result = retrieve_context(
            tenant=self.tenant,
            query="mármore",
            provider=self.provider,
            config=self.embedding_config,
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "tenant_retrieval_disabled")


class FakeEmbeddingProviderGuardTests(SimpleTestCase):
    @override_settings(RUNNING_TESTS=False, LIVIA_ALLOW_FAKE_EMBEDDINGS=False, LIVIA_RAG_EMBEDDING_PROVIDER="fake")
    def test_fake_provider_blocked_outside_tests(self):
        with self.assertRaises(EmbeddingConfigurationError):
            load_embedding_config()

    @override_settings(
        RUNNING_TESTS=False,
        LIVIA_ALLOW_FAKE_EMBEDDINGS=False,
        LIVIA_ENVIRONMENT="staging",
        LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    )
    def test_fake_provider_blocked_in_staging_even_with_allow_flag_off(self):
        with self.assertRaises(EmbeddingConfigurationError) as ctx:
            load_embedding_config()
        self.assertIn("staging", str(ctx.exception).lower())

    @override_settings(
        RUNNING_TESTS=False,
        LIVIA_ALLOW_FAKE_EMBEDDINGS=True,
        LIVIA_ENVIRONMENT="development",
        LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    )
    def test_fake_provider_allowed_when_explicitly_opted_in(self):
        cfg = load_embedding_config()
        self.assertEqual(cfg.provider, "fake")

    @override_settings(
        RUNNING_TESTS=False,
        LIVIA_ALLOW_FAKE_EMBEDDINGS=True,
        LIVIA_ENVIRONMENT="staging",
        LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    )
    def test_fake_provider_blocked_in_staging_even_with_allow_flag(self):
        with self.assertRaises(EmbeddingConfigurationError):
            load_embedding_config()
