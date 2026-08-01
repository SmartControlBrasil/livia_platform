from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import ACTION_TENANT_RAG_OPERATION_REJECTED, ACTION_TENANT_RAG_OPERATION_REQUESTED, AuditEvent
from conversations.models import Conversation, HandoffRequest, Message
from knowledge_base.models import TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operations import execute_operation_request, recover_stale_operation_requests
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
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_OPERATIONS_ENABLED=False,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
)
class KnowledgeBaseOperationsPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="ops-admin", password="pass")
        self.manager = get_user_model().objects.create_user(username="ops-manager", password="pass")
        self.viewer = get_user_model().objects.create_user(username="ops-viewer", password="pass")
        self.tenant_a = Tenant.objects.create(name="Tenant Ops A", slug="tenant-ops-a")
        self.tenant_b = Tenant.objects.create(name="Tenant Ops B", slug="tenant-ops-b")
        self.config_a = TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=False,
        )
        self.config_b = TenantRagConfiguration.objects.create(
            tenant=self.tenant_b,
            approved_folder_id="folder-b",
            sync_enabled=True,
            retrieval_enabled=False,
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.manager, role=TenantMembership.Role.MANAGER)
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def _login(self, user):
        self.client.force_login(user)

    def _operations_url(self, tenant=None):
        tenant = tenant or self.tenant_a
        return f"{reverse('operations_portal:knowledge_base_operations')}?tenant={tenant.pk}"

    def _submit_url(self):
        return reverse("operations_portal:knowledge_base_operation_submit")

    def test_requires_authentication(self):
        response = self.client.get(self._operations_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_viewer_can_open_panel_without_500(self):
        self._login(self.viewer)
        response = self.client.get(self._operations_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atualização da base")
        self.assertContains(response, "Operações desabilitadas globalmente")

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=False)
    def test_global_gate_blocks_post(self):
        self._login(self.admin)
        response = self.client.post(
            self._submit_url(),
            {
                "tenant": self.tenant_a.pk,
                "operation": "inventory",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantRagOperationRequest.objects.exists())
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_TENANT_RAG_OPERATION_REJECTED, tenant=self.tenant_a).exists()
        )

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True, LIVIA_RAG_OPERATIONS_DRY_RUN=True)
    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_create_valid_request_dry_run(self, mock_drive):
        self._login(self.admin)
        response = self.client.post(
            self._submit_url(),
            {
                "tenant": self.tenant_a.pk,
                "operation": "inventory",
            },
        )
        self.assertEqual(response.status_code, 302)
        request_obj = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        self.assertEqual(request_obj.operation, TenantRagOperationRequest.Operation.INVENTORY)
        self.assertTrue(request_obj.dry_run)
        self.assertEqual(request_obj.status, TenantRagOperationRequest.Status.PENDING)
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_TENANT_RAG_OPERATION_REQUESTED, tenant=self.tenant_a).exists()
        )
        mock_drive.assert_not_called()

        execute_operation_request(request_id=request_obj.pk)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, TenantRagOperationRequest.Status.SUCCEEDED)
        self.assertIn("discovered", request_obj.counters)
        mock_drive.assert_not_called()

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True, LIVIA_RAG_OPERATIONS_DRY_RUN=True)
    def test_duplicate_request_blocked(self):
        self._login(self.admin)
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.PENDING,
            dry_run=True,
            run_id="pending-run",
        )
        response = self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_a.pk, "operation": "sync_export"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TenantRagOperationRequest.objects.filter(tenant=self.tenant_a).count(), 1)

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True)
    def test_post_with_different_tenant_denied(self):
        self._login(self.admin)
        response = self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_b.pk, "operation": "inventory"},
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True)
    def test_invalid_operation_rejected(self):
        self._login(self.admin)
        response = self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_a.pk, "operation": "shell_exec"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantRagOperationRequest.objects.exists())

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True)
    def test_reindex_requires_permission_and_confirmation(self):
        self._login(self.manager)
        response = self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_a.pk, "operation": "full_reindex"},
        )
        self.assertEqual(response.status_code, 403)

        self._login(self.admin)
        response = self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_a.pk, "operation": "full_reindex"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantRagOperationRequest.objects.filter(operation="full_reindex").exists())

        response = self.client.post(
            self._submit_url(),
            {
                "tenant": self.tenant_a.pk,
                "operation": "full_reindex",
                "confirm_reindex": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantRagOperationRequest.objects.filter(operation="full_reindex").exists())

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True)
    def test_tenant_isolation_on_detail(self):
        self._login(self.admin)
        foreign = TenantRagOperationRequest.objects.create(
            tenant=self.tenant_b,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.SUCCEEDED,
            dry_run=True,
            run_id="tenant-b-run",
        )
        response = self.client.get(
            f"{reverse('operations_portal:knowledge_base_operation_detail', kwargs={'pk': foreign.pk})}?tenant={self.tenant_a.pk}"
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True, LIVIA_RAG_OPERATIONS_DRY_RUN=True)
    @patch("knowledge_base.rag.operations.run_index_for_tenant")
    def test_executor_failure_is_controlled(self, mock_index):
        from knowledge_base.rag.indexing import TenantRagIndexingError

        mock_index.side_effect = TenantRagIndexingError("embedding provider unavailable")
        self._login(self.admin)
        self.client.post(
            self._submit_url(),
            {"tenant": self.tenant_a.pk, "operation": "index_embeddings"},
        )
        request_obj = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        execute_operation_request(request_id=request_obj.pk)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, TenantRagOperationRequest.Status.FAILED)
        self.assertTrue(request_obj.error_code)
        self.assertLessEqual(len(request_obj.error_message), 500)

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True, LIVIA_RAG_OPERATIONS_DRY_RUN=True)
    def test_stale_execution_recovery(self):
        from django.utils import timezone
        from datetime import timedelta

        stale = TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="stale-run",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
        )
        recovered = recover_stale_operation_requests(tenant=self.tenant_a)
        stale.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(stale.status, TenantRagOperationRequest.Status.FAILED)
        self.assertEqual(stale.error_code, "stale_execution")

    @override_settings(LIVIA_RAG_OPERATIONS_ENABLED=True, LIVIA_RAG_OPERATIONS_DRY_RUN=True)
    def test_no_chat_side_effects(self):
        self._login(self.admin)
        before = {
            "conversations": Conversation.objects.count(),
            "messages": Message.objects.count(),
            "leads": LeadDraft.objects.count(),
            "handoffs": HandoffRequest.objects.count(),
        }
        self.client.get(self._operations_url())
        self.client.post(self._submit_url(), {"tenant": self.tenant_a.pk, "operation": "inventory"})
        request_obj = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        execute_operation_request(request_id=request_obj.pk)
        after = {
            "conversations": Conversation.objects.count(),
            "messages": Message.objects.count(),
            "leads": LeadDraft.objects.count(),
            "handoffs": HandoffRequest.objects.count(),
        }
        self.assertEqual(before, after)
