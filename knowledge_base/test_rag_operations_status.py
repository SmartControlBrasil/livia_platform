from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from knowledge_base.models import TenantRagConfiguration, TenantRagOperationRequest
from tenants.models import Tenant

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
)
class RagOperationsStatusCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Status Tenant", slug="status-tenant")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-status",
            sync_enabled=True,
        )

    def test_status_command_is_read_only(self):
        before = TenantRagOperationRequest.objects.count()
        out = StringIO()
        call_command("tenant_rag_operations_status", tenant=self.tenant.slug, stdout=out)
        self.assertEqual(TenantRagOperationRequest.objects.count(), before)
        self.assertIn("pending=", out.getvalue())

    def test_readiness_command_reports_simulation_ready(self):
        out = StringIO()
        call_command("tenant_rag_operations_readiness", tenant=self.tenant.slug, stdout=out)
        self.assertIn("simulation_ready", out.getvalue())

    def test_readiness_json_output(self):
        out = StringIO()
        call_command(
            "tenant_rag_operations_readiness",
            tenant=self.tenant.slug,
            json=True,
            stdout=out,
        )
        self.assertIn('"simulation_ready"', out.getvalue())
