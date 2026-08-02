from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import ACTION_TENANT_RAG_CONFIGURED, ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH, AuditEvent
from conversations.models import Conversation, HandoffRequest, Message
from knowledge_base.models import TenantRagConfiguration, TenantRagDriveFileManifest
from knowledge_base.rag.conversation_retrieval import RagRetrievalResult
from leads.models import LeadDraft
from tenants.models import Tenant, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_MAX_RETRIEVED_CHUNKS=5,
    LIVIA_RAG_MAX_CONTEXT_CHARS=3000,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
)
class KnowledgeBasePortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="kb-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="kb-viewer", password="pass")
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a-kb")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b-kb")
        self.config_a = TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            retrieval_enabled=False,
        )
        self.config_b = TenantRagConfiguration.objects.create(
            tenant=self.tenant_b,
            approved_folder_id="folder-b",
            retrieval_enabled=False,
        )
        TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant_a,
            configuration=self.config_a,
            drive_file_id="doc-a",
            name="Catalogo A",
            mime_type="text/plain",
        )
        TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant_b,
            configuration=self.config_b,
            drive_file_id="doc-b",
            name="Catalogo B",
            mime_type="text/plain",
        )

    def login_admin(self):
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def login_viewer(self):
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.viewer,
            role=TenantMembership.Role.VIEWER,
        )
        self.client.force_login(self.viewer)

    def test_requires_authentication(self):
        response = self.client.get(reverse("operations_portal:knowledge_base_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_dashboard_scoped_to_active_tenant(self):
        self.login_admin()
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.user,
            role=TenantMembership.Role.TENANT_ADMIN,
        )

        response = self.client.get(reverse("operations_portal:knowledge_base_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Documentos")
        self.assertContains(response, ">1<")

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catalogo A")
        self.assertNotContains(response, "Catalogo B")

        response = self.client.get(reverse("operations_portal:knowledge_base_documents"), {"tenant": self.tenant_b.pk})

    def test_viewer_cannot_update_configuration(self):
        self.login_viewer()
        response = self.client.post(
            reverse("operations_portal:knowledge_base_config"),
            {
                "tenant": self.tenant_a.pk,
                "retrieval_enabled": "on",
                "min_similarity_score": "0.3",
                "max_retrieved_chunks": "",
                "max_context_chars": "",
                "retrieval_timeout_seconds": "",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.config_a.refresh_from_db()
        self.assertFalse(self.config_a.retrieval_enabled)

    def test_admin_can_update_configuration_with_audit(self):
        self.login_admin()
        response = self.client.post(
            reverse("operations_portal:knowledge_base_config"),
            {
                "tenant": self.tenant_a.pk,
                "retrieval_enabled": "on",
                "min_similarity_score": "0.35",
                "max_retrieved_chunks": "2",
                "max_context_chars": "1200",
                "retrieval_timeout_seconds": "4",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.config_a.refresh_from_db()
        self.assertTrue(self.config_a.retrieval_enabled)
        self.assertEqual(self.config_a.max_retrieved_chunks, 2)
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant_a,
                action=ACTION_TENANT_RAG_CONFIGURED,
            ).exists()
        )

    def test_invalid_configuration_is_rejected(self):
        self.login_admin()
        response = self.client.post(
            reverse("operations_portal:knowledge_base_config"),
            {
                "tenant": self.tenant_a.pk,
                "retrieval_enabled": "",
                "min_similarity_score": "",
                "max_retrieved_chunks": "999",
                "max_context_chars": "",
                "retrieval_timeout_seconds": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não pode exceder o limite global")
        self.config_a.refresh_from_db()
        self.assertIsNone(self.config_a.max_retrieved_chunks)

    def test_diagnostic_search_has_no_chat_side_effects(self):
        self.login_admin()
        before = {
            "conversations": Conversation.objects.filter(tenant=self.tenant_a).count(),
            "messages": Message.objects.filter(conversation__tenant=self.tenant_a).count(),
            "leads": LeadDraft.objects.filter(tenant=self.tenant_a).count(),
            "handoffs": HandoffRequest.objects.filter(tenant=self.tenant_a).count(),
        }
        skipped = RagRetrievalResult(
            chunks=[],
            status="skipped",
            reason="tenant_retrieval_disabled",
            duration_ms=1,
            threshold=0.25,
            max_chunks=5,
            max_context_chars=3000,
            provider="fake",
            model="fake-embed-v1",
            max_score=0.0,
        )
        with patch("operations_portal.knowledge_base_services.retrieve_context", return_value=skipped):
            response = self.client.post(
                reverse("operations_portal:knowledge_base_diagnostic"),
                {"tenant": self.tenant_a.pk, "query": "mármore para bancada"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.filter(tenant=self.tenant_a).count(), before["conversations"])
        self.assertEqual(Message.objects.filter(conversation__tenant=self.tenant_a).count(), before["messages"])
        self.assertEqual(LeadDraft.objects.filter(tenant=self.tenant_a).count(), before["leads"])
        self.assertEqual(HandoffRequest.objects.filter(tenant=self.tenant_a).count(), before["handoffs"])
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant_a,
                action=ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH,
            ).exists()
        )

    def test_diagnostic_failure_renders_without_http_500(self):
        self.login_admin()

        with patch(
            "operations_portal.knowledge_base_services.retrieve_context",
            side_effect=RuntimeError("provider down"),
        ):
            response = self.client.post(
                reverse("operations_portal:knowledge_base_diagnostic"),
                {"tenant": self.tenant_a.pk, "query": "consulta curta"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "provider_or_runtime")
