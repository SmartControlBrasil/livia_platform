from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from conversations.models import Conversation, HandoffRequest
from leads.models import LeadDraft
from tenants.models import Tenant

from .selectors import get_crm_status

TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalAccessTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/login/?next=/painel/")

    def test_authenticated_non_staff_user_gets_403(self):
        user = get_user_model().objects.create_user(username="viewer", password="pass")
        self.client.force_login(user)

        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_staff_non_superuser_gets_403_until_tenant_scope_exists(self):
        user = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)

        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 403)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="pass", email="admin@example.com"
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil", slug="smart-control-brasil", is_active=True
        )
        inactive = Tenant.objects.create(name="Tenant Inativo", slug="tenant-inativo", is_active=False)
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="session-1",
            is_qualified=True,
            lead_state=Conversation.LeadState.COLLECT_NEED,
        )
        Conversation.objects.create(tenant=inactive, session_id="session-2")
        LeadDraft.objects.create(
            tenant=self.tenant, conversation=self.conversation, status=LeadDraft.Status.QUALIFIED, name="Maria"
        )
        LeadDraft.objects.create(tenant=self.tenant, status=LeadDraft.Status.SENT_TO_CRM, name="João")
        LeadDraft.objects.create(tenant=self.tenant, status=LeadDraft.Status.FAILED, name="Ana")
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=HandoffRequest.Status.PENDING,
            priority=HandoffRequest.Priority.HIGH,
        )

    def test_dashboard_renders_real_kpis(self):
        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lívia Platform")
        self.assertContains(response, "Tenants ativos")
        self.assertContains(response, "Leads enviados ao CRM")
        self.assertContains(response, "Leads com falha")
        self.assertContains(response, "Handoffs alta prioridade")
        self.assertContains(response, "session-1")
        self.assertContains(response, "Maria")
        self.assertContains(response, "CRM Smart360")
        self.assertContains(response, "Rascunhos de leads")
        self.assertContains(response, "Coleta da necessidade")

    def test_dashboard_uses_bounded_queries(self):
        with self.assertNumQueries(10):
            self.client.get(reverse("operations_portal:dashboard"))

    def test_placeholder_routes_do_not_break_sidebar(self):
        response = self.client.get(reverse("operations_portal:placeholder", kwargs={"section": "leads"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leads")

    def test_sidebar_links_exist(self):
        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertContains(response, reverse("operations_portal:placeholder", kwargs={"section": "conversas"}))
        self.assertContains(response, reverse("operations_portal:placeholder", kwargs={"section": "integracoes"}))
        self.assertContains(response, reverse("admin:index"))


class OperationsPortalCRMStatusTests(TestCase):
    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        SMART360_BASE_URL="",
        SMART360_M2M_TOKEN="",
    )
    def test_crm_status_disabled(self):
        status = get_crm_status()

        self.assertEqual(status.state, "Desligado")
        self.assertEqual(status.tone, "secondary")

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        SMART360_BASE_URL="https://smart360.example",
        SMART360_M2M_TOKEN="secret-value",
    )
    def test_crm_status_dry_run(self):
        status = get_crm_status()

        self.assertEqual(status.state, "Dry-run")
        self.assertEqual(status.tone, "warning")
        self.assertNotIn("https://smart360.example", status.detail)
        self.assertNotIn("secret-value", status.detail)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
        SMART360_BASE_URL="https://smart360.example",
        SMART360_M2M_TOKEN="secret-value",
    )
    def test_crm_status_real_active(self):
        status = get_crm_status()

        self.assertEqual(status.state, "Ativo real")
        self.assertEqual(status.tone, "success")
        self.assertNotIn("https://smart360.example", status.detail)
        self.assertNotIn("secret-value", status.detail)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
        SMART360_BASE_URL="",
        SMART360_M2M_TOKEN="",
    )
    def test_crm_status_incomplete_real_config(self):
        status = get_crm_status()

        self.assertEqual(status.state, "Configuração incompleta")
        self.assertEqual(status.tone, "danger")
