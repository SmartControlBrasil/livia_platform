from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from audit.models import ACTION_TENANT_RAG_OPERATION_DUPLICATE, ACTION_TENANT_RAG_OPERATION_STALE_RECOVERED, AuditEvent
from knowledge_base.models import TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operations import (
    RagOperationsError,
    create_operation_request,
    execute_operation_request,
    process_pending_operation_requests,
    recover_stale_operation_requests,
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

    @override_settings(
        LIVIA_RAG_OPERATIONS_DRY_RUN=False,
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
        LIVIA_ENVIRONMENT="production",
        LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="/tmp/fake-livia-google-sa.json",
        LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED=False,
    )
    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_real_inventory_operation_policy_block_never_calls_drive(self, mock_drive):
        request_obj = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            requested_by=self.user,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertFalse(finished.dry_run)
        self.assertEqual(finished.status, TenantRagOperationRequest.Status.FAILED)
        self.assertEqual(finished.error_code, "drive_sync_real_not_enabled")
        mock_drive.assert_not_called()

    @override_settings(
        LIVIA_RAG_OPERATIONS_DRY_RUN=False,
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
        LIVIA_ENVIRONMENT="production",
        LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="/tmp/fake-livia-google-sa.json",
        LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED=False,
    )
    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_real_sync_export_operation_policy_block_never_calls_drive(self, mock_drive):
        request_obj = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.SYNC_EXPORT,
            requested_by=self.user,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertEqual(finished.status, TenantRagOperationRequest.Status.FAILED)
        self.assertEqual(finished.error_code, "drive_sync_real_not_enabled")
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

    def test_active_constraint_blocks_second_operation(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.PENDING,
            dry_run=True,
            run_id="active-run",
        )
        with self.assertRaises(RagOperationsError) as ctx:
            create_operation_request(
                tenant=self.tenant,
                operation=TenantRagOperationRequest.Operation.SYNC_EXPORT,
                requested_by=self.user,
            )
        self.assertEqual(ctx.exception.code, "duplicate_operation")
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action=ACTION_TENANT_RAG_OPERATION_DUPLICATE,
            ).exists()
        )

    def test_different_tenants_can_have_active_operations(self):
        other = Tenant.objects.create(name="Other", slug="tenant-ops-other")
        TenantRagConfiguration.objects.create(
            tenant=other,
            approved_folder_id="folder-other",
            sync_enabled=True,
        )
        create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            requested_by=self.user,
        )
        other_request = create_operation_request(
            tenant=other,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            requested_by=self.user,
        )
        self.assertEqual(other_request.status, TenantRagOperationRequest.Status.PENDING)

    def test_terminal_state_allows_new_request(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.SUCCEEDED,
            dry_run=True,
            run_id="done-run-2",
        )
        new_request = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            requested_by=self.user,
        )
        self.assertEqual(new_request.status, TenantRagOperationRequest.Status.PENDING)

    def test_stale_recovery_is_transactionally_idempotent(self):
        from datetime import timedelta

        from django.utils import timezone

        stale = TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="stale-run-service",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=1),
            attempt_count=1,
        )
        first = recover_stale_operation_requests(tenant=self.tenant)
        second = recover_stale_operation_requests(tenant=self.tenant)
        stale.refresh_from_db()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(stale.status, TenantRagOperationRequest.Status.FAILED)
        self.assertEqual(stale.error_code, "stale_execution")
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=self.tenant,
                action=ACTION_TENANT_RAG_OPERATION_STALE_RECOVERED,
            ).exists()
        )

    @override_settings(LIVIA_RAG_OPERATIONS_MAX_ATTEMPTS=1)
    def test_max_attempts_blocks_reprocessing(self):
        request_obj = TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.PENDING,
            dry_run=True,
            run_id="max-attempt-run",
            attempt_count=1,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertEqual(finished.status, TenantRagOperationRequest.Status.FAILED)
        self.assertEqual(finished.error_code, "max_attempts_exceeded")

    @patch("knowledge_base.rag.operations.build_google_drive_readonly_service")
    def test_full_reindex_dry_run_renews_heartbeat(self, mock_drive):
        request_obj = create_operation_request(
            tenant=self.tenant,
            operation=TenantRagOperationRequest.Operation.FULL_REINDEX,
            requested_by=self.user,
        )
        finished = execute_operation_request(request_id=request_obj.pk)
        self.assertIn(
            finished.status,
            {
                TenantRagOperationRequest.Status.SUCCEEDED,
                TenantRagOperationRequest.Status.PARTIAL,
            },
        )
        self.assertIsNotNone(finished.last_heartbeat_at)
        mock_drive.assert_not_called()
