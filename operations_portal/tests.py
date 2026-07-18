import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from conversations.models import Conversation, HandoffRequest, Message
from leads.models import LeadDraft
from leads.services.crm_dispatch import CRMDispatchResult
from tenants.models import Tenant

from .analytics import get_dashboard_analytics
from .selectors import get_crm_status

TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


class PortalUserMixin:
    def login_superuser(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="pass", email="admin@example.com"
        )
        self.client.force_login(self.user)
        return self.user


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalAccessTests(TestCase):
    def portal_urls(self):
        tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")
        conversation = Conversation.objects.create(tenant=tenant, session_id="session-access")
        lead = LeadDraft.objects.create(tenant=tenant, conversation=conversation, status=LeadDraft.Status.FAILED)
        return [
            reverse("operations_portal:dashboard"),
            reverse("operations_portal:conversation_list"),
            reverse("operations_portal:conversation_detail", kwargs={"pk": conversation.pk}),
            reverse("operations_portal:lead_list"),
            reverse("operations_portal:lead_detail", kwargs={"pk": lead.pk}),
        ]

    def test_anonymous_user_is_redirected_to_admin_login(self):
        for url in self.portal_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"/admin/login/?next={url}")

    def test_authenticated_non_staff_user_gets_403(self):
        user = get_user_model().objects.create_user(username="viewer", password="pass")
        self.client.force_login(user)

        for url in self.portal_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_staff_non_superuser_gets_403_until_tenant_scope_exists(self):
        user = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)

        for url in self.portal_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalDashboardTests(PortalUserMixin, TestCase):
    def setUp(self):
        self.login_superuser()
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
        LeadDraft.objects.create(tenant=self.tenant, status=LeadDraft.Status.SENT_TO_CRM, name="Joao")
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
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 18)

    def test_placeholder_routes_do_not_break_sidebar(self):
        response = self.client.get(reverse("operations_portal:placeholder", kwargs={"section": "handoffs"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Handoffs")

    def test_sidebar_links_exist(self):
        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertContains(response, reverse("operations_portal:conversation_list"))
        self.assertContains(response, reverse("operations_portal:lead_list"))
        self.assertContains(response, reverse("operations_portal:placeholder", kwargs={"section": "integracoes"}))
        self.assertContains(response, reverse("admin:index"))


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalAnalyticsTests(PortalUserMixin, TestCase):
    def setUp(self):
        self.login_superuser()
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")

    def make_dt(self, days_ago=0):
        return timezone.now() - timedelta(days=days_ago)

    def set_created_at(self, instance, value):
        instance.__class__.objects.filter(pk=instance.pk).update(created_at=value, updated_at=value)
        instance.refresh_from_db()
        return instance

    def test_dashboard_uses_default_period_30(self):
        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"]["days"], 30)
        self.assertContains(response, "Últimos 30 dias")

    def test_dashboard_accepts_valid_periods(self):
        for period in (7, 30, 90):
            with self.subTest(period=period):
                response = self.client.get(reverse("operations_portal:dashboard"), {"period": period})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["period"]["days"], period)

    def test_dashboard_invalid_period_falls_back_to_30(self):
        response = self.client.get(reverse("operations_portal:dashboard"), {"period": "365"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"]["days"], 30)

    def test_analytics_fills_missing_days_and_groups_conversations(self):
        today = self.set_created_at(
            Conversation.objects.create(tenant=self.tenant, session_id="today-1"), self.make_dt(0)
        )
        self.set_created_at(Conversation.objects.create(tenant=self.tenant, session_id="today-2"), today.created_at)
        self.set_created_at(Conversation.objects.create(tenant=self.tenant, session_id="old"), self.make_dt(20))

        analytics = get_dashboard_analytics(7)
        chart = analytics["charts"]["conversations_by_day"]

        self.assertEqual(len(chart["labels"]), 7)
        self.assertEqual(sum(chart["series"][0]["data"]), 2)
        self.assertIn(0, chart["series"][0]["data"])

    def test_analytics_groups_leads_created_and_sent_by_sent_date(self):
        created = self.set_created_at(
            LeadDraft.objects.create(tenant=self.tenant, status=LeadDraft.Status.QUALIFIED), self.make_dt(1)
        )
        sent = self.set_created_at(
            LeadDraft.objects.create(
                tenant=self.tenant,
                status=LeadDraft.Status.SENT_TO_CRM,
                sent_to_crm_at=self.make_dt(0),
                crm_external_id="crm-1",
            ),
            self.make_dt(2),
        )
        LeadDraft.objects.filter(pk=sent.pk).update(sent_to_crm_at=self.make_dt(0))

        analytics = get_dashboard_analytics(7)
        leads_chart = analytics["charts"]["leads_by_day"]

        self.assertEqual(sum(leads_chart["series"][0]["data"]), 2)
        self.assertEqual(sum(leads_chart["series"][1]["data"]), 1)
        self.assertEqual(analytics["kpis"]["period_leads_created"], 2)
        self.assertEqual(analytics["kpis"]["period_leads_qualified"], 1)
        self.assertEqual(created.status, LeadDraft.Status.QUALIFIED)

    def test_analytics_builds_mutually_exclusive_funnel(self):
        for status in (
            LeadDraft.Status.DRAFT,
            LeadDraft.Status.QUALIFIED,
            LeadDraft.Status.SENT_TO_CRM,
            LeadDraft.Status.FAILED,
        ):
            self.set_created_at(LeadDraft.objects.create(tenant=self.tenant, status=status), self.make_dt(0))

        funnel = get_dashboard_analytics(7)["charts"]["funnel"]

        self.assertEqual(funnel["series"], [1, 1, 1, 1])
        self.assertIn("categorias mutuamente exclusivas", funnel["summary"])

    def test_analytics_translates_conversation_states(self):
        self.set_created_at(
            Conversation.objects.create(
                tenant=self.tenant,
                session_id="state-1",
                lead_state=Conversation.LeadState.COLLECT_NAME_COMPANY,
            ),
            self.make_dt(0),
        )

        states = get_dashboard_analytics(7)["charts"]["conversation_states"]

        self.assertEqual(states["labels"], ["Nome e empresa"])

    def test_analytics_limits_top_tenants_to_10(self):
        for index in range(11):
            tenant = Tenant.objects.create(name=f"Tenant {index:02d}", slug=f"tenant-{index:02d}")
            for item in range(index + 1):
                self.set_created_at(
                    Conversation.objects.create(tenant=tenant, session_id=f"tenant-{index}-{item}"),
                    self.make_dt(0),
                )

        tenant_volume = get_dashboard_analytics(7)["charts"]["tenant_volume"]

        self.assertEqual(len(tenant_volume["items"]), 10)
        self.assertTrue(tenant_volume["has_more"])
        self.assertEqual(tenant_volume["items"][0]["label"], "Tenant 10")

    def test_analytics_rates_are_zero_without_leads(self):
        analytics = get_dashboard_analytics(7)

        self.assertEqual(analytics["kpis"]["qualification_rate"], 0)
        self.assertEqual(analytics["kpis"]["crm_send_rate"], 0)
        self.assertFalse(analytics["charts"]["leads_by_day"]["has_data"])

    def test_dashboard_datasets_do_not_expose_personal_data_and_use_json_script(self):
        lead = LeadDraft.objects.create(
            tenant=self.tenant,
            status=LeadDraft.Status.QUALIFIED,
            name="Lead Seguro",
            email="cliente@example.com",
            phone="11988887777",
        )
        self.set_created_at(lead, self.make_dt(0))

        response = self.client.get(reverse("operations_portal:dashboard"), {"period": 7})
        payload = response.context["dashboard_charts"]
        serialized = json.dumps(payload, ensure_ascii=False)
        content = response.content.decode()

        self.assertContains(response, 'id="dashboard-analytics-data"')
        self.assertNotIn("cliente@example.com", serialized)
        self.assertNotIn("11988887777", serialized)
        self.assertNotIn("cliente@example.com", content)
        self.assertNotIn("11988887777", content)

    def test_dashboard_json_script_escapes_labels_and_empty_states_render(self):
        tenant = Tenant.objects.create(name="<script>alert(1)</script>", slug="tenant-script")
        self.set_created_at(Conversation.objects.create(tenant=tenant, session_id="safe-json"), self.make_dt(0))

        response = self.client.get(reverse("operations_portal:dashboard"), {"period": 7})
        content = response.content.decode()

        self.assertIn("dashboard-analytics-data", content)
        self.assertNotIn("<script>alert(1)</script>", content)
        self.assertIn(r"\u003Cscript\u003Ealert(1)\u003C/script\u003E", content)

    def test_dashboard_empty_states_render_without_activity(self):
        response = self.client.get(reverse("operations_portal:dashboard"), {"period": 7})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ainda não há dados neste período.", count=5)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalConversationTests(PortalUserMixin, TestCase):
    def setUp(self):
        self.login_superuser()
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")
        self.other_tenant = Tenant.objects.create(name="Caneca de Garagem", slug="caneca-de-garagem")
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="scb-qualified",
            visitor_name="Visitante Alpha",
            visitor_email="alpha@example.com",
            visitor_phone="11999998888",
            source_page="https://example.com/origem",
            is_qualified=True,
            lead_state=Conversation.LeadState.COLLECT_CONTACT,
        )
        self.lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=LeadDraft.Status.FAILED,
            name="Visitante Alpha",
            email="alpha@example.com",
            phone="11999998888",
            need_summary="Precisa acompanhar frotas M2M.",
            crm_error="timeout",
        )
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            lead_draft=self.lead,
            priority=HandoffRequest.Priority.URGENT,
        )
        Message.objects.create(conversation=self.conversation, role=Message.Role.USER, content="<script>alert(1)</script>")
        Message.objects.create(conversation=self.conversation, role=Message.Role.ASSISTANT, content="Vou ajudar com M2M.")
        self.other_conversation = Conversation.objects.create(
            tenant=self.other_tenant,
            session_id="garage-discovery",
            is_qualified=False,
            lead_state=Conversation.LeadState.DISCOVERY,
        )

    def test_conversation_list_filters_and_translates_states(self):
        response = self.client.get(
            reverse("operations_portal:conversation_list"),
            {"tenant": self.tenant.pk, "lead_state": Conversation.LeadState.COLLECT_CONTACT, "qualified": "yes", "q": "scb"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "scb-qualified")
        self.assertContains(response, "Contato")
        self.assertContains(response, "Urgente")
        self.assertContains(response, reverse("operations_portal:lead_detail", kwargs={"pk": self.lead.pk}))
        self.assertNotContains(response, "garage-discovery")

    def test_conversation_list_is_paginated_and_keeps_filters(self):
        for index in range(13):
            Conversation.objects.create(tenant=self.tenant, session_id=f"page-session-{index}")

        response = self.client.get(reverse("operations_portal:conversation_list"), {"q": "page-session"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 de 2")
        self.assertContains(response, "?q=page-session&page=2")

    def test_conversation_detail_links_related_records_and_escapes_messages(self):
        response = self.client.get(reverse("operations_portal:conversation_detail", kwargs={"pk": self.conversation.pk}))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "scb-qualified")
        self.assertContains(response, "Contato")
        self.assertContains(response, reverse("operations_portal:lead_detail", kwargs={"pk": self.lead.pk}))
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotIn("<script>alert(1)</script>", content)

    def test_conversation_list_uses_bounded_queries(self):
        for index in range(5):
            conversation = Conversation.objects.create(tenant=self.tenant, session_id=f"query-session-{index}")
            Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Oi")

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("operations_portal:conversation_list"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 8)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationsPortalLeadTests(PortalUserMixin, TestCase):
    def setUp(self):
        self.login_superuser()
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")
        self.other_tenant = Tenant.objects.create(name="Granimarmores Pitondo", slug="granimarmores-pitondo")
        self.conversation = Conversation.objects.create(tenant=self.tenant, session_id="lead-session")
        self.failed_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=LeadDraft.Status.FAILED,
            name="Carla Souza",
            company="Empresa Alpha",
            email="carla.souza@example.com",
            phone="11987654321",
            city="Sao Paulo",
            need_summary="Quer integrar atendimento e CRM sem perder contexto.",
            crm_error="timeout",
        )
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            lead_draft=self.failed_lead,
            priority=HandoffRequest.Priority.HIGH,
        )
        self.sent_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            status=LeadDraft.Status.SENT_TO_CRM,
            name="Pedro Lima",
            email="pedro@example.com",
            phone="1133334444",
            crm_external_id="smart360-external-1234567890",
            sent_to_crm_at=timezone.now(),
        )
        self.dry_run_lead = LeadDraft.objects.create(
            tenant=self.tenant,
            status=LeadDraft.Status.SENT_TO_CRM,
            name="Lead Dry Run",
            crm_external_id="dry-run-smart-control-brasil-lead-session",
            sent_to_crm_at=timezone.now(),
        )
        self.other_lead = LeadDraft.objects.create(
            tenant=self.other_tenant,
            status=LeadDraft.Status.QUALIFIED,
            name="Outro Lead",
            company="Outra Empresa",
        )

    def test_lead_list_filters_status_crm_failure_and_masks_contact(self):
        response = self.client.get(
            reverse("operations_portal:lead_list"),
            {
                "tenant": self.tenant.pk,
                "status": LeadDraft.Status.FAILED,
                "crm_sent": "no",
                "dispatch_failed": "yes",
                "q": "Alpha",
            },
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Carla Souza")
        self.assertContains(response, "Falha")
        self.assertContains(response, "Falha")
        self.assertContains(response, "ca***@example.com")
        self.assertContains(response, "***4321")
        self.assertNotIn("carla.souza@example.com", content)
        self.assertNotIn("11987654321", content)
        self.assertNotContains(response, "Pedro Lima")
        self.assertNotContains(response, "Outro Lead")

    def test_lead_list_shows_sent_crm_state_and_compact_external_id(self):
        response = self.client.get(reverse("operations_portal:lead_list"), {"crm_sent": "yes"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pedro Lima")
        self.assertContains(response, "Enviado")
        self.assertContains(response, "smart360...")
        self.assertNotContains(response, "smart360-external-1234567890")

    def test_lead_detail_shows_full_operational_context_and_links(self):
        response = self.client.get(reverse("operations_portal:lead_detail", kwargs={"pk": self.failed_lead.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "carla.souza@example.com")
        self.assertContains(response, "11987654321")
        self.assertContains(response, "timeout")
        self.assertContains(response, "Reprocessar envio ao CRM")
        self.assertContains(response, reverse("operations_portal:conversation_detail", kwargs={"pk": self.conversation.pk}))
        self.assertContains(response, "Alta")

    def test_lead_detail_identifies_dry_run_crm_dispatch(self):
        response = self.client.get(reverse("operations_portal:lead_detail", kwargs={"pk": self.dry_run_lead.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dry-run")
        self.assertNotContains(response, "Reprocessar envio ao CRM")

    def test_retry_crm_dispatch_requires_post_and_csrf(self):
        url = reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.failed_lead.pk})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_retry_crm_dispatch_calls_service_for_failed_unsent_lead(self):
        result = CRMDispatchResult(
            attempted=True,
            success=True,
            dry_run=True,
            lead_draft=self.failed_lead,
            external_id="dry-run-id",
            message="ok",
        )
        service = Mock()
        service.dispatch_if_qualified.return_value = result

        with patch("operations_portal.views.CRMDispatchService", return_value=service):
            response = self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.failed_lead.pk}))

        self.assertRedirects(response, reverse("operations_portal:lead_detail", kwargs={"pk": self.failed_lead.pk}))
        service.dispatch_if_qualified.assert_called_once()
        self.failed_lead.refresh_from_db()
        self.assertEqual(self.failed_lead.status, LeadDraft.Status.QUALIFIED)
        self.assertEqual(self.failed_lead.crm_error, "")

    def test_retry_crm_dispatch_does_not_call_service_for_already_sent_lead(self):
        service = Mock()

        with patch("operations_portal.views.CRMDispatchService", return_value=service):
            response = self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.sent_lead.pk}))

        self.assertRedirects(response, reverse("operations_portal:lead_detail", kwargs={"pk": self.sent_lead.pk}))
        service.dispatch_if_qualified.assert_not_called()
        self.sent_lead.refresh_from_db()
        self.assertEqual(self.sent_lead.status, LeadDraft.Status.SENT_TO_CRM)

    def test_lead_list_uses_bounded_queries(self):
        for index in range(5):
            LeadDraft.objects.create(tenant=self.tenant, status=LeadDraft.Status.QUALIFIED, name=f"Lead {index}")

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("operations_portal:lead_list"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 8)


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
