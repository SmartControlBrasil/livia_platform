from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditEvent
from integrations.models import TenantWebhookConfig
from knowledge_base.models import KnowledgeDocument
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalTenantManagementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="tenant-admin", password="pass")
        self.viewer = get_user_model().objects.create_user(username="viewer", password="pass")
        self.superuser = get_user_model().objects.create_superuser(username="root", password="pass", email="root@example.com")
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a", domain="https://tenant-a.example")
        self.other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b", domain="https://tenant-b.example")
        self.profile = AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia A",
            business_domain="serviços técnicos",
            short_description="Atendimento do Tenant A.",
        )
        AssistantProfile.objects.create(tenant=self.other_tenant, name="Lívia B")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://tenant-a.example")
        TenantAllowedOrigin.objects.create(tenant=self.other_tenant, origin="https://tenant-b.example")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.viewer, role=TenantMembership.Role.VIEWER)

    def test_authorized_admin_lists_only_accessible_tenants(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:tenant_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tenant A")
        self.assertNotContains(response, "Tenant B")
        self.assertContains(response, "Novo tenant")

    def test_access_denied_without_membership(self):
        user = get_user_model().objects.create_user(username="outsider", password="pass")
        self.client.force_login(user)

        response = self.client.get(reverse("operations_portal:tenant_list"))

        self.assertEqual(response.status_code, 403)

    def test_viewer_can_view_but_cannot_write(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Salvar geral")
        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {"tenant": self.tenant.pk, "action": "save_general", "tenant-name": "Novo", "tenant-slug": self.tenant.slug, "tenant-domain": self.tenant.domain, "tenant-is_active": "on"},
        )
        self.assertEqual(response.status_code, 403)

    def test_create_tenant_uses_profile_and_origin_without_enabling_integrations(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("operations_portal:tenant_create"),
            {
                "tenant-name": "Logistics Demo",
                "tenant-slug": "logistics-demo",
                "tenant-domain": "logistics.example",
                "tenant-is_active": "on",
                "profile-name": "Lívia Logistics",
                "profile-business_name": "Logistics Demo",
                "profile-business_domain": "logística e transporte",
                "profile-short_description": "Qualifica fretes com origem e destino.",
                "profile-primary_goal": "qualificar fretes",
                "profile-tone": "consultivo",
                "profile-initial_message": "Olá! Como posso ajudar no frete?",
                "profile-widget_title": "Lívia Logistics",
                "profile-launcher_label": "Falar com logística",
                "profile-primary_color": "#123abc",
                "profile-position": "bottom_right",
                "profile-placeholder_text": "Digite sua rota...",
                "profile-show_branding": "on",
                "profile-is_widget_enabled": "on",
                "origins": "https://logistics.example\nhttps://app.logistics.example",
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(slug="logistics-demo")
        self.assertEqual(tenant.domain, "https://logistics.example")
        profile = tenant.assistant_profile
        self.assertEqual(profile.business_domain, "logística e transporte")
        self.assertEqual(profile.short_description, "Qualifica fretes com origem e destino.")
        self.assertFalse(profile.use_ai)
        self.assertEqual(TenantAllowedOrigin.objects.filter(tenant=tenant, is_active=True).count(), 2)
        self.assertFalse(TenantWebhookConfig.objects.filter(tenant=tenant).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                tenant=tenant,
                action="tenant.onboarding_completed",
                metadata__source="operations_portal.tenants.create",
            ).exists()
        )

    def test_edit_tenant_profile_widget_and_inactive_state(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {
                "tenant": self.tenant.pk,
                "action": "save_general",
                "tenant-name": "Tenant A Editado",
                "tenant-slug": self.tenant.slug,
                "tenant-domain": "https://tenant-a-new.example",
                "tenant-is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Tenant A Editado")
        self.assertEqual(self.tenant.domain, "https://tenant-a-new.example")
        self.assertTrue(self.tenant.is_active)

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {
                "tenant": self.tenant.pk,
                "action": "save_assistant",
                "profile-name": "Lívia Editada",
                "profile-business_name": "Tenant A Negócio",
                "profile-business_domain": "educação corporativa",
                "profile-short_description": "Qualifica treinamentos.",
                "profile-primary_goal": "qualificar projetos",
                "profile-tone": "direto",
                "profile-initial_message": "Olá! Sou a Lívia Editada.",
                "profile-widget_title": "Widget A",
                "profile-launcher_label": "Conversar",
                "profile-primary_color": "#abcdef",
                "profile-position": "bottom_left",
                "profile-placeholder_text": "Mensagem",
                "profile-show_branding": "on",
                "profile-is_widget_enabled": "on",
                "profile-use_ai": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.name, "Lívia Editada")
        self.assertEqual(self.profile.business_domain, "educação corporativa")
        self.assertEqual(self.profile.primary_color, "#abcdef")
        self.assertTrue(self.profile.use_ai)

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {
                "tenant": self.tenant.pk,
                "action": "save_general",
                "tenant-name": "Tenant A Editado",
                "tenant-slug": self.tenant.slug,
                "tenant-domain": "https://tenant-a-new.example",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.is_active)

    def test_origin_validation_and_soft_deactivation(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {"tenant": self.tenant.pk, "action": "save_origins", "origins": "*"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revise as allowed origins")
        self.assertTrue(TenantAllowedOrigin.objects.get(tenant=self.tenant).is_active)

        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {"tenant": self.tenant.pk, "action": "save_origins", "origins": "https://novo.example"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantAllowedOrigin.objects.get(tenant=self.tenant, origin="https://tenant-a.example").is_active)
        self.assertTrue(TenantAllowedOrigin.objects.get(tenant=self.tenant, origin="https://novo.example").is_active)

    def test_detail_shows_snippet_config_endpoint_readiness_and_knowledge_link(self):
        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Doc A",
            slug="doc-a",
            content="Conteúdo A",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-tenant=&quot;tenant-a&quot;')
        self.assertContains(response, "/api/widget/config/?tenant=tenant-a")
        self.assertContains(response, "KnowledgeDocument")
        self.assertContains(response, reverse("operations_portal:knowledge_base_documents") + f"?tenant={self.tenant.pk}")
        self.assertContains(response, "Readiness")

    def test_tenant_isolation_rejects_other_tenant_detail_and_post(self):
        self.client.force_login(self.admin)

        self.assertEqual(
            self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.other_tenant.pk})).status_code,
            404,
        )
        response = self.client.post(
            reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}),
            {"tenant": self.other_tenant.pk, "action": "save_origins", "origins": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TenantAllowedOrigin.objects.filter(tenant=self.other_tenant, origin="https://evil.example").exists())

    def test_superuser_can_find_inactive_tenant(self):
        self.other_tenant.is_active = False
        self.other_tenant.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("operations_portal:tenant_list"), {"status": "inactive"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tenant B")

    def test_no_secret_values_are_rendered_or_audited(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))

        self.assertNotContains(response, "secret")
        for event in AuditEvent.objects.all():
            self.assertNotIn("secret", str(event.before_data).lower())
            self.assertNotIn("secret", str(event.after_data).lower())
