import json
import uuid

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from io import StringIO

from conversations.models import ChatRequest, Conversation, HandoffRequest
from integrations.side_effect_policy import SideEffectStatus, SideEffectType, evaluate_side_effect_policy
from integrations.models import TenantWebhookConfig
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.origins import is_origin_allowed, normalize_origin
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.onboarding import build_widget_snippet
from tenants.services.site_readiness import (
    SITE_READINESS_NOT_READY,
    SITE_READINESS_READY,
    SITE_READINESS_WARNING,
    inspect_tenant_site_readiness,
)


class SiteReadinessContractTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Cliente Pronto",
            slug="cliente-pronto",
            domain="https://www.cliente-pronto.example",
            is_active=True,
        )
        AssistantProfile.objects.create(tenant=self.tenant, name="Lívia Cliente")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.cliente-pronto.example")

    def test_ready_tenant_returns_ready_status(self):
        package = TenantInstallPackageService().build_for_tenant(self.tenant)

        self.assertEqual(package.readiness_status, SITE_READINESS_READY)
        self.assertTrue(package.is_ready_for_install)
        self.assertEqual(package.assistant_name, "Lívia Cliente")
        self.assertIn("readiness", package.to_dict())
        self.assertEqual(package.to_dict()["readiness"], SITE_READINESS_READY)

    def test_missing_tenant_report_is_not_ready(self):
        report = inspect_tenant_site_readiness(None, tenant_slug="inexistente")

        self.assertEqual(report.overall_status, SITE_READINESS_NOT_READY)
        self.assertTrue(report.blocking)

    def test_inactive_tenant_is_not_ready(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        report = inspect_tenant_site_readiness(self.tenant)

        self.assertEqual(report.overall_status, SITE_READINESS_NOT_READY)
        codes = {check.code for check in report.checks if check.status == "FAIL"}
        self.assertIn("tenant_active", codes)

    def test_missing_profile_is_not_ready(self):
        AssistantProfile.objects.filter(tenant=self.tenant).delete()

        report = inspect_tenant_site_readiness(self.tenant)

        self.assertEqual(report.overall_status, SITE_READINESS_NOT_READY)
        self.assertIn("assistant_profile_exists", [check.code for check in report.checks])

    def test_missing_origin_is_not_ready(self):
        TenantAllowedOrigin.objects.filter(tenant=self.tenant).delete()

        report = inspect_tenant_site_readiness(self.tenant)

        self.assertEqual(report.overall_status, SITE_READINESS_NOT_READY)
        self.assertIn("active_origin_present", [check.code for check in report.checks])

    def test_multiple_origins_are_supported(self):
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://app.cliente-pronto.example")

        package = TenantInstallPackageService().build_for_tenant(self.tenant)

        self.assertEqual(len(package.allowed_origins), 2)
        self.assertEqual(package.readiness_status, SITE_READINESS_READY)

    def test_contract_escapes_and_hides_secrets(self):
        self.tenant.name = '<img src=x onerror=alert(1)>'
        self.tenant.save(update_fields=["name"])
        AssistantProfile.objects.filter(tenant=self.tenant).update(name='"><script>alert(1)</script>')
        TenantWebhookConfig.objects.create(
            tenant=self.tenant,
            name="N8N",
            target_url="https://n8n.example/webhook",
            secret_token="super-secret-token",
        )

        package = TenantInstallPackageService().build_for_tenant(self.tenant)
        payload_text = json.dumps(package.to_dict())

        self.assertNotIn("super-secret-token", payload_text)
        self.assertNotIn("secret_token", payload_text)
        self.assertNotIn("<script>alert(1)</script>", payload_text)

        response = self.client.get("/install/cliente-pronto/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)


class OriginValidationExtendedTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.example.com")

    def test_https_origin_is_valid(self):
        self.assertEqual(normalize_origin("https://www.example.com"), "https://www.example.com")
        self.assertTrue(is_origin_allowed(self.tenant, "https://www.example.com"))

    @override_settings(DEBUG=True, LIVIA_DEV_ALLOWED_WIDGET_ORIGINS=["http://localhost:8000"])
    def test_http_origin_allowed_in_development_policy(self):
        self.assertEqual(normalize_origin("http://localhost:8000"), "http://localhost:8000")
        self.assertTrue(is_origin_allowed(self.tenant, "http://localhost:8000"))

    def test_path_in_origin_is_rejected(self):
        with self.assertRaises(Exception):
            normalize_origin("https://example.com/path")

    def test_wildcard_is_rejected(self):
        with self.assertRaises(Exception):
            normalize_origin("*")

    def test_similar_domain_is_not_authorized(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://evil-example.com"))

    def test_different_port_is_not_authorized(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://www.example.com:8443"))
        self.assertFalse(is_origin_allowed(self.tenant, "https://example.com:443"))

    def test_unauthorized_subdomain_is_rejected(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://app.example.com"))

    def test_trailing_slash_is_normalized(self):
        self.assertEqual(normalize_origin("https://WWW.Example.COM/"), "https://www.example.com")
        self.assertTrue(is_origin_allowed(self.tenant, "https://www.example.com/"))

    def test_case_insensitive_host_matching(self):
        self.assertTrue(is_origin_allowed(self.tenant, "HTTPS://WWW.EXAMPLE.COM"))


class GranimarmoresOfficialOriginsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Granimármores Pitondo",
            slug="granimarmores-pitondo",
            domain="https://www.granimarmorespitondo.com.br",
            is_active=True,
        )
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.granimarmorespitondo.com.br")

    def test_official_domain_is_authorized(self):
        self.assertTrue(is_origin_allowed(self.tenant, "https://www.granimarmorespitondo.com.br"))

    def test_alternate_domain_requires_explicit_registration(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://granimarmorespitondo.com.br"))
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://granimarmorespitondo.com.br")
        self.assertTrue(is_origin_allowed(self.tenant, "https://granimarmorespitondo.com.br"))

    def test_random_subdomain_is_rejected(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://app.granimarmorespitondo.com.br"))

    def test_different_port_is_rejected(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://www.granimarmorespitondo.com.br:8443"))

    def test_lookalike_domain_is_rejected(self):
        self.assertFalse(is_origin_allowed(self.tenant, "https://www.granimarmorepitondo.com.br"))


class InstallPageExtendedTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Granimármores Pitondo",
            slug="granimarmores-pitondo",
            domain="www.granimarmorespitondo.com.br/",
            is_active=True,
        )
        AssistantProfile.objects.create(tenant=self.tenant, name="Lívia")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.granimarmorespitondo.com.br")

    def test_install_page_is_public_and_returns_200(self):
        response = self.client.get("/install/granimarmores-pitondo/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checklist de prontidão")
        self.assertContains(response, "Copiar snippet")

    def test_missing_tenant_returns_404(self):
        response = self.client.get("/install/tenant-inexistente/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Tenant não encontrado", status_code=404)

    def test_not_ready_tenant_shows_blocking_status(self):
        TenantAllowedOrigin.objects.filter(tenant=self.tenant).update(is_active=False)

        response = self.client.get("/install/granimarmores-pitondo/")

        self.assertContains(response, "NOT_READY")
        self.assertContains(response, "Nenhuma origin ativa cadastrada")

    def test_snippet_is_rendered_as_text_not_executed(self):
        response = self.client.get("/install/granimarmores-pitondo/")
        content = response.content.decode()

        self.assertIn("&lt;script", content)
        self.assertIn("data-tenant=&quot;granimarmores-pitondo&quot;", content)
        self.assertNotRegex(content, r'<script[^>]+src="https://livia\.smartcontrolbrasil\.com\.br/widget\.js"')

    def test_install_json_includes_readiness_contract(self):
        response = self.client.get("/install/granimarmores-pitondo.json")
        data = response.json()

        self.assertEqual(data["readiness"], SITE_READINESS_READY)
        self.assertIn("readiness_checks", data)
        self.assertIn("assistant_name", data)
        self.assertIn("install_instructions", data)
        self.assertIn(" defer>", data["snippet"])


class WidgetContractExtendedTests(TestCase):
    def test_widget_requires_data_tenant(self):
        response = self.client.get("/widget.js")
        content = response.content.decode()

        self.assertIn('getAttribute("data-tenant")', content)
        self.assertIn("data-tenant é obrigatório", content)

    def test_widget_initializes_once_per_tenant(self):
        response = self.client.get("/widget.js")
        content = response.content.decode()

        self.assertIn("__liviaWidgetInit", content)
        self.assertIn("Widget já inicializado", content)

    def test_widget_builds_chat_endpoint_from_script_src(self):
        response = self.client.get("/widget.js")
        content = response.content.decode()

        self.assertIn('new URL("/api/chat/", scriptEl.src).href', content)
        self.assertIn('getAttribute("data-api-url")', content)

    def test_widget_logs_origin_or_config_errors(self):
        response = self.client.get("/widget.js")
        content = response.content.decode()

        self.assertIn("Configuração do widget recusada", content)

    def test_widget_has_no_secrets(self):
        response = self.client.get("/widget.js")
        content = response.content.decode().lower()

        self.assertNotIn("api_key", content)
        self.assertNotIn("secret", content)
        self.assertNotIn("token", content)

    def test_official_snippet_supports_defer_and_legacy_api_url(self):
        snippet = build_widget_snippet("tenant-slug")

        self.assertIn('data-tenant="tenant-slug"', snippet)
        self.assertIn('data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"', snippet)
        self.assertIn(" defer>", snippet)

    def test_blocked_origin_returns_403_on_config(self):
        tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        AssistantProfile.objects.create(tenant=tenant)
        TenantAllowedOrigin.objects.create(tenant=tenant, origin="https://allowed.example")

        response = self.client.get(
            "/api/widget/config/?tenant=tenant",
            HTTP_ORIGIN="https://blocked.example",
            HTTP_X_LIVIA_TENANT="tenant",
        )

        self.assertEqual(response.status_code, 403)


class TenantSiteReadinessCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", is_active=True)
        AssistantProfile.objects.create(tenant=self.tenant, name="Lívia")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://tenant.example")

    def test_command_reports_ready(self):
        out = StringIO()
        call_command("tenant_site_readiness", tenant="tenant", stdout=out)

        self.assertIn("Readiness: READY", out.getvalue())
        self.assertIn('data-tenant="tenant"', out.getvalue())

    def test_command_reports_not_ready_with_exit_code(self):
        TenantAllowedOrigin.objects.filter(tenant=self.tenant).update(is_active=False)

        with self.assertRaises(CommandError):
            call_command("tenant_site_readiness", tenant="tenant")

    def test_command_json_mode(self):
        out = StringIO()
        call_command("tenant_site_readiness", tenant="tenant", json=True, stdout=out)
        payload = json.loads(out.getvalue())

        self.assertEqual(payload["tenant"], "tenant")
        self.assertEqual(payload["readiness"]["readiness"], SITE_READINESS_READY)
        self.assertIn("snippet", payload)
        self.assertNotIn("secret", out.getvalue().lower())

    @override_settings(LIVIA_AI_ENABLED=False)
    def test_command_warning_does_not_fail(self):
        profile = self.tenant.assistant_profile
        profile.use_ai = True
        profile.save(update_fields=["use_ai"])

        out = StringIO()
        call_command("tenant_site_readiness", tenant="tenant", stdout=out)

        self.assertIn("Readiness: WARNING", out.getvalue())

    def test_command_missing_tenant_fails(self):
        with self.assertRaises(CommandError):
            call_command("tenant_site_readiness", tenant="missing")


class SideEffectPolicyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", is_active=True)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
        SMART360_LEAD_DISPATCH_REAL_ENABLED=False,
    )
    def test_smart360_real_requires_explicit_enable(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.SMART360_LEAD_DISPATCH,
            tenant=self.tenant,
            integration_configured=True,
        )
        self.assertEqual(decision.status, SideEffectStatus.BLOCKED)
        self.assertEqual(decision.code, "smart360_real_not_explicitly_enabled")

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_smart360_dry_run_is_safe(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.SMART360_LEAD_DISPATCH,
            tenant=self.tenant,
            integration_configured=False,
        )
        self.assertEqual(decision.status, SideEffectStatus.DRY_RUN)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
        SMART360_LEAD_DISPATCH_REAL_ENABLED=True,
        SMART360_LEAD_DISPATCH_REAL_ALLOWED_ENVS="development",
        SMART360_LEAD_DISPATCH_REAL_TENANT_ALLOWLIST="tenant",
        LIVIA_ENVIRONMENT="development",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_smart360_real_enabled_only_with_all_gates(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.SMART360_LEAD_DISPATCH,
            tenant=self.tenant,
            integration_configured=True,
        )
        self.assertEqual(decision.status, SideEffectStatus.REAL_ENABLED)

    @override_settings(
        LIVIA_WEBHOOKS_ENABLED=False,
        LIVIA_WEBHOOKS_DRY_RUN=False,
        LIVIA_WEBHOOKS_REAL_ENABLED=True,
    )
    def test_tenant_cannot_expand_globally_disabled_webhook(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.WEBHOOK_DELIVERY,
            tenant=self.tenant,
            integration_configured=True,
        )
        self.assertEqual(decision.status, SideEffectStatus.BLOCKED)
        self.assertEqual(decision.code, "webhooks_disabled")

    @override_settings(
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
        LIVIA_ENVIRONMENT="production",
        LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="/tmp/fake-livia-google-sa.json",
        LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED=True,
        LIVIA_GOOGLE_DRIVE_SYNC_REAL_ALLOWED_ENVS="production",
        LIVIA_GOOGLE_DRIVE_SYNC_REAL_TENANT_ALLOWLIST="tenant",
    )
    def test_google_drive_sync_real_enabled_only_with_all_gates(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.GOOGLE_DRIVE_SYNC,
            tenant=self.tenant,
        )
        self.assertEqual(decision.status, SideEffectStatus.REAL_ENABLED)
        self.assertTrue(decision.external_call_allowed)

    @override_settings(
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_EMBEDDING_API_KEY="",
    )
    def test_openai_embedding_without_api_key_is_fail_closed(self):
        decision = evaluate_side_effect_policy(
            side_effect=SideEffectType.OPENAI_EMBEDDING,
            tenant=self.tenant,
        )
        self.assertEqual(decision.status, SideEffectStatus.BLOCKED)
        self.assertEqual(decision.code, "openai_embedding_missing_api_key")


class SideEffectReadinessCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", is_active=True)

    @override_settings(
        LIVIA_AI_ENABLED=False,
        LIVIA_RAG_ENABLED=True,
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_side_effect_readiness_human_output(self):
        out = StringIO()
        call_command("tenant_side_effect_readiness", tenant="tenant", stdout=out)
        text = out.getvalue()
        self.assertIn("Tenant: tenant", text)
        self.assertIn("SMART360_LEAD_DISPATCH", text)
        self.assertIn("OVERALL: SAFE", text)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
        SMART360_LEAD_DISPATCH_REAL_ENABLED=True,
        SMART360_LEAD_DISPATCH_REAL_ALLOWED_ENVS="development",
        LIVIA_ENVIRONMENT="development",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
        SMART360_BASE_URL="https://smart360.example",
        SMART360_M2M_TOKEN="token",
    )
    def test_side_effect_readiness_fails_when_real_enabled(self):
        with self.assertRaises(CommandError):
            call_command("tenant_side_effect_readiness", tenant="tenant")


@override_settings(
    LIVIA_AI_ENABLED=False,
    SMART360_LEAD_DISPATCH_ENABLED=False,
    SMART360_LEAD_DISPATCH_DRY_RUN=True,
)
class GranimarmoresLocalSmokeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Granimármores Pitondo",
            slug="granimarmores-pitondo",
            domain="https://www.granimarmorespitondo.com.br",
            is_active=True,
        )
        AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia Granimármores",
            initial_message="Olá! Sou a Lívia da Granimármores Pitondo. Como posso ajudar com seu projeto?",
            tone="profissional, acolhedora e objetiva",
            primary_goal="qualificar solicitações de orçamento para marmoraria",
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511940241328",
            handoff_whatsapp_label="Falar com atendimento Granimármores",
        )
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.granimarmorespitondo.com.br")

    def test_smoke_install_widget_and_cors_policy(self):
        install = self.client.get("/install/granimarmores-pitondo/")
        self.assertEqual(install.status_code, 200)
        self.assertContains(install, "READY")
        self.assertContains(install, "data-tenant=&quot;granimarmores-pitondo&quot;")

        widget = self.client.get("/widget.js")
        self.assertEqual(widget.status_code, 200)
        self.assertIn("application/javascript", widget["Content-Type"])

        cfg_ok = self.client.get(
            "/api/widget/config/?tenant=granimarmores-pitondo",
            HTTP_ORIGIN="https://www.granimarmorespitondo.com.br",
            HTTP_X_LIVIA_TENANT="granimarmores-pitondo",
        )
        self.assertEqual(cfg_ok.status_code, 200)

        cfg_blocked = self.client.get(
            "/api/widget/config/?tenant=granimarmores-pitondo",
            HTTP_ORIGIN="https://evil-example.com",
            HTTP_X_LIVIA_TENANT="granimarmores-pitondo",
        )
        self.assertEqual(cfg_blocked.status_code, 403)

    def test_smoke_chat_idempotency_lead_and_handoff(self):
        request_id = str(uuid.uuid4())
        payload = {
            "tenant": "granimarmores-pitondo",
            "session_id": "gp-session-1",
            "request_id": request_id,
            "message": "Quero orçamento para bancada de cozinha em São Paulo.",
        }
        headers = {
            "content_type": "application/json",
            "HTTP_ORIGIN": "https://www.granimarmorespitondo.com.br",
            "HTTP_X_LIVIA_TENANT": "granimarmores-pitondo",
            "HTTP_X_LIVIA_REQUEST_ID": request_id,
        }

        first = self.client.post("/api/chat/", data=json.dumps(payload), **headers)
        second = self.client.post("/api/chat/", data=json.dumps(payload), **headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ChatRequest.objects.filter(tenant=self.tenant, session_id="gp-session-1").count(), 1)
        self.assertEqual(Conversation.objects.filter(tenant=self.tenant, session_id="gp-session-1").count(), 1)
        self.assertLessEqual(LeadDraft.objects.filter(tenant=self.tenant).count(), 1)

        handoff_request_id = str(uuid.uuid4())
        handoff_payload = {
            "tenant": "granimarmores-pitondo",
            "session_id": "gp-session-2",
            "request_id": handoff_request_id,
            "message": "Quero falar com um atendente humano agora.",
        }
        handoff = self.client.post(
            "/api/chat/",
            data=json.dumps(handoff_payload),
            content_type="application/json",
            HTTP_ORIGIN="https://www.granimarmorespitondo.com.br",
            HTTP_X_LIVIA_TENANT="granimarmores-pitondo",
            HTTP_X_LIVIA_REQUEST_ID=handoff_request_id,
        )
        self.assertEqual(handoff.status_code, 200)
        self.assertGreaterEqual(HandoffRequest.objects.filter(tenant=self.tenant).count(), 1)


class TenantChatSmokeCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Granimármores Pitondo",
            slug="granimarmores-pitondo",
            domain="https://www.granimarmorespitondo.com.br",
            is_active=True,
        )
        AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia Granimármores",
            use_ai=False,
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511940241328",
        )
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.granimarmorespitondo.com.br")

    def test_chat_smoke_command_rolls_back_by_default(self):
        before_conversations = Conversation.objects.filter(tenant=self.tenant).count()
        output = StringIO()
        call_command("tenant_chat_smoke", tenant="granimarmores-pitondo", scenario="commercial", stdout=output)
        after_conversations = Conversation.objects.filter(tenant=self.tenant).count()

        self.assertEqual(before_conversations, after_conversations)
        self.assertIn("Rollback applied: true", output.getvalue())

    def test_chat_smoke_command_json_output(self):
        output = StringIO()
        call_command("tenant_chat_smoke", tenant="granimarmores-pitondo", scenario="commercial", json=True, stdout=output)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["tenant"], "granimarmores-pitondo")
        self.assertTrue(payload["rollback_applied"])
        self.assertEqual(payload["external_calls"]["smart360_http"], 0)
        self.assertEqual(payload["external_calls"]["webhook_http"], 0)
