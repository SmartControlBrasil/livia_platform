from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from audit.admin import AuditEventAdmin
from audit.models import (
    ACTION_ASSISTANT_PROFILE_UPDATED,
    ACTION_HANDOFF_STATUS_CHANGED,
    ACTION_LEAD_CRM_DISPATCH_RETRIED,
    ACTION_TENANT_CREATED,
    ACTION_TENANT_UPDATED,
    ACTION_WEBHOOK_CONFIG_CREATED,
    AuditEvent,
)
from audit.services import MASKED_VALUE, SERIALIZATION_ERROR_VALUE, extract_ip_address, record_audit_event
from conversations.models import Conversation, HandoffRequest
from integrations.models import OutboxEvent
from integrations.models import TenantWebhookConfig
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class AuditPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="auditor", password="pass", email="auditor@example.com"
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")
        self.conversation = Conversation.objects.create(tenant=self.tenant, session_id="audit-session")

    def test_handoff_status_change_records_actor_tenant_before_after_and_ip(self):
        handoff = HandoffRequest.objects.create(tenant=self.tenant, conversation=self.conversation)

        response = self.client.post(
            reverse("operations_portal:handoff_update_status", kwargs={"pk": handoff.pk}),
            {"status": HandoffRequest.Status.SENT},
            HTTP_X_FORWARDED_FOR="not-an-ip, 203.0.113.10",
        )

        self.assertRedirects(response, reverse("operations_portal:handoff_detail", kwargs={"pk": handoff.pk}))
        event = AuditEvent.objects.get(action=ACTION_HANDOFF_STATUS_CHANGED)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.tenant, self.tenant)
        self.assertEqual(event.before_data["status"], HandoffRequest.Status.PENDING)
        self.assertEqual(event.after_data["status"], HandoffRequest.Status.SENT)
        self.assertEqual(event.ip_address, "203.0.113.10")

    def test_crm_retry_records_attempt_without_dispatching_real_integration(self):
        lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=LeadDraft.Status.FAILED,
            crm_error="erro temporário",
        )
        response = self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": lead.pk}))

        self.assertRedirects(response, reverse("operations_portal:lead_detail", kwargs={"pk": lead.pk}))
        outbox_event = OutboxEvent.objects.get(event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_id=str(lead.pk))
        event = AuditEvent.objects.get(action=ACTION_LEAD_CRM_DISPATCH_RETRIED)
        self.assertEqual(event.before_data["status"], LeadDraft.Status.FAILED)
        self.assertEqual(event.after_data["status"], LeadDraft.Status.QUALIFIED)
        self.assertEqual(event.metadata["outbox_event_id"], str(outbox_event.event_id))

    def test_assistant_profile_settings_records_only_changed_fields(self):
        profile = AssistantProfile.objects.create(
            tenant=self.tenant,
            human_handoff_enabled=False,
            handoff_whatsapp_label="Falar com um especialista",
        )

        response = self.client.post(
            reverse("operations_portal:settings"),
            {
                "tenant": self.tenant.pk,
                "action": "save_handoff",
                "handoff-human_handoff_enabled": "on",
                "handoff-human_handoff_channel": "whatsapp",
                "handoff-handoff_whatsapp_number": "+55 (11) 99999-8888",
                "handoff-handoff_whatsapp_label": "Falar com um especialista",
                "handoff-handoff_whatsapp_message": profile.handoff_whatsapp_message,
            },
        )

        self.assertRedirects(response, f"{reverse('operations_portal:settings')}?tenant={self.tenant.pk}")
        event = AuditEvent.objects.get(action=ACTION_ASSISTANT_PROFILE_UPDATED)
        self.assertEqual(set(event.before_data), {"human_handoff_enabled", "human_handoff_channel", "handoff_whatsapp_number"})
        self.assertEqual(event.after_data["handoff_whatsapp_number"], "5511999998888")

    def test_get_requests_do_not_generate_change_audit(self):
        handoff = HandoffRequest.objects.create(tenant=self.tenant, conversation=self.conversation)

        self.client.get(reverse("operations_portal:handoff_detail", kwargs={"pk": handoff.pk}))
        self.client.get(reverse("operations_portal:settings"), {"tenant": self.tenant.pk})

        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_invalid_operation_and_invalid_form_do_not_generate_event(self):
        handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=HandoffRequest.Status.RESOLVED,
        )
        self.client.post(
            reverse("operations_portal:handoff_update_status", kwargs={"pk": handoff.pk}),
            {"status": HandoffRequest.Status.SENT},
        )
        self.client.post(
            reverse("operations_portal:settings"),
            {
                "tenant": self.tenant.pk,
                "human_handoff_enabled": "on",
                "human_handoff_channel": "whatsapp",
                "handoff_whatsapp_number": "123",
                "handoff_whatsapp_label": "Falar com um especialista",
                "handoff_whatsapp_message": "Olá",
            },
        )

        self.assertEqual(AuditEvent.objects.count(), 0)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class AuditAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="pass", email="admin@example.com"
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(name="Tenant Base", slug="tenant-base")

    def test_tenant_admin_create_and_update_generate_events(self):
        add_response = self.client.post(
            reverse("admin:tenants_tenant_add"),
            {"name": "Tenant Admin", "slug": "tenant-admin", "domain": "example.com", "is_active": "on"},
        )
        self.assertEqual(add_response.status_code, 302)
        created = Tenant.objects.get(slug="tenant-admin")
        create_event = AuditEvent.objects.get(action=ACTION_TENANT_CREATED, object_id=str(created.pk))
        self.assertEqual(create_event.after_data["slug"], "tenant-admin")

        change_response = self.client.post(
            reverse("admin:tenants_tenant_change", kwargs={"object_id": created.pk}),
            {"name": "Tenant Admin", "slug": "tenant-admin", "domain": "new.example.com", "is_active": "on"},
        )
        self.assertEqual(change_response.status_code, 302)
        update_event = AuditEvent.objects.get(action=ACTION_TENANT_UPDATED, object_id=str(created.pk))
        self.assertEqual(update_event.before_data, {"domain": "example.com"})
        self.assertEqual(update_event.after_data, {"domain": "new.example.com"})

    def test_webhook_admin_create_masks_secret_token(self):
        response = self.client.post(
            reverse("admin:integrations_tenantwebhookconfig_add"),
            {
                "tenant": self.tenant.pk,
                "name": "CRM",
                "event_type": TenantWebhookConfig.EventType.ALL,
                "target_url": "https://example.com/webhook",
                "secret_token": "super-secret-token",
                "is_active": "on",
                "dry_run": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.get(action=ACTION_WEBHOOK_CONFIG_CREATED)
        self.assertEqual(event.after_data["secret_token"], MASKED_VALUE)

    def test_invalid_admin_form_does_not_generate_event(self):
        response = self.client.post(
            reverse("admin:integrations_tenantwebhookconfig_add"),
            {
                "tenant": self.tenant.pk,
                "name": "CRM inválido",
                "event_type": TenantWebhookConfig.EventType.ALL,
                "target_url": "ftp://example.com/webhook",
                "secret_token": "super-secret-token",
                "is_active": "on",
                "dry_run": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_audit_event_admin_is_view_only(self):
        modeladmin = AuditEventAdmin(AuditEvent, admin.site)
        request = RequestFactory().get("/admin/audit/auditevent/")
        request.user = self.user

        self.assertFalse(modeladmin.has_add_permission(request))
        self.assertFalse(modeladmin.has_change_permission(request))
        self.assertFalse(modeladmin.has_delete_permission(request))
        self.assertTrue(modeladmin.has_view_permission(request))


class AuditServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil")

    def test_sensitive_data_is_masked(self):
        event = record_audit_event(
            action=ACTION_TENANT_UPDATED,
            tenant=self.tenant,
            obj=self.tenant,
            before_data={"password": "123", "authorization": "Bearer abc", "name": "Old"},
            after_data={"api_key": "secret", "transcript": "mensagem completa", "name": "New"},
            metadata={"secret_token": "webhook-secret"},
        )

        self.assertEqual(event.before_data["password"], MASKED_VALUE)
        self.assertEqual(event.before_data["authorization"], MASKED_VALUE)
        self.assertEqual(event.after_data["api_key"], MASKED_VALUE)
        self.assertEqual(event.after_data["transcript"], MASKED_VALUE)
        self.assertEqual(event.metadata["secret_token"], MASKED_VALUE)

    def test_anonymous_or_system_action_can_generate_event_without_actor(self):
        event = record_audit_event(
            action=ACTION_TENANT_UPDATED,
            actor=AnonymousUser(),
            tenant=self.tenant,
            obj=self.tenant,
            before_data={"name": "Old"},
            after_data={"name": "New"},
        )

        self.assertIsNone(event.actor)

    def test_ip_is_extracted_safely(self):
        request = RequestFactory().post(
            "/",
            REMOTE_ADDR="198.51.100.20",
            HTTP_X_FORWARDED_FOR="bad-ip, 2001:db8::1",
        )

        self.assertEqual(extract_ip_address(request), "2001:db8::1")

    def test_serialization_failure_does_not_break_audit_record(self):
        event = record_audit_event(
            action=ACTION_TENANT_UPDATED,
            tenant=self.tenant,
            obj=self.tenant,
            before_data={"value": object()},
            after_data={"name": "New"},
        )

        self.assertEqual(event.before_data["value"], SERIALIZATION_ERROR_VALUE)
