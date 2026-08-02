from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assistant_core.models import AiUsageEvent
from audit.models import (
    ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED,
    ACTION_OPERATIONAL_ALERT_CREATED,
    ACTION_OPERATIONAL_ALERT_RESOLVED,
    ACTION_OPERATIONAL_ALERT_SYNC,
    AuditEvent,
)
from knowledge_base.models import RagRetrievalEvent, TenantOperationalAlert, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operational_alert_sync import (
    OperationalAlertError,
    acknowledge_operational_alert,
    resolve_operational_alert,
    sync_operational_alerts,
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
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_DIMENSION=8,
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
    LIVIA_RAG_ALERT_RETRIEVAL_MIN_EXECUTED=5,
    LIVIA_RAG_ALERT_RETRIEVAL_EMPTY_RATE=0.8,
    LIVIA_RAG_ALERT_AI_FAILURE_MIN=3,
)
class OperationalAlertSyncTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="alert-admin", password="pass")
        self.tenant_a = Tenant.objects.create(name="Alert A", slug="alert-a")
        self.tenant_b = Tenant.objects.create(name="Alert B", slug="alert-b")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_b,
            approved_folder_id="folder-b",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)

    def _create_stale_operation(self, tenant):
        return TenantRagOperationRequest.objects.create(
            tenant=tenant,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="alert-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def test_critical_stale_operation_creates_alert(self):
        self._create_stale_operation(self.tenant_a)
        result = sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        self.assertEqual(result.created, 1)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        self.assertEqual(alert.severity, TenantOperationalAlert.Severity.CRITICAL)
        self.assertEqual(alert.status, TenantOperationalAlert.Status.OPEN)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_CREATED).exists())

    def test_deduplication_updates_existing_alert(self):
        operation = self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        self.assertEqual(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).count(), 1)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        self.assertEqual(alert.occurrence_count, 2)
        self.assertEqual(alert.source_reference, str(operation.pk))

    def test_auto_resolution_when_condition_removed(self):
        operation = self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.lease_expires_at = timezone.now() + timedelta(hours=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        self.assertEqual(alert.status, TenantOperationalAlert.Status.RESOLVED)
        self.assertEqual(alert.resolution_source, TenantOperationalAlert.ResolutionSource.AUTO)

    def test_reopen_after_auto_resolution(self):
        operation = self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        self.assertEqual(alert.status, TenantOperationalAlert.Status.OPEN)
        self.assertGreaterEqual(alert.occurrence_count, 2)

    def test_acknowledge_and_resolve_manual(self):
        self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.status, TenantOperationalAlert.Status.ACKNOWLEDGED)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED).exists())
        resolve_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            resolution_note="Worker reiniciado e fila normalizada.",
        )
        alert.refresh_from_db()
        self.assertEqual(alert.status, TenantOperationalAlert.Status.RESOLVED)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_RESOLVED).exists())

    def test_invalid_acknowledge_transition_blocked(self):
        self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        with self.assertRaises(OperationalAlertError):
            acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)

    def test_tenant_isolation_on_sync(self):
        self._create_stale_operation(self.tenant_b)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        self.assertEqual(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).count(), 0)
        self.assertEqual(TenantOperationalAlert.objects.filter(tenant=self.tenant_b).count(), 0)

    def test_retrieval_threshold_avoids_small_sample_alert(self):
        for _ in range(3):
            RagRetrievalEvent.objects.create(
                tenant=self.tenant_a,
                status=RagRetrievalEvent.Status.EMPTY,
                hit=False,
                dry_run=True,
            )
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        self.assertFalse(
            TenantOperationalAlert.objects.filter(
                tenant=self.tenant_a,
                rule_id="retrieval_empty_elevated",
            ).exists()
        )

    def test_openai_failures_requires_minimum(self):
        for _ in range(2):
            AiUsageEvent.objects.create(
                tenant=self.tenant_a,
                operation=AiUsageEvent.Operation.CHAT_COMPLETION,
                model="fake",
                success=False,
                error_type="timeout",
            )
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        self.assertFalse(
            TenantOperationalAlert.objects.filter(tenant=self.tenant_a, rule_id="openai_failures").exists()
        )

    def test_concurrent_sync_does_not_duplicate(self):
        self._create_stale_operation(self.tenant_a)

        def _run_sync():
            sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)

        if connection.vendor == "postgresql":
            self.skipTest("Concorrência validada em OperationalPostgresConcurrencyTests.")
        _run_sync()
        _run_sync()
        self.assertEqual(
            TenantOperationalAlert.objects.filter(
                tenant=self.tenant_a,
                rule_id="rag_operation_stale",
            ).count(),
            1,
        )

    def test_cli_uses_same_service(self):
        from io import StringIO

        from django.core.management import call_command

        self._create_stale_operation(self.tenant_a)
        out = StringIO()
        call_command("sync_operational_alerts", tenant="alert-a", stdout=out)
        self.assertIn("created=1", out.getvalue())
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_SYNC).exists())


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
)
class OperationalAlertPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="alert-portal-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="alert-portal-viewer", password="pass")
        self.outsider = get_user_model().objects.create_user(username="alert-portal-outsider", password="pass")
        self.tenant_a = Tenant.objects.create(name="Portal Alert A", slug="portal-alert-a")
        self.tenant_b = Tenant.objects.create(name="Portal Alert B", slug="portal-alert-b")
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def _alerts_url(self, **params):
        query = f"tenant={self.tenant_a.pk}"
        for key, value in params.items():
            query += f"&{key}={value}"
        return f"{reverse('operations_portal:knowledge_base_alerts')}?{query}"

    def test_viewer_can_list_alerts_empty_state(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self._alerts_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nenhum alerta operacional aberto")
        self.assertContains(response, "Execute a atualização de diagnóstico")

    def test_outsider_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._alerts_url())
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_sync(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("operations_portal:knowledge_base_health_sync"),
            {"tenant": self.tenant_a.pk, "period": "7d"},
        )
        self.assertEqual(response.status_code, 403)

    def test_operator_sync_via_portal(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
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
            {"tenant": self.tenant_a.pk, "period": "7d"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantOperationalAlert.objects.filter(tenant=self.tenant_a).exists())

    def test_tenant_isolation_in_list(self):
        TenantOperationalAlert.objects.create(
            tenant=self.tenant_b,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.CRITICAL,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_operation_stale",
            fingerprint="rag_operation_stale:999",
            title="Stale B",
            summary="Tenant B only",
            detected_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._alerts_url())
        self.assertNotContains(response, "Stale B")

    def test_filters_and_pagination(self):
        now = timezone.now()
        for index in range(13):
            TenantOperationalAlert.objects.create(
                tenant=self.tenant_a,
                category=TenantOperationalAlert.Category.RAG_OPERATIONS,
                severity=TenantOperationalAlert.Severity.WARNING,
                status=TenantOperationalAlert.Status.OPEN,
                rule_id="rag_operation_failed",
                fingerprint=f"rag_operation_failed:{index}",
                title=f"Failed {index}",
                summary="Falha",
                detected_at=now,
                last_seen_at=now,
            )
        self.client.force_login(self.admin)
        response = self.client.get(self._alerts_url(severity="warning", page=2))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 de 2")

    @patch("knowledge_base.rag.operational_metrics.inspect_tenant_embedding_health")
    def test_metadata_does_not_expose_secrets(self, mock_health):
        from knowledge_base.rag.embedding_profile import EmbeddingProfile, TenantEmbeddingHealth

        mock_health.return_value = TenantEmbeddingHealth(
            tenant_slug="portal-alert-a",
            profile=EmbeddingProfile(
                provider="fake",
                model="fake-embed-v1",
                dimension=8,
                signature="sig",
                batch_size=8,
                schema_vector_dimension=None,
            ),
            total=1,
            compatible=0,
            null_vectors=0,
            wrong_model=0,
            wrong_dimension=0,
            wrong_signature=0,
            inactive=0,
            stale=0,
            reindex_required=1,
            invalid=0,
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("operations_portal:knowledge_base_health_sync"),
            {"tenant": self.tenant_a.pk, "period": "7d"},
        )
        alert = TenantOperationalAlert.objects.filter(tenant=self.tenant_a).first()
        self.assertIsNotNone(alert)
        metadata_blob = str(alert.metadata)
        self.assertNotIn("sk-", metadata_blob)
