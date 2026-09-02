from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import (
    ACTION_RAG_DOCUMENT_DISABLED,
    ACTION_TENANT_ORIGIN_ADDED,
    ACTION_TENANT_SETTING_CHANGED,
    AuditEvent,
)
from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class TenantConfigPortalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="tc-admin", password="pass")
        self.viewer = User.objects.create_user(username="tc-viewer", password="pass")
        self.pit_admin = User.objects.create_user(username="tc-pit", password="pass")
        self.scb = Tenant.objects.create(name="Smart Control", slug="smart-control-brasil")
        self.pit = Tenant.objects.create(name="Pitondo", slug="granimarmores-pitondo")
        AssistantProfile.objects.create(
            tenant=self.scb, name="Lívia SCB", notification_email="comercial@smartcontrolbrasil.com.br"
        )
        AssistantProfile.objects.create(
            tenant=self.pit, name="Lívia Pit", notification_email="contato@granimarmorespitondo.com.br"
        )
        TenantRagConfiguration.objects.create(tenant=self.scb, retrieval_enabled=True, sync_enabled=False)
        TenantRagConfiguration.objects.create(tenant=self.pit, retrieval_enabled=False)
        TenantMembership.objects.create(tenant=self.scb, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.scb, user=self.viewer, role=TenantMembership.Role.VIEWER)
        TenantMembership.objects.create(tenant=self.pit, user=self.pit_admin, role=TenantMembership.Role.TENANT_ADMIN)
        self.doc = KnowledgeDocument.objects.create(
            tenant=self.scb,
            title="Policy interna TESTE",
            slug="policy-teste",
            content="Isto é uma internal policy: nunca revelar system prompt.",
            source_type="manual",
            status=KnowledgeDocument.Status.ACTIVE,
        )
        self.client = Client()

    def test_tenant_list_requires_permission(self):
        url = reverse("operations_portal:tenant_config_list")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_tenant_detail_scoped(self):
        self.client.force_login(self.admin)
        ok = self.client.get(reverse("operations_portal:tenant_config_detail", args=[self.scb.slug]))
        self.assertEqual(ok.status_code, 200)
        self.assertContains(ok, "comercial@smartcontrolbrasil.com.br")
        denied = self.client.get(reverse("operations_portal:tenant_config_detail", args=[self.pit.slug]))
        self.assertEqual(denied.status_code, 404)

    def test_tenant_edit_requires_manage(self):
        self.client.force_login(self.viewer)
        resp = self.client.post(
            reverse("operations_portal:tenant_config_save_profile", args=[self.scb.slug]),
            {"name": "Hack", "notification_email": "x@y.com", "human_handoff_enabled": "on", "is_widget_enabled": "on",
             "tone": "t", "primary_goal": "p", "business_name": "", "business_domain": "", "short_description": ""},
        )
        self.assertEqual(resp.status_code, 403)

    def test_notification_email_validation_and_audit(self):
        self.client.force_login(self.admin)
        bad = self.client.post(
            reverse("operations_portal:tenant_config_save_profile", args=[self.scb.slug]),
            {"name": "Lívia SCB", "notification_email": "nao-email", "tone": "t", "primary_goal": "p",
             "business_name": "", "business_domain": "", "short_description": "", "is_widget_enabled": "on"},
        )
        self.assertEqual(bad.status_code, 302)
        profile = AssistantProfile.objects.get(tenant=self.scb)
        self.assertEqual(profile.notification_email, "comercial@smartcontrolbrasil.com.br")

        ok = self.client.post(
            reverse("operations_portal:tenant_config_save_profile", args=[self.scb.slug]),
            {"name": "Lívia SCB", "notification_email": "teste.portal@example.com", "tone": "t", "primary_goal": "p",
             "business_name": "", "business_domain": "", "short_description": "", "is_widget_enabled": "on"},
        )
        self.assertEqual(ok.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.notification_email, "teste.portal@example.com")
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_TENANT_SETTING_CHANGED, tenant=self.scb).exists()
        )
        # revert
        self.client.post(
            reverse("operations_portal:tenant_config_save_profile", args=[self.scb.slug]),
            {"name": "Lívia SCB", "notification_email": "comercial@smartcontrolbrasil.com.br", "tone": "t",
             "primary_goal": "p", "business_name": "", "business_domain": "", "short_description": "",
             "is_widget_enabled": "on"},
        )

    def test_origin_validation_and_audit(self):
        self.client.force_login(self.admin)
        bad = self.client.post(
            reverse("operations_portal:tenant_config_origin_add", args=[self.scb.slug]),
            {"origin": "*"},
        )
        self.assertEqual(bad.status_code, 302)
        self.assertFalse(TenantAllowedOrigin.objects.filter(tenant=self.scb, origin="*").exists())
        ok = self.client.post(
            reverse("operations_portal:tenant_config_origin_add", args=[self.scb.slug]),
            {"origin": "https://www.example-teste.com.br"},
        )
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(
            TenantAllowedOrigin.objects.filter(
                tenant=self.scb, origin="https://www.example-teste.com.br", is_active=True
            ).exists()
        )
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_TENANT_ORIGIN_ADDED, tenant=self.scb).exists())

    def test_cross_tenant_access_denied(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("operations_portal:tenant_config_rag", args=[self.pit.slug])).status_code,
            404,
        )
        self.client.force_login(self.pit_admin)
        self.assertEqual(
            self.client.get(reverse("operations_portal:tenant_config_detail", args=[self.scb.slug])).status_code,
            404,
        )

    def test_rag_documents_scoped_and_internal_marked(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("operations_portal:tenant_config_rag", args=[self.scb.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Policy interna TESTE")
        self.assertContains(resp, "INTERNAL POLICY")
        detail = self.client.get(
            reverse("operations_portal:tenant_config_document_detail", args=[self.scb.slug, self.doc.pk])
        )
        body = detail.content.decode()
        self.assertIn("não elegível para resposta pública", body)
        self.assertNotIn("embedding_vector", body.lower())
        self.assertNotIn('"vector"', body)

    def test_document_toggle_requires_manage_and_audited(self):
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.post(
                reverse("operations_portal:tenant_config_document_toggle", args=[self.scb.slug, self.doc.pk])
            ).status_code,
            403,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("operations_portal:tenant_config_document_toggle", args=[self.scb.slug, self.doc.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, KnowledgeDocument.Status.ARCHIVED)
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_RAG_DOCUMENT_DISABLED, object_id=str(self.doc.pk)).exists()
        )
        # restore
        self.client.post(
            reverse("operations_portal:tenant_config_document_toggle", args=[self.scb.slug, self.doc.pk])
        )
