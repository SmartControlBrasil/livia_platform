from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import ACTION_OPERATIONAL_MONITORING_COMPLETED, ACTION_OPERATIONAL_MONITORING_SKIPPED, AuditEvent
from knowledge_base.models import (
    OperationalMonitoringBatchRun,
    TenantOperationalAlert,
    TenantOperationalMonitoringRun,
    TenantRagConfiguration,
    TenantRagOperationRequest,
)
from knowledge_base.rag.operational_monitoring import (
    monitoring_gate_status,
    process_operational_monitoring,
    prune_operational_monitoring_runs,
    recover_stale_monitoring_batches,
)
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
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_OPERATIONAL_MONITORING_ENABLED=False,
    LIVIA_OPERATIONAL_MONITORING_DRY_RUN=True,
)
class OperationalMonitoringServiceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="mon-admin", password="pass")
        self.tenant_a = Tenant.objects.create(name="Mon A", slug="mon-a")
        self.tenant_b = Tenant.objects.create(name="Mon B", slug="mon-b")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            operational_monitoring_enabled=True,
            retrieval_enabled=True,
        )
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_b,
            approved_folder_id="folder-b",
            operational_monitoring_enabled=True,
            retrieval_enabled=True,
        )

    def test_gate_disabled_skips_automatic_batch(self):
        result = process_operational_monitoring(all_eligible=True, trigger="scheduler")
        self.assertEqual(result.status, OperationalMonitoringBatchRun.Status.SKIPPED)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_MONITORING_SKIPPED).exists())

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True, LIVIA_OPERATIONAL_MONITORING_DRY_RUN=True)
    def test_dry_run_does_not_persist_alerts(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="mon-stale",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        result = process_operational_monitoring(
            tenant_slug="mon-a",
            trigger="cli",
            dry_run=True,
        )
        self.assertEqual(result.status, OperationalMonitoringBatchRun.Status.SUCCEEDED)
        self.assertEqual(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).count(), 0)

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True, LIVIA_OPERATIONAL_MONITORING_DRY_RUN=False)
    def test_real_run_syncs_alerts(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="mon-stale-real",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        result = process_operational_monitoring(
            tenant_slug="mon-a",
            trigger="cli",
            dry_run=False,
        )
        self.assertEqual(result.status, OperationalMonitoringBatchRun.Status.SUCCEEDED)
        self.assertTrue(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).exists())
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_MONITORING_COMPLETED).exists())

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True)
    def test_tenant_not_enabled_is_skipped_for_automatic(self):
        TenantRagConfiguration.objects.update(operational_monitoring_enabled=False)
        result = process_operational_monitoring(all_eligible=True, trigger="scheduler")
        self.assertEqual(result.status, OperationalMonitoringBatchRun.Status.SKIPPED)

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True, LIVIA_OPERATIONAL_MONITORING_DRY_RUN=False)
    def test_partial_failure_isolates_tenants(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="mon-a-stale",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )

        from knowledge_base.rag.operational_alert_sync import SyncOperationalAlertsResult

        def fail_tenant_b(*args, **kwargs):
            if kwargs.get("tenant") and kwargs["tenant"].slug == "mon-b":
                raise RuntimeError("forced diagnostic failure")
            return SyncOperationalAlertsResult(
                tenant_slug=kwargs["tenant"].slug,
                created=1,
                updated=0,
                reopened=0,
                auto_resolved=0,
                active=1,
                dry_run=False,
            )

        with patch("knowledge_base.rag.operational_monitoring.sync_operational_alerts", side_effect=fail_tenant_b):
            result = process_operational_monitoring(all_eligible=True, trigger="scheduler", dry_run=False)

        self.assertEqual(result.status, OperationalMonitoringBatchRun.Status.PARTIAL)
        self.assertEqual(result.tenants_processed, 1)
        self.assertEqual(result.tenants_failed, 1)
        self.assertFalse(
            TenantOperationalMonitoringRun.objects.filter(
                tenant=self.tenant_b,
                status=TenantOperationalMonitoringRun.Status.SUCCEEDED,
            ).exists()
        )

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True)
    def test_stale_batch_recovery(self):
        batch = OperationalMonitoringBatchRun.objects.create(
            trigger="scheduler",
            status=OperationalMonitoringBatchRun.Status.RUNNING,
            dry_run=True,
            period="7d",
            started_at=timezone.now() - timedelta(hours=1),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
        )
        recovered = recover_stale_monitoring_batches()
        batch.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(batch.status, OperationalMonitoringBatchRun.Status.FAILED)

    @override_settings(LIVIA_OPERATIONAL_MONITORING_RETENTION_DAYS=30)
    def test_prune_old_runs(self):
        old = OperationalMonitoringBatchRun.objects.create(
            trigger="cli",
            status=OperationalMonitoringBatchRun.Status.SUCCEEDED,
            dry_run=True,
            period="7d",
            started_at=timezone.now() - timedelta(days=120),
            finished_at=timezone.now() - timedelta(days=120),
        )
        TenantOperationalMonitoringRun.objects.create(
            batch=old,
            tenant=self.tenant_a,
            status=TenantOperationalMonitoringRun.Status.SUCCEEDED,
            started_at=old.started_at,
            finished_at=old.finished_at,
        )
        result = prune_operational_monitoring_runs(days=30)
        self.assertGreaterEqual(result["batches_deleted"], 1)

    @override_settings(LIVIA_OPERATIONAL_MONITORING_ENABLED=True, LIVIA_OPERATIONAL_MONITORING_DRY_RUN=False)
    def test_cli_tenant_slug_only(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="cli-stale",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        call_command("process_operational_monitoring", tenant="mon-a", no_dry_run=True)
        self.assertTrue(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).exists())
        self.assertFalse(TenantOperationalAlert.objects.filter(tenant=self.tenant_b).exists())


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_OPERATIONAL_MONITORING_ENABLED=False,
)
class OperationalMonitoringPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="mon-portal-admin", password="pass")
        self.tenant = Tenant.objects.create(name="Mon Portal", slug="mon-portal")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder",
            retrieval_enabled=True,
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)

    def test_health_shows_monitoring_section_empty(self):
        self.client.force_login(self.admin)
        url = f"{reverse('operations_portal:knowledge_base_health')}?tenant={self.tenant.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoramento automático")
        self.assertContains(response, "Desabilitado")

    def test_portal_sync_creates_monitoring_run(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="portal-stale",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("operations_portal:knowledge_base_health_sync"),
            {"tenant": self.tenant.pk, "period": "7d"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantOperationalMonitoringRun.objects.filter(tenant=self.tenant).exists())
        self.assertTrue(TenantOperationalAlert.objects.filter(tenant=self.tenant).exists())

    def test_monitoring_gate_status_defaults(self):
        gate = monitoring_gate_status()
        self.assertFalse(gate.enabled)
        self.assertTrue(gate.dry_run)
