from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from integrations.models import TenantWebhookConfig
from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalAssistantSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="settings-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="settings-viewer", password="pass")
        self.manager = get_user_model().objects.create_user(username="settings-manager", password="pass")
        self.outsider = get_user_model().objects.create_user(username="settings-outsider", password="pass")
        self.tenant = Tenant.objects.create(name="Tenant Config A", slug="tenant-config-a", domain="https://a.example")
        self.other_tenant = Tenant.objects.create(name="Tenant Config B", slug="tenant-config-b", domain="https://b.example")
        self.profile = AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia A",
            business_name="Negócio A",
            business_domain="serviços B2B",
            short_description="Atendimento comercial do Tenant A.",
            primary_goal="qualificar oportunidades",
            tone="consultivo",
            initial_message="Olá! Sou a Lívia A.",
        )
        self.other_profile = AssistantProfile.objects.create(tenant=self.other_tenant, name="Lívia B")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://a.example")
        KnowledgeDocument.objects.create(tenant=self.tenant, title="Doc A", slug="doc-a", content="Conteúdo A")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
            last_inventory_status=TenantRagConfiguration.InventoryStatus.SUCCESS,
            last_index_status=TenantRagConfiguration.InventoryStatus.SUCCESS,
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)
        TenantMembership.objects.create(tenant=self.tenant, user=self.manager, role=TenantMembership.Role.MANAGER)

    def profile_payload(self, **overrides):
        payload = {
            "tenant": self.tenant.pk,
            "action": "save_profile",
            "profile-name": "Lívia Operacional",
            "profile-business_name": "Negócio Operacional",
            "profile-business_domain": "educação corporativa",
            "profile-short_description": "Qualifica demandas com contexto do tenant.",
            "profile-primary_goal": "qualificar projetos prioritários",
            "profile-tone": "direto e acolhedor",
            "profile-initial_message": "Olá! Sou a Lívia Operacional.",
            "profile-widget_title": "Atendimento Operacional",
            "profile-launcher_label": "Conversar agora",
            "profile-primary_color": "#123abc",
            "profile-position": "bottom_left",
            "profile-placeholder_text": "Digite sua necessidade",
            "profile-show_branding": "on",
            "profile-is_widget_enabled": "on",
            "profile-use_ai": "on",
            "profile-is_active": "on",
        }
        payload.update(overrides)
        return payload

    def test_authorized_user_reads_assistant_settings_and_operational_links(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:settings"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Identidade")
        self.assertContains(response, "Objetivo conversacional")
        self.assertContains(response, "Widget")
        self.assertContains(response, "Estado operacional")
        self.assertContains(response, "serviços B2B")
        self.assertContains(response, "Atendimento comercial do Tenant A.")
        self.assertContains(response, reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))
        self.assertContains(response, reverse("operations_portal:knowledge_base_documents") + f"?tenant={self.tenant.pk}")
        self.assertContains(response, f"/install/{self.tenant.slug}/")
        self.assertContains(response, "/api/widget/config/?tenant=tenant-config-a")
        self.assertContains(response, "Retrieval: ativo")
        self.assertContains(response, "Allowed origins")
        self.assertContains(response, "https://a.example")
        self.assertContains(response, "Embeddings")
        self.assertNotContains(response, "API_KEY")
        self.assertNotContains(response, "SECRET")
        self.assertNotContains(response, "SMART360_M2M_TOKEN")

    def test_access_denied_without_membership(self):
        self.client.force_login(self.outsider)

        response = self.client.get(reverse("operations_portal:settings"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 403)

    def test_manager_can_read_but_cannot_save(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("operations_portal:settings"), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Salvar configurações")
        response = self.client.post(reverse("operations_portal:settings"), self.profile_payload())
        self.assertEqual(response.status_code, 403)

    def test_profile_and_widget_update_is_tenant_scoped_and_has_no_side_effects(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("operations_portal:settings"), self.profile_payload())

        self.assertRedirects(response, f"{reverse('operations_portal:settings')}?tenant={self.tenant.pk}")
        self.profile.refresh_from_db()
        self.other_profile.refresh_from_db()
        self.assertEqual(self.profile.name, "Lívia Operacional")
        self.assertEqual(self.profile.business_name, "Negócio Operacional")
        self.assertEqual(self.profile.business_domain, "educação corporativa")
        self.assertEqual(self.profile.short_description, "Qualifica demandas com contexto do tenant.")
        self.assertEqual(self.profile.primary_goal, "qualificar projetos prioritários")
        self.assertEqual(self.profile.tone, "direto e acolhedor")
        self.assertTrue(self.profile.use_ai)
        self.assertTrue(self.profile.is_active)
        self.assertEqual(self.profile.initial_message, "Olá! Sou a Lívia Operacional.")
        self.assertEqual(self.profile.widget_title, "Atendimento Operacional")
        self.assertEqual(self.profile.launcher_label, "Conversar agora")
        self.assertEqual(self.profile.placeholder_text, "Digite sua necessidade")
        self.assertEqual(self.profile.primary_color, "#123abc")
        self.assertEqual(self.profile.position, "bottom_left")
        self.assertTrue(self.profile.show_branding)
        self.assertTrue(self.profile.is_widget_enabled)
        self.assertEqual(self.other_profile.name, "Lívia B")
        self.assertFalse(TenantWebhookConfig.objects.filter(tenant=self.tenant).exists())

    def test_tenant_isolation_rejects_cross_tenant_post(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("operations_portal:settings"),
            self.profile_payload(tenant=self.other_tenant.pk, **{"profile-name": "Nome indevido"}),
        )

        self.assertEqual(response.status_code, 403)
        self.other_profile.refresh_from_db()
        self.assertEqual(self.other_profile.name, "Lívia B")

    def test_invalid_color_and_position_are_rejected(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("operations_portal:settings"), self.profile_payload(**{"profile-primary_color": "blue"}))
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.primary_color, "#2563eb")

        response = self.client.post(reverse("operations_portal:settings"), self.profile_payload(**{"profile-position": "top_right"}))
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.position, "bottom_right")

    def test_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)

        response = csrf_client.post(reverse("operations_portal:settings"), self.profile_payload())

        self.assertEqual(response.status_code, 403)
