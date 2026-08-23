import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditEvent
from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.rollout import (
    ACTION_ROLLOUT_PLANNED,
    ENVIRONMENT_PRODUCTION,
    ENVIRONMENT_STAGING,
    ROLLOUT_STATUS_BLOCKED,
    ROLLOUT_STATUS_READY,
    ROLLOUT_STATUS_VALIDATED,
    TenantRolloutService,
    TenantRolloutSpec,
)

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_AI_ENABLED=False,
    LIVIA_AI_DRY_RUN=True,
    LIVIA_RAG_OPERATIONS_ENABLED=False,
    LIVIA_RAG_INDEXING_ENABLED=False,
    SMART360_LEAD_DISPATCH_ENABLED=False,
    SMART360_LEAD_DISPATCH_DRY_RUN=True,
    LIVIA_WEBHOOKS_ENABLED=False,
    LIVIA_WEBHOOKS_DRY_RUN=True,
)
class TenantRolloutServiceTests(TestCase):
    def setUp(self):
        self.tenant = self._tenant("empresa-x", "https://www.empresa-x.com.br")
        self.other = self._tenant("outra", "https://www.outra.com.br")
        self.service = TenantRolloutService()

    def _tenant(self, slug, origin, *, widget_enabled=True):
        tenant = Tenant.objects.create(name=slug, slug=slug, domain=origin)
        AssistantProfile.objects.create(
            tenant=tenant,
            is_active=True,
            is_widget_enabled=widget_enabled,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511999999999",
        )
        TenantAllowedOrigin.objects.create(tenant=tenant, origin=origin, is_active=True)
        KnowledgeDocument.objects.create(tenant=tenant, title="FAQ", slug="faq", status=KnowledgeDocument.Status.ACTIVE)
        return tenant

    def build(self, **kwargs):
        options = {"tenant": self.tenant, "target_origin": "https://www.empresa-x.com.br", "environment": ENVIRONMENT_STAGING, "dry_run": True}
        options.update(kwargs)
        return self.service.build(TenantRolloutSpec(**options))

    def test_staging_ready_with_warning_allowed_and_install_package_reused(self):
        result = self.build()
        package = TenantInstallPackageService().build_for_tenant(self.tenant)

        self.assertEqual(result.status, ROLLOUT_STATUS_READY)
        self.assertEqual(result.environment, ENVIRONMENT_STAGING)
        self.assertEqual(result.install_plan.snippet, package.snippet)
        self.assertTrue(result.side_effects_safe)
        self.assertIn("staging_warnings_allowed", {check.code for check in result.checks})

    def test_not_ready_tenant_blocks_rollout(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active", "updated_at"])

        result = self.build()

        self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)
        self.assertIn("tenant_active", {check.code for check in result.blocking_checks})

    def test_production_degraded_or_warning_blocks_without_explicit_policy(self):
        LeadDraft.objects.create(
            tenant=self.tenant,
            name="Lead com falha",
            status=LeadDraft.Status.QUALIFIED,
            dispatch_status=LeadDraft.DispatchStatus.FAILED,
        )

        result = self.build(environment=ENVIRONMENT_PRODUCTION)

        self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)
        self.assertIn("operational_ready", {check.code for check in result.blocking_checks})
        self.assertIn("commercial_ready", {check.code for check in result.blocking_checks})

    def test_origin_of_other_tenant_and_root_www_are_explicit(self):
        other_origin = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.outra.com.br"))
        root_origin = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://empresa-x.com.br"))

        self.assertFalse(other_origin.origin_valid)
        self.assertEqual(other_origin.status, ROLLOUT_STATUS_BLOCKED)
        self.assertFalse(root_origin.origin_valid)
        self.assertEqual(root_origin.status, ROLLOUT_STATUS_BLOCKED)

    def test_origin_rejects_path_query_fragment_and_wildcard(self):
        for origin in ["*", "https://www.empresa-x.com.br/path", "https://www.empresa-x.com.br?x=1", "https://www.empresa-x.com.br#frag"]:
            result = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin=origin))
            self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)
            self.assertIn("origin_valid", {check.code for check in result.blocking_checks})

    def test_widget_disabled_blocks_unless_explicitly_allowed(self):
        tenant = self._tenant("widget-off", "https://widget-off.com.br", widget_enabled=False)

        blocked = self.service.build(TenantRolloutSpec(tenant=tenant, target_origin="https://widget-off.com.br"))
        allowed = self.service.build(TenantRolloutSpec(tenant=tenant, target_origin="https://widget-off.com.br", allow_widget_disabled=True))

        self.assertEqual(blocked.status, ROLLOUT_STATUS_BLOCKED)
        self.assertIn("widget_enabled", {check.code for check in blocked.blocking_checks})
        self.assertEqual(allowed.status, ROLLOUT_STATUS_READY, [(check.code, check.status, check.detail, check.blocking) for check in allowed.checks])

    @override_settings(
        LIVIA_ENVIRONMENT="staging",
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_INDEXING_ENABLED=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_EMBEDDING_API_KEY="sk-test-embedding",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_authorized_openai_embedding_real_enabled_allows_staging_rollout(self):
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            source_mode=TenantRagConfiguration.SOURCE_MANUAL,
            sync_enabled=False,
            retrieval_enabled=True,
        )

        result = TenantRolloutService().build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"))

        self.assertTrue(result.side_effects_safe)
        self.assertEqual(result.status, ROLLOUT_STATUS_READY)
        self.assertNotIn("side_effects_safe", {check.code for check in result.blocking_checks})

    @override_settings(
        LIVIA_ENVIRONMENT="staging",
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_INDEXING_ENABLED=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_EMBEDDING_API_KEY="",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_embedding_missing_key_blocks_staging_rollout(self):
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            source_mode=TenantRagConfiguration.SOURCE_MANUAL,
            sync_enabled=False,
            retrieval_enabled=True,
        )

        result = TenantRolloutService().build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"))

        self.assertFalse(result.side_effects_safe)
        self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)
        self.assertIn("side_effects_safe", {check.code for check in result.blocking_checks})

    @override_settings(
        LIVIA_ENVIRONMENT="staging",
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_INDEXING_ENABLED=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_EMBEDDING_API_KEY="sk-test-embedding",
        LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_drive_required_missing_service_account_blocks_staging_rollout(self):
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            source_mode=TenantRagConfiguration.SOURCE_GOOGLE_DRIVE,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )

        result = TenantRolloutService().build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"))

        self.assertFalse(result.side_effects_safe)
        self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)
        self.assertIn("side_effects_safe", {check.code for check in result.blocking_checks})

    @override_settings(
        LIVIA_ENVIRONMENT="production",
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_INDEXING_ENABLED=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_EMBEDDING_API_KEY="sk-test-embedding",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_production_rollout_remains_fail_closed_for_real_embedding(self):
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            source_mode=TenantRagConfiguration.SOURCE_MANUAL,
            sync_enabled=False,
            retrieval_enabled=True,
        )

        result = TenantRolloutService().build(
            TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br", environment=ENVIRONMENT_PRODUCTION)
        )

        self.assertFalse(result.side_effects_safe)
        self.assertEqual(result.status, ROLLOUT_STATUS_BLOCKED)

    @override_settings(
        LIVIA_ENVIRONMENT="staging",
        LIVIA_AI_ENABLED=False,
        LIVIA_AI_DRY_RUN=True,
        LIVIA_RAG_ENABLED=False,
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        LIVIA_WEBHOOKS_ENABLED=False,
        LIVIA_WEBHOOKS_DRY_RUN=True,
    )
    def test_chat_off_smart360_dry_run_webhooks_off_do_not_block_staging(self):
        result = TenantRolloutService().build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"))

        self.assertTrue(result.side_effects_safe)
        self.assertEqual(result.status, ROLLOUT_STATUS_READY)

    def test_commercial_warning_without_handoff_does_not_block_staging(self):
        tenant = self._tenant("sem-handoff", "https://sem-handoff.com.br")
        profile = tenant.assistant_profile
        profile.human_handoff_enabled = False
        profile.handoff_whatsapp_number = ""
        profile.save(update_fields=["human_handoff_enabled", "handoff_whatsapp_number", "updated_at"])

        result = TenantRolloutService().build(TenantRolloutSpec(tenant=tenant, target_origin="https://sem-handoff.com.br"))

        self.assertEqual(result.operational_status, "WARNING")
        self.assertEqual(result.status, ROLLOUT_STATUS_READY)
        self.assertNotIn("commercial_ready", {check.code for check in result.blocking_checks})

    def test_smoke_success_rolls_back_and_blocks_external_calls(self):
        result = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"), run_smoke=True)

        self.assertEqual(result.status, ROLLOUT_STATUS_VALIDATED)
        self.assertTrue(result.smoke_result.ok)
        self.assertTrue(result.smoke_result.rollback_applied)
        self.assertEqual(sum(result.smoke_result.external_calls.values()), 0)

    def test_smoke_failure_is_structured(self):
        with patch("tenants.services.rollout.Client.get", side_effect=RuntimeError("forced smoke failure")):
            result = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"), run_smoke=True)

        self.assertEqual(result.status, "FAILED")
        self.assertFalse(result.smoke_result.ok)
        self.assertTrue(result.smoke_result.errors)

    def test_record_audit_without_secrets(self):
        result = self.service.build(TenantRolloutSpec(tenant=self.tenant, target_origin="https://www.empresa-x.com.br"), record_audit=True)

        self.assertEqual(result.status, ROLLOUT_STATUS_READY)
        event = AuditEvent.objects.get(action=ACTION_ROLLOUT_PLANNED, tenant=self.tenant)
        self.assertEqual(event.metadata["target_origin"], "https://www.empresa-x.com.br")
        self.assertNotIn("token", str(event.metadata).lower())
        self.assertNotIn("secret", str(event.metadata).lower())


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class TenantRolloutCommandAndPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="viewer", password="pass")
        self.tenant = Tenant.objects.create(name="Empresa X", slug="empresa-x", domain="https://www.empresa-x.com.br")
        AssistantProfile.objects.create(
            tenant=self.tenant,
            is_active=True,
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511999999999",
        )
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.empresa-x.com.br", is_active=True)
        KnowledgeDocument.objects.create(tenant=self.tenant, title="FAQ", slug="faq", status=KnowledgeDocument.Status.ACTIVE)
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def test_management_command_text_and_json(self):
        out = StringIO()
        call_command("tenant_rollout", "--tenant", "empresa-x", "--origin", "https://www.empresa-x.com.br", "--environment", "staging", stdout=out)
        self.assertIn("Rollout ............. READY", out.getvalue())
        self.assertIn('data-tenant="empresa-x"', out.getvalue())

        out = StringIO()
        call_command("tenant_rollout", "--tenant", "empresa-x", "--origin", "https://www.empresa-x.com.br", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["tenant"], "empresa-x")
        self.assertEqual(payload["status"], "READY")
        self.assertNotIn("token", out.getvalue().lower())

    def test_management_command_blocked_exits_with_error(self):
        with self.assertRaises(CommandError):
            call_command("tenant_rollout", "--tenant", "empresa-x", "--origin", "https://evil.example")

    def test_portal_detail_shows_rollout_and_admin_can_plan(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rollout")
        self.assertContains(response, "Smoke checklist")

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {
                "tenant": self.tenant.pk,
                "action": "plan_rollout",
                "rollout_origin": "https://www.empresa-x.com.br",
                "rollout_environment": "staging",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_ROLLOUT_PLANNED, tenant=self.tenant, actor=self.user).exists())

    def test_viewer_sees_rollout_but_cannot_plan(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rollout")
        self.assertNotContains(response, "Planejar</button>")

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {
                "tenant": self.tenant.pk,
                "action": "plan_rollout",
                "rollout_origin": "https://www.empresa-x.com.br",
                "rollout_environment": "staging",
            },
        )
        self.assertEqual(response.status_code, 403)
