import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import ACTION_OUTBOX_REQUEUED, AuditEvent
from integrations.models import OutboxEvent, TenantWebhookConfig, WebhookDeliveryLog
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_WEBHOOKS_ENABLED=True,
    LIVIA_WEBHOOKS_DRY_RUN=True,
    SMART360_LEAD_DISPATCH_ENABLED=False,
    SMART360_LEAD_DISPATCH_DRY_RUN=False,
    LIVIA_AI_ENABLED=False,
    LIVIA_RAG_ENABLED=False,
    LIVIA_RAG_OPERATIONS_ENABLED=False,
    LIVIA_RAG_INDEXING_ENABLED=False,
)
class OperationsPortalIntegrationsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="integrations-admin", password="pass")
        self.manager = get_user_model().objects.create_user(username="integrations-manager", password="pass")
        self.outsider = get_user_model().objects.create_user(username="integrations-outsider", password="pass")
        self.tenant = Tenant.objects.create(name="Tenant Integrations A", slug="tenant-integrations-a", domain="https://a.example")
        self.other_tenant = Tenant.objects.create(name="Tenant Integrations B", slug="tenant-integrations-b", domain="https://b.example")
        AssistantProfile.objects.create(tenant=self.tenant)
        AssistantProfile.objects.create(tenant=self.other_tenant)
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://a.example")
        TenantMembership.objects.create(tenant=self.tenant, user=self.admin, role=TenantMembership.Role.TENANT_ADMIN)
        TenantMembership.objects.create(tenant=self.tenant, user=self.manager, role=TenantMembership.Role.MANAGER)
        self.webhook = TenantWebhookConfig.objects.create(
            tenant=self.tenant,
            name="Webhook A",
            event_type=TenantWebhookConfig.EventType.ALL,
            target_url="https://hooks.example/a",
            secret_token="super-secret-token",
            is_active=True,
            dry_run=True,
        )
        self.event = self.create_outbox_event(
            tenant=self.tenant,
            status=OutboxEvent.Status.DEAD_LETTER,
            event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
            payload={"data": {"name": "Lead A", "api_key": "sk-secret"}, "secret_token": "hidden"},
            last_error_message="timeout without sensitive data",
        )
        self.other_event = self.create_outbox_event(
            tenant=self.other_tenant,
            status=OutboxEvent.Status.DEAD_LETTER,
            event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
            last_error_message="other tenant failure",
        )
        WebhookDeliveryLog.objects.create(
            tenant=self.tenant,
            webhook_config=self.webhook,
            event_type=TenantWebhookConfig.EventType.LEAD_QUALIFIED,
            status=WebhookDeliveryLog.Status.DRY_RUN,
            payload_preview={"secret_token": "should-not-render"},
        )

    def create_outbox_event(self, *, tenant, status, event_type, payload=None, last_error_message=""):
        return OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=tenant,
            event_type=event_type,
            aggregate_type="LeadDraft",
            aggregate_id="1",
            deduplication_key=f"{tenant.pk}:{event_type}:{uuid.uuid4()}",
            payload=payload or {"data": {"ok": True}},
            status=status,
            attempts=2,
            max_attempts=3,
            available_at=timezone.now(),
            last_attempt_at=timezone.now(),
            last_error_code="timeout" if last_error_message else "",
            last_error_message=last_error_message,
        )

    def test_authorized_user_reads_status_readiness_and_outbox_without_secrets(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:integrations"), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OPENAI_CHAT")
        self.assertContains(response, "SMART360_LEAD_DISPATCH")
        self.assertContains(response, "WEBHOOK_DELIVERY")
        self.assertContains(response, "BLOCKED")
        self.assertContains(response, "DRY_RUN")
        self.assertContains(response, "Readiness consolidado")
        self.assertContains(response, "Outbox")
        self.assertContains(response, "timeout without sensitive data")
        self.assertContains(response, "Webhook A")
        self.assertContains(response, "https://hooks.example/a")
        self.assertNotContains(response, "super-secret-token")
        self.assertNotContains(response, "should-not-render")
        self.assertNotContains(response, "sk-secret")
        self.assertNotContains(response, "other tenant failure")

    def test_access_denied_without_membership_or_wrong_tenant(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("operations_portal:integrations"), {"tenant": self.tenant.pk}).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("operations_portal:integrations"), {"tenant": self.other_tenant.pk}).status_code, 403)

    def test_outbox_detail_redacts_payload_and_is_tenant_scoped(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:outbox_event_detail", kwargs={"pk": self.event.pk}), {"tenant": self.tenant.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "[redacted]")
        self.assertContains(response, "Lead A")
        self.assertNotContains(response, "sk-secret")
        self.assertEqual(
            self.client.get(reverse("operations_portal:outbox_event_detail", kwargs={"pk": self.other_event.pk}), {"tenant": self.tenant.pk}).status_code,
            404,
        )

    def test_manager_can_read_but_cannot_requeue(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse("operations_portal:integrations"), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("operations_portal:outbox_event_requeue", kwargs={"pk": self.event.pk}), {"tenant": self.tenant.pk})
        self.assertEqual(response.status_code, 403)

    def test_requeue_is_post_csrf_tenant_scoped_and_audited_without_external_side_effects(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(reverse("operations_portal:outbox_event_requeue", kwargs={"pk": self.event.pk}), {"tenant": self.tenant.pk}).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.post(reverse("operations_portal:outbox_event_requeue", kwargs={"pk": self.event.pk}), {"tenant": self.tenant.pk})

        self.assertRedirects(response, reverse("operations_portal:outbox_event_detail", kwargs={"pk": self.event.pk}))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(self.event.locked_by, "")
        self.assertEqual(self.event.last_error_code, "manual_requeue_from_portal")
        audit = AuditEvent.objects.get(action=ACTION_OUTBOX_REQUEUED)
        self.assertEqual(audit.tenant, self.tenant)
        self.assertEqual(audit.metadata["source"], "operations_portal.integrations")

    def test_requeue_rejects_ineligible_status(self):
        event = self.create_outbox_event(
            tenant=self.tenant,
            status=OutboxEvent.Status.SUCCEEDED,
            event_type=OutboxEvent.EventType.HANDOFF_CREATED,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("operations_portal:outbox_event_requeue", kwargs={"pk": event.pk}), {"tenant": self.tenant.pk})

        self.assertRedirects(response, reverse("operations_portal:outbox_event_detail", kwargs={"pk": event.pk}))
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.Status.SUCCEEDED)
        self.assertFalse(AuditEvent.objects.filter(action=ACTION_OUTBOX_REQUEUED, object_id=str(event.pk)).exists())

    def test_tenant_detail_links_to_integrations(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("operations_portal:tenant_detail", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("operations_portal:integrations") + f"?tenant={self.tenant.pk}")
