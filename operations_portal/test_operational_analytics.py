from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from knowledge_base.models import TenantOperationalAlert, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operational_alert_sync import acknowledge_operational_alert, resolve_operational_alert, sync_operational_alerts
from knowledge_base.rag.operational_analytics import (
    AnalyticsFilters,
    build_operational_analytics,
    compute_percentiles,
    parse_analytics_period,
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
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
    LIVIA_OPERATIONAL_ANALYTICS_MIN_SAMPLE=1,
)
class OperationalAnalyticsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="analytics-admin", password="pass")
        self.operator = get_user_model().objects.create_user(username="analytics-operator", password="pass")
        self.foreign = get_user_model().objects.create_user(username="analytics-foreign", password="pass")
        self.tenant_a = Tenant.objects.create(name="Analytics A", slug="analytics-a")
        self.tenant_b = Tenant.objects.create(name="Analytics B", slug="analytics-b")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.admin_membership = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.operator_membership = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.operator,
            role=TenantMembership.Role.OPERATOR,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.foreign,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.client = Client()

    def _create_stale_operation(self, tenant=None):
        return TenantRagOperationRequest.objects.create(
            tenant=tenant or self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="analytics-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def _sync_alert(self, tenant=None):
        self._create_stale_operation(tenant)
        sync_operational_alerts(tenant=tenant or self.tenant_a, actor=self.admin)
        return TenantOperationalAlert.objects.get(tenant=tenant or self.tenant_a)

    def test_volume_counts(self):
        alert = self._sync_alert()
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertGreaterEqual(payload["volume"]["created"], 1)
        self.assertEqual(payload["backlog"]["total_open"], 1)

    def test_backlog_only_open_alerts(self):
        alert = self._sync_alert()
        resolve_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            resolution_note="Resolvido para teste de analytics de backlog aberto.",
        )
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertEqual(payload["backlog"]["total_open"], 0)
        self.assertGreaterEqual(payload["volume"]["resolved"], 1)

    def test_ack_median(self):
        alert = self._sync_alert()
        acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertTrue(payload["ack_times"]["has_data"])
        self.assertGreaterEqual(payload["ack_times"]["median_minutes"], 0)

    def test_resolution_median(self):
        alert = self._sync_alert()
        acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        resolve_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            resolution_note="Resolvido para teste de mediana de resolução operacional.",
        )
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertTrue(payload["resolution_times"]["has_data"])

    def test_percentiles(self):
        result = compute_percentiles([10, 20, 30, 40, 50])
        self.assertEqual(result["median_minutes"], 30.0)
        self.assertEqual(result["p75_minutes"], 40.0)

    @override_settings(LIVIA_OPERATIONAL_ANALYTICS_MIN_SAMPLE=3)
    def test_insufficient_sample(self):
        result = compute_percentiles([10, 20])
        self.assertFalse(result["has_data"])

    def test_reopen_count_recurrence(self):
        alert = self._sync_alert()
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertGreaterEqual(payload["recurrence"]["alerts_with_reopen"], 1)

    def test_occurrence_vs_reopen_note(self):
        alert = self._sync_alert()
        alert.occurrence_count = 5
        alert.reopen_count = 0
        alert.save(update_fields=["occurrence_count", "reopen_count", "updated_at"])
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertIn("occurrence_count", payload["occurrences"]["note"])

    def test_workload_score(self):
        alert = self._sync_alert()
        alert.assigned_to = self.admin_membership
        alert.save(update_fields=["assigned_to", "updated_at"])
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        assignee = next(row for row in payload["capacity"]["assignees"] if row["membership_id"] == self.admin_membership.pk)
        self.assertGreater(assignee["workload_score"], 0)

    def test_unassigned_ranking(self):
        self._sync_alert()
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertEqual(payload["unassigned"]["count"], 1)
        self.assertEqual(len(payload["unassigned"]["top_urgent"]), 1)

    def test_period_parsing(self):
        self.assertEqual(parse_analytics_period("90d"), "90d")
        self.assertEqual(parse_analytics_period("invalid"), "7d")

    def test_tenant_isolation(self):
        self._sync_alert()
        self._sync_alert(tenant=self.tenant_b)
        payload_a = build_operational_analytics(tenant=self.tenant_a, period="7d")
        payload_b = build_operational_analytics(tenant=self.tenant_b, period="7d")
        self.assertEqual(payload_a["backlog"]["total_open"], 1)
        self.assertEqual(payload_b["backlog"]["total_open"], 1)

    def test_cli_matches_service(self):
        self._sync_alert()
        service_payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        out = StringIO()
        call_command("operational_analytics_report", tenant_slug="analytics-a", period="7d", stdout=out)
        self.assertIn("backlog=1", out.getvalue())
        self.assertEqual(service_payload["backlog"]["total_open"], 1)

    def test_portal_page_renders(self):
        self._sync_alert()
        self.client.force_login(self.admin)
        session = self.client.session
        session["operations_portal_active_tenant_id"] = str(self.tenant_a.pk)
        session.save()
        response = self.client.get(reverse("operations_portal:operational_analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics operacional")

    def test_operator_cannot_export(self):
        self._sync_alert()
        self.client.force_login(self.operator)
        session = self.client.session
        session["operations_portal_active_tenant_id"] = str(self.tenant_a.pk)
        session.save()
        response = self.client.get(reverse("operations_portal:operational_analytics_export"))
        self.assertEqual(response.status_code, 403)

    def test_age_buckets(self):
        alert = self._sync_alert()
        alert.detected_at = timezone.now() - timedelta(hours=5)
        alert.save(update_fields=["detected_at", "updated_at"])
        payload = build_operational_analytics(tenant=self.tenant_a, period="7d")
        bucket_map = {row["key"]: row["count"] for row in payload["age_buckets"]["buckets"]}
        self.assertGreater(bucket_map.get("4_24h", 0), 0)

    def test_priority_filter(self):
        self._sync_alert()
        payload = build_operational_analytics(
            tenant=self.tenant_a,
            period="7d",
            filters=AnalyticsFilters(priority="P1"),
        )
        self.assertGreaterEqual(payload["backlog"]["total_open"], 1)

    def test_query_count_reasonable(self):
        self._sync_alert()
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            build_operational_analytics(tenant=self.tenant_a, period="7d")
        self.assertLessEqual(len(ctx.captured_queries), 45)
        self.assertGreater(len(ctx.captured_queries), 10)
