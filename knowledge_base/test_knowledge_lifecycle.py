from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from knowledge_base.models import (
    KnowledgeDocument,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
)
from knowledge_base.rag.retriever import retrieve_relevant_knowledge
from knowledge_base.services.importing import import_tenant_knowledge_path
from knowledge_base.services.lifecycle import (
    IMPORT_CREATED,
    IMPORT_UNCHANGED,
    IMPORT_UPDATED,
    INDEX_COMPLETED,
    INDEX_FAILED,
    READINESS_DEGRADED,
    READINESS_EMPTY,
    READINESS_READY,
    READINESS_STALE,
    KnowledgeLifecycleService,
    compute_document_fingerprint,
)
from knowledge_base.testing.rag_dimensions import RagTestDimensionMixin
from tenants.models import Tenant
from tenants.services.onboarding import TenantOnboardingService


@override_settings(
    LIVIA_RAG_INDEXING_ENABLED=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_BATCH_SIZE=2,
    LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS=5,
    LIVIA_RAG_EMBEDDING_MAX_RETRIES=1,
    LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS=0,
    LIVIA_RAG_EMBEDDING_API_KEY="",
    LIVIA_RAG_ENABLED=False,
    LIVIA_RAG_DRY_RUN=True,
)
class KnowledgeLifecycleServiceTests(RagTestDimensionMixin, TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="manual-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.other_config = TenantRagConfiguration.objects.create(
            tenant=self.other_tenant,
            approved_folder_id="manual-b",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.service = KnowledgeLifecycleService()

    def upsert(self, *, tenant=None, slug="doc-a", content="Mármore Carrara para bancada de cozinha."):
        tenant = tenant or self.tenant
        return self.service.upsert_document(
            tenant=tenant,
            title=slug.replace("-", " ").title(),
            slug=slug,
            content=content,
            source_type="manual",
            tags=["marmore"],
        )

    def test_fingerprint_is_deterministic_and_tenant_scoped(self):
        first = compute_document_fingerprint(tenant=self.tenant, title="Doc", content="Mesmo conteúdo")
        second = compute_document_fingerprint(tenant=self.tenant, title="Doc", content="Mesmo conteúdo")
        other = compute_document_fingerprint(tenant=self.other_tenant, title="Doc", content="Mesmo conteúdo")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 64)

    def test_import_same_content_is_unchanged_and_changed_content_is_stale(self):
        first = self.upsert()
        second = self.upsert()
        third = self.upsert(content="Mármore Carrara alterado para banheiro.")

        self.assertEqual(first.status, IMPORT_CREATED)
        self.assertEqual(second.status, IMPORT_UNCHANGED)
        self.assertEqual(third.status, IMPORT_UPDATED)
        self.assertEqual(KnowledgeDocument.objects.filter(tenant=self.tenant, slug="doc-a").count(), 1)
        document = KnowledgeDocument.objects.get(tenant=self.tenant, slug="doc-a")
        self.assertEqual(document.lifecycle_status, KnowledgeDocument.LifecycleStatus.STALE)

    def test_reindex_document_is_idempotent_and_marks_ready(self):
        result = self.upsert()

        indexed = self.service.reindex_document(tenant=self.tenant, document_id=result.document.pk)
        second = self.service.reindex_document(tenant=self.tenant, document_id=result.document.pk)

        self.assertEqual(indexed.status, INDEX_COMPLETED)
        self.assertEqual(second.status, INDEX_COMPLETED)
        document = KnowledgeDocument.objects.get(pk=result.document.pk)
        self.assertEqual(document.lifecycle_status, KnowledgeDocument.LifecycleStatus.INDEXED)
        self.assertEqual(document.content_sha256, document.indexed_content_sha256)
        self.assertEqual(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count(), 1)
        self.assertEqual(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=True).count(), 1)
        self.assertEqual(self.service.readiness(tenant=self.tenant).status, READINESS_READY)

    def test_disabled_failed_and_stale_documents_are_not_keyword_retrieved(self):
        ready = self.upsert(slug="ready-doc", content="Produto Alpha disponível para orçamento.")
        stale = self.upsert(slug="stale-doc", content="Produto Beta não indexado.")
        disabled = self.upsert(slug="disabled-doc", content="Produto Gamma arquivado.")
        failed = self.upsert(slug="failed-doc", content="Produto Delta falhou.")
        self.service.reindex_document(tenant=self.tenant, document_id=ready.document.pk)
        self.service.disable_document(tenant=self.tenant, document_id=disabled.document.pk)
        KnowledgeDocument.objects.filter(pk=failed.document.pk).update(lifecycle_status=KnowledgeDocument.LifecycleStatus.FAILED)

        titles = [item.title for item in retrieve_relevant_knowledge(self.tenant, "Produto Alpha Beta Gamma Delta orçamento", limit=10)]

        self.assertIn("Ready Doc", titles)
        self.assertNotIn("Stale Doc", titles)
        self.assertNotIn("Disabled Doc", titles)
        self.assertNotIn("Failed Doc", titles)
        self.assertEqual(KnowledgeDocument.objects.get(pk=stale.document.pk).lifecycle_status, KnowledgeDocument.LifecycleStatus.STALE)

    def test_tenant_isolation_for_disable_reindex_chunks_and_retrieval(self):
        own = self.upsert(slug="tenant-a-doc", content="Conteúdo exclusivo do tenant A.")
        other = self.upsert(tenant=self.other_tenant, slug="tenant-b-doc", content="Conteúdo exclusivo do tenant B.")
        self.service.reindex_document(tenant=self.tenant, document_id=own.document.pk)
        self.service.reindex_document(tenant=self.other_tenant, document_id=other.document.pk)

        with self.assertRaises(KnowledgeDocument.DoesNotExist):
            self.service.disable_document(tenant=self.tenant, document_id=other.document.pk)
        with self.assertRaises(KnowledgeDocument.DoesNotExist):
            self.service.reindex_document(tenant=self.tenant, document_id=other.document.pk)

        self.assertFalse(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest__tenant=self.other_tenant).exists())
        titles = [item.title for item in retrieve_relevant_knowledge(self.tenant, "tenant B exclusivo", limit=10)]
        self.assertNotIn("Tenant B Doc", titles)

    def test_index_failure_marks_document_failed_and_readiness_degraded(self):
        result = self.upsert(slug="failure-doc", content="Conteúdo com falha controlada.")

        with patch("knowledge_base.services.lifecycle.run_chunk_build_for_tenant", side_effect=RuntimeError("forced lifecycle failure")):
            indexed = self.service.reindex_document(tenant=self.tenant, document_id=result.document.pk)

        document = KnowledgeDocument.objects.get(pk=result.document.pk)
        self.assertEqual(indexed.status, INDEX_FAILED)
        self.assertEqual(document.lifecycle_status, KnowledgeDocument.LifecycleStatus.FAILED)
        self.assertNotIn("Conteúdo", [item.title for item in retrieve_relevant_knowledge(self.tenant, "falha controlada")])
        self.assertEqual(self.service.readiness(tenant=self.tenant).status, READINESS_DEGRADED)

    def test_readiness_empty_and_stale(self):
        empty_tenant = Tenant.objects.create(name="Empty", slug="empty")
        self.assertEqual(self.service.readiness(tenant=empty_tenant).status, READINESS_EMPTY)
        self.upsert(slug="stale-only", content="Documento aguardando índice.")
        self.assertEqual(self.service.readiness(tenant=self.tenant).status, READINESS_STALE)

    def test_import_command_reuses_lifecycle_service_and_reports_unchanged(self):
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "manual.md"
            source.write_text("Conteúdo manual idempotente.", encoding="utf-8")
            first = import_tenant_knowledge_path(tenant=self.tenant, source=source, replace=True)
            second = import_tenant_knowledge_path(tenant=self.tenant, source=source, replace=True)

        self.assertEqual(first.created, 1)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(KnowledgeDocument.objects.filter(tenant=self.tenant, slug="manual").count(), 1)

    def test_status_and_reindex_commands_are_service_adapters(self):
        result = self.upsert(slug="command-doc", content="Documento de comando.")
        status_output = StringIO()
        call_command("tenant_knowledge_status", "--tenant", self.tenant.slug, stdout=status_output)
        self.assertIn("status=STALE", status_output.getvalue())

        dry_output = StringIO()
        call_command("reindex_tenant_knowledge", "--tenant", self.tenant.slug, "--document-id", str(result.document.pk), "--dry-run", stdout=dry_output)
        self.assertIn("DRY RUN", dry_output.getvalue())
        self.assertIn("REINDEX_REQUIRED", dry_output.getvalue())

    def test_onboarding_uses_lifecycle_readiness_for_seeded_knowledge(self):
        onboarded = TenantOnboardingService().onboard(
            slug="seeded-lifecycle",
            name="Seeded Lifecycle",
            domain="https://seeded.example",
            seed_knowledge=True,
        )

        self.assertEqual(onboarded.knowledge_status, READINESS_STALE)
