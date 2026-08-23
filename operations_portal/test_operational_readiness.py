import json
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from conversations.models import ChatRequest, Conversation, HandoffRequest
from integrations.models import OutboxEvent
from knowledge_base.models import KnowledgeDocument, TenantOperationalAlert, TenantOperationalMaintenanceWindow
from leads.models import LeadDraft
from operations_portal.operational_readiness import (
    STATUS_DEGRADED,
    STATUS_MAINTENANCE,
    STATUS_NOT_READY,
    STATUS_READY,
    STATUS_WARNING,
    TenantOperationalReadinessService,
)
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership

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
)
class TenantOperationalReadinessTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.service = TenantOperationalReadinessService(now=self.now)
        self.tenant = self._tenant("tenant-a")
        self.other_tenant = self._tenant("tenant-b")

    def _tenant(self, slug):
        tenant = Tenant.objects.create(name=slug.title(), slug=slug, domain=f"https://{slug}.example")
        AssistantProfile.objects.create(
            tenant=tenant,
            name="Lívia",
            is_active=True,
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511999999999",
        )
        TenantAllowedOrigin.objects.create(tenant=tenant, origin=f"https://{slug}.example", is_active=True)
        KnowledgeDocument.objects.create(tenant=tenant, title="FAQ", slug="faq", status=KnowledgeDocument.Status.ACTIVE)
        return tenant

    def _outbox_event(self, tenant, *, status=OutboxEvent.Status.PENDING, created_at=None):
        event = OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=tenant,
            event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
            aggregate_type="LeadDraft",
            aggregate_id=str(uuid.uuid4()),
            deduplication_key=str(uuid.uuid4()),
            payload={"safe": True},
            status=status,
            available_at=self.now,
        )
        if created_at:
            OutboxEvent.objects.filter(pk=event.pk).update(created_at=created_at)
            event.refresh_from_db()
        return event

    def test_ready_when_all_components_are_healthy_or_policy_disabled(self):
        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_READY)
        self.assertEqual(status.site.status, STATUS_READY)
        self.assertEqual(status.knowledge.status, STATUS_READY)
        self.assertEqual(status.commercial.status, STATUS_READY)
        self.assertEqual(status.integrations.status, STATUS_READY)
        self.assertEqual(status.outbox.status, STATUS_READY)
        self.assertFalse(status.integrations.details["ai_allowed"])

    def test_warning_for_outbox_backlog(self):
        old = self.now - timedelta(minutes=30)
        self._outbox_event(self.tenant, status=OutboxEvent.Status.PENDING, created_at=old)

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_WARNING)
        self.assertEqual(status.outbox.status, STATUS_WARNING)
        self.assertIn("outbox_backlog", {issue.code for issue in status.incidents})

    def test_degraded_for_outbox_failure_and_dispatch_failure(self):
        self._outbox_event(self.tenant, status=OutboxEvent.Status.DEAD_LETTER)
        LeadDraft.objects.create(tenant=self.tenant, dispatch_status=LeadDraft.DispatchStatus.FAILED)

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_DEGRADED)
        self.assertEqual(status.outbox.status, STATUS_DEGRADED)
        self.assertEqual(status.commercial.status, STATUS_DEGRADED)

    def test_not_ready_when_site_or_knowledge_is_critically_not_ready(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active", "updated_at"])

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_NOT_READY)
        self.assertEqual(status.site.status, STATUS_NOT_READY)

    def test_maintenance_overrides_other_statuses(self):
        TenantOperationalMaintenanceWindow.objects.create(
            tenant=self.tenant,
            title="Janela ativa",
            description="Manutenção programada",
            starts_at=self.now - timedelta(minutes=5),
            ends_at=self.now + timedelta(minutes=30),
            status=TenantOperationalMaintenanceWindow.Status.ACTIVE,
            scope=TenantOperationalMaintenanceWindow.Scope.ALL,
        )

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_MAINTENANCE)
        self.assertEqual(len(status.maintenance_windows), 1)

    def test_chat_stuck_failed_knowledge_stale_and_pending_handoff_are_reported(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="s1")
        request = ChatRequest.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            session_id="s1",
            request_id=uuid.uuid4(),
            request_fingerprint="abc",
            status=ChatRequest.Status.PROCESSING,
        )
        ChatRequest.objects.filter(pk=request.pk).update(updated_at=self.now - timedelta(minutes=20))
        doc = KnowledgeDocument.objects.get(tenant=self.tenant, slug="faq")
        doc.content = "novo conteúdo"
        doc.content_sha256 = "a" * 64
        doc.lifecycle_status = KnowledgeDocument.LifecycleStatus.STALE
        doc.save(update_fields=["content", "content_sha256", "lifecycle_status", "updated_at"])
        handoff = HandoffRequest.objects.create(tenant=self.tenant, conversation=conversation, status=HandoffRequest.Status.PENDING)
        HandoffRequest.objects.filter(pk=handoff.pk).update(created_at=self.now - timedelta(days=2))

        status = self.service.for_tenant(self.tenant)
        codes = {issue.code for issue in status.incidents}

        self.assertEqual(status.status, STATUS_NOT_READY)
        self.assertIn("chat_request_stuck", codes)
        self.assertIn("knowledge_documents_stale", codes)
        self.assertIn("handoff_pending_old", codes)

    def test_tenant_isolation_for_operational_counters(self):
        self._outbox_event(self.other_tenant, status=OutboxEvent.Status.DEAD_LETTER)

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_READY)
        self.assertEqual(status.outbox.details["counts"][OutboxEvent.Status.DEAD_LETTER], 0)

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="", LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True)
    def test_real_integration_missing_config_degrades_without_leaking_secret(self):
        status = TenantOperationalReadinessService(now=self.now).for_tenant(self.tenant)
        payload = json.dumps(status.as_dict())

        self.assertEqual(status.integrations.status, STATUS_DEGRADED)
        self.assertIn("openai_chat_missing_api_key", payload)
        self.assertNotIn("sk-", payload)

    def test_open_operational_alerts_contribute_to_incidents(self):
        TenantOperationalAlert.objects.create(
            tenant=self.tenant,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_warning",
            fingerprint="tenant-a:rag_warning",
            title="RAG warning",
            summary="Há sinal operacional aberto.",
            detected_at=self.now,
            last_seen_at=self.now,
        )

        status = self.service.for_tenant(self.tenant)

        self.assertEqual(status.status, STATUS_WARNING)
        self.assertIn("operational_alerts_open", {issue.code for issue in status.incidents})


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class TenantOperationalReadinessPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ops", password="pass")
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a", domain="https://tenant-a.example")
        self.other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b", domain="https://tenant-b.example")
        AssistantProfile.objects.create(
            tenant=self.tenant,
            is_active=True,
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511999999999",
        )
        AssistantProfile.objects.create(tenant=self.other_tenant)
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://tenant-a.example", is_active=True)
        KnowledgeDocument.objects.create(tenant=self.tenant, title="FAQ", slug="faq", status=KnowledgeDocument.Status.ACTIVE)
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role=TenantMembership.Role.VIEWER)
        self.client.force_login(self.user)

    def test_tenant_list_shows_operational_summary_with_rbac_scope(self):
        response = self.client.get(reverse("operations_portal:tenant_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operação")
        self.assertContains(response, "tenant-a")
        self.assertNotContains(response, "tenant-b")

    def test_tenant_detail_shows_read_only_operational_diagnostic(self):
        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operação")
        self.assertContains(response, "Status geral")
        self.assertContains(response, "Outbox")
        self.assertNotContains(response, "Salvar geral")


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class TenantOperationalReadinessCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a", domain="https://tenant-a.example")
        AssistantProfile.objects.create(
            tenant=self.tenant,
            is_active=True,
            is_widget_enabled=True,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="5511999999999",
        )
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://tenant-a.example", is_active=True)
        KnowledgeDocument.objects.create(tenant=self.tenant, title="FAQ", slug="faq", status=KnowledgeDocument.Status.ACTIVE)

    def test_command_outputs_text_and_json(self):
        out = StringIO()
        call_command("tenant_operational_status", "--tenant", "tenant-a", stdout=out)
        self.assertIn("tenant-a:", out.getvalue())

        out = StringIO()
        call_command("tenant_operational_status", "--all", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload[0]["tenant"], "tenant-a")
        self.assertIn("status", payload[0])
        self.assertNotIn("token", out.getvalue().lower())
