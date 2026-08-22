from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import ACTION_KNOWLEDGE_DOCUMENT_CREATED, ACTION_KNOWLEDGE_DOCUMENT_UPDATED, AuditEvent
from conversations.models import Conversation, HandoffRequest, Message
from integrations.models import TenantWebhookConfig
from knowledge_base.models import KnowledgeDocument, TenantRagChunkEmbedding, TenantRagConfiguration, TenantRagDocumentChunk, TenantRagDriveFileManifest, TenantRagDriveTextStaging, TenantRagOperationRequest
from leads.models import LeadDraft
from tenants.models import Tenant, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class KnowledgeDocumentPortalCrudTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="kb-doc-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="kb-doc-viewer", password="pass")
        self.outsider = get_user_model().objects.create_user(username="kb-doc-outsider", password="pass")
        self.tenant = Tenant.objects.create(name="Tenant Docs A", slug="tenant-docs-a", domain="https://docs-a.example")
        self.other_tenant = Tenant.objects.create(name="Tenant Docs B", slug="tenant-docs-b", domain="https://docs-b.example")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)
        self.document = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Catálogo Mármore",
            slug="catalogo-marmore",
            content="Mármore branco para bancada e escada.",
            source_type="manual",
            source_url="https://docs-a.example/catalogo",
            tags=["marmore", "bancada"],
        )
        self.other_document = KnowledgeDocument.objects.create(
            tenant=self.other_tenant,
            title="Documento Isolado",
            slug="documento-isolado",
            content="Conteúdo de outro tenant.",
            tags=["isolado"],
        )

    def login_admin(self):
        self.client.force_login(self.admin)

    def login_viewer(self):
        self.client.force_login(self.viewer)

    def test_authorized_list_filters_by_tenant_and_search(self):
        self.login_admin()

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catálogo Mármore")
        self.assertContains(response, "Novo documento")
        self.assertNotContains(response, "Documento Isolado")

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk, "q": "bancada"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catálogo Mármore")

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk, "q": "isolado"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Documento Isolado")
        self.assertContains(response, "Nenhum KnowledgeDocument")

    def test_access_denied_without_membership(self):
        self.client.force_login(self.outsider)

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 403)

    def test_viewer_can_read_but_cannot_write(self):
        self.login_viewer()

        response = self.client.get(reverse("operations_portal:knowledge_base_document_detail", kwargs={"pk": self.document.pk}), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Editar</a>")

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_create"),
            {
                "tenant": self.tenant.pk,
                "title": "Novo",
                "slug": "novo",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "",
                "tags_text": "teste",
                "content": "conteúdo",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(KnowledgeDocument.objects.filter(tenant=self.tenant, slug="novo").exists())

    def test_create_document_with_tags_and_audit(self):
        self.login_admin()

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_create"),
            {
                "tenant": self.tenant.pk,
                "title": "FAQ Granitos",
                "slug": "faq-granitos",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "https://docs-a.example/faq",
                "tags_text": "granito, faq\npreço",
                "content": "Granitos têm prazos e acabamentos específicos.",
            },
        )

        self.assertEqual(response.status_code, 302)
        document = KnowledgeDocument.objects.get(tenant=self.tenant, slug="faq-granitos")
        self.assertEqual(document.tags, ["granito", "faq", "preço"])
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action=ACTION_KNOWLEDGE_DOCUMENT_CREATED).exists())

    def test_edit_document_status_source_url_and_tags(self):
        self.login_admin()

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_edit", kwargs={"pk": self.document.pk}),
            {
                "tenant": self.tenant.pk,
                "title": "Catálogo Mármore Atualizado",
                "slug": "catalogo-marmore",
                "status": KnowledgeDocument.Status.ARCHIVED,
                "source_type": "manual",
                "source_url": "https://docs-a.example/novo",
                "tags_text": "marmore, arquivado",
                "content": "Conteúdo atualizado.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, KnowledgeDocument.Status.ARCHIVED)
        self.assertEqual(self.document.source_url, "https://docs-a.example/novo")
        self.assertEqual(self.document.tags, ["marmore", "arquivado"])
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action=ACTION_KNOWLEDGE_DOCUMENT_UPDATED).exists())

    def test_invalid_source_url_is_rejected(self):
        self.login_admin()

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_edit", kwargs={"pk": self.document.pk}),
            {
                "tenant": self.tenant.pk,
                "title": "Catálogo",
                "slug": "catalogo-marmore",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "not-a-url",
                "tags_text": "marmore",
                "content": "Conteúdo",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe uma URL válida")
        self.document.refresh_from_db()
        self.assertEqual(self.document.source_url, "https://docs-a.example/catalogo")

    def test_tenant_isolation_rejects_foreign_document_and_posted_tenant_mismatch(self):
        self.login_admin()

        response = self.client.get(
            reverse("operations_portal:knowledge_base_document_detail", kwargs={"pk": self.other_document.pk}),
            {"tenant": self.tenant.pk},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_edit", kwargs={"pk": self.document.pk}),
            {
                "tenant": self.other_tenant.pk,
                "title": "Ataque",
                "slug": "catalogo-marmore",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "",
                "tags_text": "",
                "content": "Ataque",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.document.refresh_from_db()
        self.assertNotEqual(self.document.title, "Ataque")

    def test_import_upload_uses_shared_service_and_validates_format(self):
        self.login_admin()

        upload = SimpleUploadedFile("guia-atendimento.md", b"Atendimento VIP para bancadas.", content_type="text/markdown")
        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_import"),
            {
                "tenant": self.tenant.pk,
                "file": upload,
                "source_type": "import",
                "tags_text": "vip, atendimento",
                "status": KnowledgeDocument.Status.ACTIVE,
            },
        )

        self.assertEqual(response.status_code, 302)
        imported = KnowledgeDocument.objects.get(tenant=self.tenant, slug="guia-atendimento")
        self.assertEqual(imported.tags, ["vip", "atendimento"])
        self.assertEqual(imported.source_type, "import")

        invalid = SimpleUploadedFile("planilha.xlsx", b"nope", content_type="application/octet-stream")
        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_import"),
            {
                "tenant": self.tenant.pk,
                "file": invalid,
                "source_type": "import",
                "status": KnowledgeDocument.Status.ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formato não suportado")

    def test_text_retrieval_is_tenant_scoped_and_has_no_side_effects(self):
        KnowledgeDocument.objects.create(
            tenant=self.other_tenant,
            title="Outro Bancada",
            slug="outro-bancada",
            content="Bancada secreta de outro tenant.",
            tags=["bancada"],
        )
        before = {
            "conversations": Conversation.objects.count(),
            "messages": Message.objects.count(),
            "leads": LeadDraft.objects.count(),
            "handoffs": HandoffRequest.objects.count(),
            "webhooks": TenantWebhookConfig.objects.count(),
        }
        self.login_admin()

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_detail", kwargs={"pk": self.document.pk}),
            {"tenant": self.tenant.pk, "query": "bancada marmore"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catálogo Mármore")
        self.assertNotContains(response, "Bancada secreta")
        self.assertEqual(Conversation.objects.count(), before["conversations"])
        self.assertEqual(Message.objects.count(), before["messages"])
        self.assertEqual(LeadDraft.objects.count(), before["leads"])
        self.assertEqual(HandoffRequest.objects.count(), before["handoffs"])
        self.assertEqual(TenantWebhookConfig.objects.count(), before["webhooks"])

    def test_tenant_detail_links_to_filtered_knowledge_base(self):
        self.login_admin()

        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("operations_portal:knowledge_base_documents") + f"?tenant={self.tenant.pk}")

    def test_detail_shows_existing_rag_status_without_creating_pipeline(self):
        config = TenantRagConfiguration.objects.create(tenant=self.tenant, approved_folder_id="folder-a")
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=config,
            drive_file_id="drive-a",
            name="Drive A",
            mime_type="text/plain",
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            normalized_text="Texto",
            normalized_text_sha256="abc",
            exported_at="2026-01-01T00:00:00Z",
        )
        chunk = TenantRagDocumentChunk.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            staging=staging,
            ordinal=0,
            chunk_text="Texto",
            chunk_sha256="c",
            source_text_sha256="abc",
            chunk_config_signature="sig",
        )
        TenantRagChunkEmbedding.objects.create(
            tenant=self.tenant,
            chunk=chunk,
            manifest=manifest,
            chunk_sha256="c",
            chunk_config_signature="sig",
            provider="fake",
            model="fake",
            dimension=8,
            embedding_config_signature="e",
            vector=[0.0] * 8,
        )
        self.login_admin()

        response = self.client.get(
            reverse("operations_portal:knowledge_base_document_detail", kwargs={"pk": self.document.pk}),
            {"tenant": self.tenant.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chunks deste documento")
        self.assertContains(response, "Embeddings deste documento")
        self.assertContains(response, reverse("operations_portal:knowledge_base_operations") + f"?tenant={self.tenant.pk}")

    def test_no_secret_values_are_rendered_or_audited(self):
        self.login_admin()

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "secret")
        for event in AuditEvent.objects.all():
            self.assertNotIn("secret", str(event.before_data).lower())
            self.assertNotIn("secret", str(event.after_data).lower())


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_INDEXING_ENABLED=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=False,
)
class KnowledgeDocumentManualRagPipelineTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="manual-rag-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="manual-rag-viewer", password="pass")
        self.tenant = Tenant.objects.create(name="Manual RAG A", slug="manual-rag-a")
        self.other_tenant = Tenant.objects.create(name="Manual RAG B", slug="manual-rag-b")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-manual-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.other_config = TenantRagConfiguration.objects.create(
            tenant=self.other_tenant,
            approved_folder_id="folder-manual-b",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def _login_admin(self):
        self.client.force_login(self.admin)

    def test_manual_create_materializes_manifest_and_staging_without_embeddings(self):
        self._login_admin()
        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_create"),
            {
                "tenant": self.tenant.pk,
                "title": "Manual Cozinhas",
                "slug": "manual-cozinhas",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "",
                "tags_text": "cozinha",
                "content": "Bancadas de cozinha com cuba e cooktop definidos no projeto.",
            },
        )
        self.assertEqual(response.status_code, 302)
        document = KnowledgeDocument.objects.get(tenant=self.tenant, slug="manual-cozinhas")
        manifest = TenantRagDriveFileManifest.objects.get(tenant=self.tenant, drive_file_id=f"manual-knowledge-document-{document.pk}")
        self.assertEqual(manifest.configuration, self.config)
        self.assertEqual(manifest.status, TenantRagDriveFileManifest.Status.EXPORTED)
        self.assertEqual(manifest.text_staging.tenant, self.tenant)
        self.assertIn("Bancadas de cozinha", manifest.text_staging.normalized_text)
        self.assertFalse(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant).exists())

    def test_manual_edit_marks_manifest_updated_and_archive_deactivates_chunks_embeddings(self):
        from knowledge_base.services.manual_rag import sync_manual_knowledge_document_to_rag
        from knowledge_base.rag.sync import run_chunk_build_for_tenant
        from knowledge_base.rag.indexing import run_index_for_tenant

        document = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Manual Banheiros",
            slug="manual-banheiros",
            status=KnowledgeDocument.Status.ACTIVE,
            content="Cubas esculpidas para banheiro.",
        )
        sync_manual_knowledge_document_to_rag(document=document)
        run_chunk_build_for_tenant(configuration=self.config)
        run_index_for_tenant(configuration=self.config)
        self.assertTrue(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, is_active=True).exists())

        self._login_admin()
        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_edit", kwargs={"pk": document.pk}),
            {
                "tenant": self.tenant.pk,
                "title": "Manual Banheiros",
                "slug": "manual-banheiros",
                "status": KnowledgeDocument.Status.ACTIVE,
                "source_type": "manual",
                "source_url": "",
                "tags_text": "banheiro",
                "content": "Cubas esculpidas e nichos sob medida para banheiro.",
            },
        )
        self.assertEqual(response.status_code, 302)
        manifest = TenantRagDriveFileManifest.objects.get(tenant=self.tenant, drive_file_id=f"manual-knowledge-document-{document.pk}")
        self.assertEqual(manifest.status, TenantRagDriveFileManifest.Status.UPDATED)

        response = self.client.post(
            reverse("operations_portal:knowledge_base_document_edit", kwargs={"pk": document.pk}),
            {
                "tenant": self.tenant.pk,
                "title": "Manual Banheiros",
                "slug": "manual-banheiros",
                "status": KnowledgeDocument.Status.ARCHIVED,
                "source_type": "manual",
                "source_url": "",
                "tags_text": "banheiro",
                "content": "Arquivado.",
            },
        )
        self.assertEqual(response.status_code, 302)
        manifest.refresh_from_db()
        self.assertFalse(manifest.is_active)
        self.assertEqual(manifest.status, TenantRagDriveFileManifest.Status.REMOVED)
        self.assertFalse(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).exists())
        self.assertFalse(TenantRagChunkEmbedding.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).exists())

    def test_build_chunks_operation_syncs_manual_docs_tenant_scoped_and_does_not_duplicate_unchanged(self):
        from knowledge_base.rag.operations import create_operation_request, execute_operation_request

        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Manual Escadas",
            slug="manual-escadas",
            status=KnowledgeDocument.Status.ACTIVE,
            content="Escadas com degraus, espelhos e patamares sob medida.",
        )
        KnowledgeDocument.objects.create(
            tenant=self.other_tenant,
            title="Outro Secreto",
            slug="outro-secreto",
            status=KnowledgeDocument.Status.ACTIVE,
            content="Conteúdo de outro tenant não pode entrar aqui.",
        )
        request_obj = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.BUILD_CHUNKS,
            requested_by=self.admin,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertEqual(finished.status, TenantRagOperationRequest.Status.SUCCEEDED)
        self.assertEqual(finished.counters["manual_synced"], 1)
        self.assertTrue(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, chunk_text__icontains="Escadas").exists())
        self.assertFalse(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, chunk_text__icontains="outro tenant").exists())
        first_count = TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count()

        request_again = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.BUILD_CHUNKS,
            requested_by=self.admin,
        )
        execute_operation_request(request_id=request_again.pk)
        self.assertEqual(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count(), first_count)

    def test_viewer_cannot_submit_processing_operation(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("operations_portal:knowledge_base_operation_submit"),
            {"tenant": self.tenant.pk, "operation": "build_chunks"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TenantRagOperationRequest.objects.filter(tenant=self.tenant).exists())

    def test_dashboard_and_document_list_show_manual_and_drive_state_without_secrets(self):
        self._login_admin()
        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Manual Contato",
            slug="manual-contato",
            status=KnowledgeDocument.Status.ACTIVE,
            content="Contato por telefone ou WhatsApp.",
        )
        from knowledge_base.services.manual_rag import sync_manual_knowledge_documents_for_tenant

        sync_manual_knowledge_documents_for_tenant(tenant=self.tenant)
        response = self.client.get(reverse("operations_portal:knowledge_base_dashboard"), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fonte Google Drive")
        self.assertContains(response, "Upload manual")
        self.assertNotContains(response, "secret")

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "STALE")
        self.assertContains(response, "Manual Contato")
