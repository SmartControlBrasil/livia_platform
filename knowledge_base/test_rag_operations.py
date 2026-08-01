from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from knowledge_base.models import TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operations import (
    RagOperationsError,
    create_operation_request,
    execute_operation_request,
    process_pending_operation_requests,
)
from tenants.models import Tenant

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
)
class RagOperationsServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ops-user", password="pass")
        self.tenant = Tenant.objects.create(name="Tenant Ops", slug="tenant-ops-service")
        self.configuration = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-ops",
            sync_enabled=True,
        )

    def test_create_request_when_sync_disabled_for_sync_operations(self):
        self.configuration.sync_enabled = False
        self.configuration.save(update_fields=["sync_enabled"])
        with self.assertRaises(RagOperationsError) as ctx:
            create_operation_request(
                tenant=self.tenant,
                operation=TenantRagOperationRequest.Operation.SYNC_EXPORT,
                requested_by=self.user,
            )
        self.assertEqual(ctx.exception.code, "sync_disabled")

    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_dry_run_inventory_never_calls_drive(self, mock_drive):
        request_obj = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            requested_by=self.user,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertEqual(finished.status, TenantRagOperationRequest.Status.SUCCEEDED)
        self.assertTrue(finished.dry_run)
        mock_drive.assert_not_called()

    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_worker_processes_pending_queue(self, mock_drive):
        create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.BUILD_CHUNKS,
            requested_by=self.user,
        )
        processed = process_pending_operation_requests(tenant=self.tenant, limit=1)
        self.assertEqual(len(processed), 1)
        request_obj = TenantRagOperationRequest.objects.get(pk=processed[0])
        self.assertIn(
            request_obj.status,
            {
                TenantRagOperationRequest.Status.SUCCEEDED,
                TenantRagOperationRequest.Status.PARTIAL,
                TenantRagOperationRequest.Status.FAILED,
            },
        )
        mock_drive.assert_not_called()

    def test_execute_is_idempotent_for_terminal_states(self):
        request_obj = TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.SUCCEEDED,
            dry_run=True,
            run_id="done-run",
            counters={"discovered": 1},
        )
        again = execute_operation_request(request_id=request_obj.pk)
        self.assertEqual(again.pk, request_obj.pk)
        self.assertEqual(again.status, TenantRagOperationRequest.Status.SUCCEEDED)
