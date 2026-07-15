from io import StringIO

from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase, override_settings

from knowledge_base.models import KnowledgeDocument
from tenants.models import AssistantProfile, Tenant
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.onboarding import (
    TenantOnboardingService,
    build_widget_snippet,
    normalize_allowed_origin,
)


class SeedInitialTenantsCommandTests(TestCase):
    def test_seed_initial_tenants_is_idempotent(self):
        call_command("seed_initial_tenants", verbosity=0)
        call_command("seed_initial_tenants", verbosity=0)

        self.assertEqual(Tenant.objects.filter(slug="smart-control-brasil").count(), 1)
        self.assertEqual(Tenant.objects.filter(slug="granimarmores-pitondo").count(), 1)
        self.assertEqual(AssistantProfile.objects.count(), 2)
        smart_tenant = Tenant.objects.get(slug="smart-control-brasil")
        self.assertTrue(smart_tenant.is_active)
        self.assertIn("Smart Control Brasil", smart_tenant.assistant_profile.initial_message)



class TenantOnboardingServiceTests(TestCase):
    def setUp(self):
        self.service = TenantOnboardingService()

    def test_onboard_creates_tenant_when_missing(self):
        result = self.service.onboard(
            slug="canecadegaragem",
            name="Caneca de Garagem",
            domain="https://canecadegaragem.com.br",
        )

        self.assertTrue(result.created_tenant)
        self.assertTrue(Tenant.objects.filter(slug="canecadegaragem").exists())
        self.assertEqual(result.tenant.domain, "https://canecadegaragem.com.br")

    def test_onboard_updates_existing_tenant_without_duplicate(self):
        Tenant.objects.create(slug="granimarmores-pitondo", name="Antigo", domain="https://old.example")

        result = self.service.onboard(
            slug="granimarmores-pitondo",
            name="Granimármores Pitondo",
            domain="https://www.granimarmorespitondo.com.br/",
        )

        self.assertFalse(result.created_tenant)
        self.assertEqual(Tenant.objects.filter(slug="granimarmores-pitondo").count(), 1)
        self.assertEqual(result.tenant.name, "Granimármores Pitondo")
        self.assertEqual(result.allowed_origin, "https://www.granimarmorespitondo.com.br")

    def test_onboard_creates_assistant_profile(self):
        result = self.service.onboard(
            slug="profile-new",
            name="Profile New",
            domain="profile-new.com.br",
            assistant_name="Lívia",
            primary_goal="qualificar atendimento comercial",
        )

        self.assertTrue(result.created_profile)
        self.assertEqual(AssistantProfile.objects.count(), 1)
        self.assertEqual(result.assistant_profile.primary_goal, "qualificar atendimento comercial")

    def test_onboard_updates_existing_assistant_profile_without_duplicate(self):
        tenant = Tenant.objects.create(slug="profile-existing", name="Profile Existing", domain="https://old.example")
        AssistantProfile.objects.create(tenant=tenant, name="Assistente antiga", primary_goal="antigo")

        result = self.service.onboard(
            slug="profile-existing",
            name="Profile Existing",
            domain="profile-existing.com.br",
            assistant_name="Lívia Nova",
            primary_goal="novo objetivo",
        )

        self.assertFalse(result.created_profile)
        self.assertEqual(AssistantProfile.objects.count(), 1)
        self.assertEqual(result.assistant_profile.name, "Lívia Nova")
        self.assertEqual(result.assistant_profile.primary_goal, "novo objetivo")

    def test_build_widget_snippet_uses_defaults(self):
        snippet = build_widget_snippet("smart-control-brasil")

        self.assertIn('src="https://livia.smartcontrolbrasil.com.br/widget.js"', snippet)
        self.assertIn('data-tenant="smart-control-brasil"', snippet)
        self.assertIn('data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"', snippet)

    def test_normalize_domain_with_https(self):
        allowed_origin, warnings = normalize_allowed_origin("https://example.com.br/")

        self.assertEqual(allowed_origin, "https://example.com.br")
        self.assertNotIn("Domain uses http; production embeds should use https.", warnings)

    def test_normalize_domain_without_scheme_assumes_https(self):
        allowed_origin, warnings = normalize_allowed_origin("example.com.br")

        self.assertEqual(allowed_origin, "https://example.com.br")
        self.assertIn("Domain did not include a scheme; https:// was assumed.", warnings)

    def test_seed_knowledge_creates_base_document(self):
        result = self.service.onboard(
            slug="seed-new",
            name="Seed New",
            domain="seed-new.com.br",
            primary_goal="qualificar oportunidades",
            seed_knowledge=True,
        )

        self.assertEqual(result.created_knowledge_count, 1)
        document = KnowledgeDocument.objects.get(tenant=result.tenant, title="Sobre Seed New")
        self.assertEqual(document.source_type, "manual")
        self.assertTrue(document.is_active)
        self.assertEqual(document.tags, ["institucional", "onboarding"])

    def test_seed_knowledge_is_idempotent(self):
        first = self.service.onboard(
            slug="seed-idempotent",
            name="Seed Idempotent",
            domain="seed-idempotent.com.br",
            seed_knowledge=True,
        )
        second = self.service.onboard(
            slug="seed-idempotent",
            name="Seed Idempotent",
            domain="seed-idempotent.com.br",
            seed_knowledge=True,
        )

        self.assertEqual(first.created_knowledge_count, 1)
        self.assertEqual(second.created_knowledge_count, 0)
        self.assertEqual(KnowledgeDocument.objects.filter(tenant=second.tenant).count(), 1)

    def test_dry_run_does_not_write_to_database(self):
        result = self.service.onboard(
            slug="dry-run-tenant",
            name="Dry Run Tenant",
            domain="dry-run.example.com",
            seed_knowledge=True,
            dry_run=True,
        )

        self.assertTrue(result.created_tenant)
        self.assertEqual(result.created_knowledge_count, 1)
        self.assertFalse(Tenant.objects.filter(slug="dry-run-tenant").exists())
        self.assertEqual(AssistantProfile.objects.count(), 0)
        self.assertEqual(KnowledgeDocument.objects.count(), 0)


class OnboardTenantCommandTests(TestCase):
    def test_onboard_tenant_command_prints_snippet(self):
        output = StringIO()

        call_command(
            "onboard_tenant",
            "--slug",
            "command-tenant",
            "--name",
            "Command Tenant",
            "--domain",
            "command.example.com",
            stdout=output,
        )

        content = output.getvalue()
        self.assertIn("Snippet do widget:", content)
        self.assertIn('data-tenant="command-tenant"', content)
        self.assertIn("Tenant: command-tenant (criado)", content)



class TenantInstallPackageTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Granimármores Pitondo",
            slug="granimarmores-pitondo",
            domain="www.granimarmorespitondo.com.br/",
            is_active=True,
        )
        AssistantProfile.objects.create(tenant=self.tenant, name="Lívia")

    def test_install_page_returns_200_for_existing_tenant(self):
        response = self.client.get("/install/granimarmores-pitondo/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Instalação da Lívia")
        self.assertContains(response, "Granimármores Pitondo")

    def test_install_html_contains_widget_snippet_and_data_tenant(self):
        response = self.client.get("/install/granimarmores-pitondo/")

        self.assertContains(response, "https://livia.smartcontrolbrasil.com.br/widget.js")
        self.assertContains(response, 'data-tenant="granimarmores-pitondo"')
        self.assertContains(response, 'data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"')

    def test_install_html_does_not_contain_secrets(self):
        from integrations.models import TenantWebhookConfig

        TenantWebhookConfig.objects.create(
            tenant=self.tenant,
            name="N8N",
            target_url="https://n8n.example/webhook",
            secret_token="super-secret-token",
        )

        response = self.client.get("/install/granimarmores-pitondo/")
        content = response.content.decode()

        self.assertNotIn("super-secret-token", content)
        self.assertNotIn("secret_token", content)

    def test_install_json_returns_expected_payload(self):
        response = self.client.get("/install/granimarmores-pitondo.json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tenant"], "granimarmores-pitondo")
        self.assertEqual(data["name"], "Granimármores Pitondo")
        self.assertTrue(data["is_active"])
        self.assertEqual(data["allowed_origin"], "https://www.granimarmorespitondo.com.br")
        self.assertIn('data-tenant="granimarmores-pitondo"', data["snippet"])

    def test_install_json_does_not_contain_secrets(self):
        from integrations.models import TenantWebhookConfig

        TenantWebhookConfig.objects.create(
            tenant=self.tenant,
            name="N8N",
            target_url="https://n8n.example/webhook",
            secret_token="super-secret-token",
        )

        response = self.client.get("/install/granimarmores-pitondo.json")
        payload_text = str(response.json())

        self.assertNotIn("super-secret-token", payload_text)
        self.assertNotIn("secret_token", payload_text)

    def test_install_missing_tenant_returns_404(self):
        html_response = self.client.get("/install/tenant-inexistente/")
        json_response = self.client.get("/install/tenant-inexistente.json")

        self.assertEqual(html_response.status_code, 404)
        self.assertContains(html_response, "Tenant não encontrado", status_code=404)
        self.assertEqual(json_response.status_code, 404)
        self.assertEqual(json_response.json()["error"], "Tenant not found.")

    def test_inactive_tenant_shows_warning(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        response = self.client.get("/install/granimarmores-pitondo/")

        self.assertContains(response, "inativo")
        self.assertContains(response, "widget não processará atendimentos")

    @override_settings(LIVIA_ALLOWED_WIDGET_ORIGINS=["https://outro.example"])
    def test_install_package_warns_when_origin_is_not_allowed(self):
        response = self.client.get("/install/granimarmores-pitondo.json")

        self.assertIn("não está em LIVIA_ALLOWED_WIDGET_ORIGINS", " ".join(response.json()["warnings"]))

    def test_tenant_admin_exposes_install_url_and_widget_snippet_readonly(self):
        tenant_admin = admin.site._registry[Tenant]

        self.assertIn("install_url", tenant_admin.readonly_fields)
        self.assertIn("widget_snippet_preview", tenant_admin.readonly_fields)
        self.assertIn("/install/granimarmores-pitondo/", tenant_admin.install_url(self.tenant))
        self.assertIn('data-tenant="granimarmores-pitondo"', tenant_admin.widget_snippet_preview(self.tenant))

    def test_install_package_reuses_normalized_domain(self):
        package = TenantInstallPackageService().build_for_tenant(self.tenant)

        self.assertEqual(package.allowed_origin, "https://www.granimarmorespitondo.com.br")
