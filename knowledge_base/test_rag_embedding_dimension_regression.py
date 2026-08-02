from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from knowledge_base.models import (
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
)
from knowledge_base.testing.rag_dimensions import (
    RAG_PRODUCTION_EMBEDDING_DIMENSION,
    rag_test_zero_vector,
)
from tenants.models import Tenant


@override_settings(
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=RAG_PRODUCTION_EMBEDDING_DIMENSION,
)
class RagEmbeddingDimensionRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Dim", slug="dim-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-dim",
            sync_enabled=True,
        )
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=self.config,
            drive_file_id="dim-file",
            name="Dim",
            mime_type="application/vnd.google-apps.document",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            normalized_text="texto",
            normalized_text_sha256="b" * 64,
            normalized_text_char_count=5,
            normalized_text_byte_count=5,
            exported_at=timezone.now(),
        )
        self.chunk = TenantRagDocumentChunk.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            staging=staging,
            ordinal=0,
            chunk_text="texto",
            chunk_sha256="c" * 64,
            source_text_sha256="b" * 64,
            chunk_config_signature="cfg",
            char_count=5,
            byte_count=5,
            start_char=0,
            end_char=5,
            status=TenantRagDocumentChunk.Status.ACTIVE,
            is_active=True,
        )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_rejects_dimension_metadata_mismatch(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL pgvector column vector(1536)")

        embedding = TenantRagChunkEmbedding(
            tenant=self.tenant,
            chunk=self.chunk,
            manifest=self.chunk.manifest,
            chunk_sha256=self.chunk.chunk_sha256,
            chunk_config_signature=self.chunk.chunk_config_signature,
            provider="fake",
            model="fake-embed-v1",
            dimension=8,
            embedding_config_signature="fake:fake-embed-v1:8:4",
            vector=rag_test_zero_vector(RAG_PRODUCTION_EMBEDDING_DIMENSION),
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            is_active=True,
            first_indexed_at=timezone.now(),
            last_indexed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            embedding.save()

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_accepts_matching_dimension_and_vector(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL pgvector column vector(1536)")

        vector = rag_test_zero_vector(RAG_PRODUCTION_EMBEDDING_DIMENSION)
        embedding = TenantRagChunkEmbedding.objects.create(
            tenant=self.tenant,
            chunk=self.chunk,
            manifest=self.chunk.manifest,
            chunk_sha256=self.chunk.chunk_sha256,
            chunk_config_signature=self.chunk.chunk_config_signature,
            provider="fake",
            model="fake-embed-v1",
            dimension=RAG_PRODUCTION_EMBEDDING_DIMENSION,
            embedding_config_signature=(
                f"fake:fake-embed-v1:{RAG_PRODUCTION_EMBEDDING_DIMENSION}:4"
            ),
            vector=vector,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
            is_active=True,
            first_indexed_at=timezone.now(),
            last_indexed_at=timezone.now(),
        )
        embedding.refresh_from_db()
        self.assertEqual(embedding.dimension, RAG_PRODUCTION_EMBEDDING_DIMENSION)
        self.assertEqual(len(embedding.vector), RAG_PRODUCTION_EMBEDDING_DIMENSION)
