from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assistant_core.models import AiUsageEvent
from knowledge_base.models import RagRetrievalEvent, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operational_metrics import (
    build_ai_usage_summary,
    build_retrieval_metrics,
    parse_health_period,
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
)
class KnowledgeBaseHealthPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = get_user_model().objects.create_user(username="health-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="health-viewer", password="pass")
        self.outsider = get_user_model().objects.create_user(username="health-outsider", password="pass")
        self.tenant_a = Tenant.objects.create(name="Health A", slug="health-a")
        self.tenant_b = Tenant.objects.create(name="Health B", slug="health-b")
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
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def _url(self, tenant=None, **params):
        tenant = tenant or self.tenant_a
        query = f"tenant={tenant.pk}"
        for key, value in params.items():
            query += f"&{key}={value}"
        return f"{reverse('operations_portal:knowledge_base_health')}?{query}"

    def test_viewer_can_access_health_page(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central de Saúde RAG e IA")

    def test_outsider_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)

    def test_tenant_isolation_in_retrieval_metrics(self):
        RagRetrievalEvent.objects.create(
            tenant=self.tenant_b,
            status=RagRetrievalEvent.Status.COMPLETED,
            hit=True,
            dry_run=True,
        )
        RagRetrievalEvent.objects.create(
            tenant=self.tenant_a,
            status=RagRetrievalEvent.Status.EMPTY,
            hit=False,
            dry_run=True,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Empty: 1")
        self.assertNotContains(response, "Tenant B")

    def test_empty_state_without_events(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nenhum evento de retrieval no período")

    def test_effective_configuration_visible(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertContains(response, "Configuração efetiva")
        self.assertContains(response, "fake-embed-v1")
        self.assertNotContains(response, "sk-")

    def test_pending_operation_visible(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.PENDING,
            dry_run=True,
            run_id="health-pending",
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertContains(response, "Pending: 1")

    def test_stale_operation_recommendation(self):
        TenantRagOperationRequest.objects.create(
            tenant=self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="health-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertContains(response, "Stale running: 1")
        self.assertContains(response, "lease expirado")

    def test_retrieval_and_ai_metrics(self):
        RagRetrievalEvent.objects.create(
            tenant=self.tenant_a,
            status=RagRetrievalEvent.Status.COMPLETED,
            hit=True,
            dry_run=True,
            duration_ms=120,
        )
        AiUsageEvent.objects.create(
            tenant=self.tenant_a,
            operation=AiUsageEvent.Operation.GROUNDED_SYNTHESIS,
            model="fake-model",
            success=True,
            total_tokens=42,
            latency_ms=300,
        )
        AiUsageEvent.objects.create(
            tenant=self.tenant_a,
            operation=AiUsageEvent.Operation.CHAT_COMPLETION,
            model="fake-model",
            success=False,
            error_type="timeout",
            total_tokens=0,
            latency_ms=1000,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._url(period="24h"))
        self.assertContains(response, "Hits: 1")
        self.assertContains(response, "Tokens: 42")
        self.assertContains(response, "timeout")

    def test_period_filter_30d(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._url(period="30d"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Últimos 30 dias")

    def test_invalid_period_defaults_to_7d(self):
        self.assertEqual(parse_health_period("invalid"), "7d")

    @patch("knowledge_base.rag.operational_metrics.inspect_tenant_embedding_health")
    def test_vector_health_warning(self, mock_health):
        from knowledge_base.rag.embedding_profile import EmbeddingProfile, TenantEmbeddingHealth

        mock_health.return_value = TenantEmbeddingHealth(
            tenant_slug="health-a",
            profile=EmbeddingProfile(
                provider="fake",
                model="fake-embed-v1",
                dimension=8,
                signature="sig",
                batch_size=8,
                schema_vector_dimension=None,
            ),
            total=2,
            compatible=0,
            null_vectors=0,
            wrong_model=0,
            wrong_dimension=0,
            wrong_signature=0,
            inactive=0,
            stale=0,
            reindex_required=2,
            invalid=0,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self._url())
        self.assertContains(response, "REINDEX_REQUIRED")

    def test_retrieval_events_paginated(self):
        for index in range(13):
            RagRetrievalEvent.objects.create(
                tenant=self.tenant_a,
                status=RagRetrievalEvent.Status.COMPLETED,
                hit=True,
                dry_run=True,
            )
        self.client.force_login(self.admin)
        response = self.client.get(self._url(retrieval_page=2))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 de 2")


class OperationalMetricsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Metrics", slug="metrics-tenant")

    def test_retrieval_hit_rate_zero_denominator_safe(self):
        metrics = build_retrieval_metrics(tenant=self.tenant, period="7d")
        self.assertFalse(metrics["has_data"])
        self.assertIsNone(metrics["hit_rate"])

    def test_ai_usage_summary_tenant_scoped(self):
        other = Tenant.objects.create(name="Other", slug="metrics-other")
        AiUsageEvent.objects.create(
            tenant=other,
            operation=AiUsageEvent.Operation.EMBEDDING,
            model="m",
            success=True,
            total_tokens=999,
        )
        summary = build_ai_usage_summary(tenant=self.tenant, period="7d")
        self.assertFalse(summary["has_data"])
        self.assertEqual(summary["total_tokens"], 0)
