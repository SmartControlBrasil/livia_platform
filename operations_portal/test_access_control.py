import uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import (
    ACTION_HANDOFF_STATUS_CHANGED,
    ACTION_LEAD_CRM_DISPATCH_RETRIED,
    ACTION_TENANT_MEMBERSHIP_CREATED,
    ACTION_TENANT_MEMBERSHIP_DEACTIVATED,
    ACTION_TENANT_MEMBERSHIP_UPDATED,
    AuditEvent,
)
from conversations.models import Conversation, HandoffRequest
from integrations.models import OutboxEvent
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant, TenantMembership


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class PortalTenantAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="member", password="pass")
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.conv_a = Conversation.objects.create(tenant=self.tenant_a, session_id="session-a")
        self.conv_b = Conversation.objects.create(tenant=self.tenant_b, session_id="session-b")
        self.lead_a = LeadDraft.objects.create(tenant=self.tenant_a, conversation=self.conv_a, status=LeadDraft.Status.FAILED)
        self.lead_b = LeadDraft.objects.create(tenant=self.tenant_b, conversation=self.conv_b, status=LeadDraft.Status.FAILED)
        self.handoff_a = HandoffRequest.objects.create(tenant=self.tenant_a, conversation=self.conv_a)
        self.handoff_b = HandoffRequest.objects.create(tenant=self.tenant_b, conversation=self.conv_b)

    def login_with_role(self, role, tenant=None, user=None):
        user = user or self.user
        TenantMembership.objects.create(tenant=tenant or self.tenant_a, user=user, role=role)
        self.client.force_login(user)
        return user

    def test_single_tenant_user_accesses_dashboard_automatically(self):
        self.login_with_role(TenantMembership.Role.VIEWER)

        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tenant"], self.tenant_a)
        self.assertContains(response, "session-a")
        self.assertNotContains(response, "session-b")

    def test_multi_tenant_user_can_switch_only_to_authorized_tenant(self):
        self.login_with_role(TenantMembership.Role.VIEWER, tenant=self.tenant_a)
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.user, role=TenantMembership.Role.VIEWER)

        response = self.client.get(reverse("operations_portal:dashboard"), {"tenant": self.tenant_b.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tenant"], self.tenant_b)

        other = Tenant.objects.create(name="Tenant C", slug="tenant-c")
        response = self.client.get(reverse("operations_portal:dashboard"), {"tenant": other.pk})
        self.assertEqual(response.status_code, 403)

    def test_arbitrary_tenant_in_post_or_session_is_rejected(self):
        self.login_with_role(TenantMembership.Role.TENANT_ADMIN, tenant=self.tenant_a)
        session = self.client.session
        session["operations_portal_active_tenant_id"] = str(self.tenant_b.pk)
        session.save()

        self.assertEqual(self.client.get(reverse("operations_portal:dashboard")).status_code, 403)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("operations_portal:settings"),
            {
                "tenant": self.tenant_b.pk,
                "action": "save_handoff",
                "handoff-human_handoff_enabled": "on",
                "handoff-human_handoff_channel": "disabled",
                "handoff-handoff_whatsapp_number": "",
                "handoff-handoff_whatsapp_label": "Falar",
                "handoff-handoff_whatsapp_message": "Olá",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_user_without_membership_and_staff_without_membership_get_403(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("operations_portal:dashboard")).status_code, 403)

        staff = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("operations_portal:dashboard")).status_code, 403)

    def test_superuser_keeps_global_access(self):
        admin = get_user_model().objects.create_superuser(username="admin", password="pass", email="admin@example.com")
        self.client.force_login(admin)

        response = self.client.get(reverse("operations_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["portal_is_global"])
        self.assertContains(response, "session-a")
        self.assertContains(response, "session-b")

    def test_lists_details_and_filters_do_not_cross_tenant_scope(self):
        self.login_with_role(TenantMembership.Role.VIEWER, tenant=self.tenant_a)

        self.assertEqual(self.client.get(reverse("operations_portal:conversation_list"), {"tenant": self.tenant_b.pk}).status_code, 403)
        response = self.client.get(reverse("operations_portal:conversation_list"), {"q": "session"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "session-a")
        self.assertNotContains(response, "session-b")
        self.assertEqual(self.client.get(reverse("operations_portal:conversation_detail", kwargs={"pk": self.conv_b.pk})).status_code, 404)

        self.assertEqual(self.client.get(reverse("operations_portal:lead_list"), {"tenant": self.tenant_b.pk}).status_code, 403)
        response = self.client.get(reverse("operations_portal:lead_list"), {"status": LeadDraft.Status.FAILED})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"#{self.lead_a.pk}")
        self.assertEqual(self.client.get(reverse("operations_portal:lead_detail", kwargs={"pk": self.lead_b.pk})).status_code, 404)

        self.assertEqual(self.client.get(reverse("operations_portal:handoff_list"), {"tenant": self.tenant_b.pk}).status_code, 403)
        response = self.client.get(reverse("operations_portal:handoff_list"), {"status": HandoffRequest.Status.PENDING})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"#{self.handoff_a.pk}")
        self.assertEqual(self.client.get(reverse("operations_portal:handoff_detail", kwargs={"pk": self.handoff_b.pk})).status_code, 404)

    def test_permission_matrix_is_enforced_in_portal_actions(self):
        self.login_with_role(TenantMembership.Role.VIEWER, tenant=self.tenant_a)
        self.assertNotContains(self.client.get(reverse("operations_portal:lead_detail", kwargs={"pk": self.lead_a.pk})), "Reprocessar envio ao CRM")
        self.assertEqual(self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.lead_a.pk})).status_code, 403)
        self.assertEqual(self.client.post(reverse("operations_portal:handoff_update_status", kwargs={"pk": self.handoff_a.pk}), {"status": HandoffRequest.Status.SENT}).status_code, 403)
        self.handoff_a.refresh_from_db()
        self.assertEqual(self.handoff_a.status, HandoffRequest.Status.PENDING)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_operator_changes_handoff_but_cannot_retry_crm(self):
        self.login_with_role(TenantMembership.Role.OPERATOR, tenant=self.tenant_a)

        response = self.client.post(
            reverse("operations_portal:handoff_update_status", kwargs={"pk": self.handoff_a.pk}),
            {"status": HandoffRequest.Status.SENT},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_HANDOFF_STATUS_CHANGED, tenant=self.tenant_a).exists())
        self.assertEqual(self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.lead_a.pk})).status_code, 403)

    def test_manager_retries_crm_and_records_actor_tenant(self):
        self.login_with_role(TenantMembership.Role.MANAGER, tenant=self.tenant_a)

        response = self.client.post(reverse("operations_portal:lead_retry_crm", kwargs={"pk": self.lead_a.pk}))

        self.assertEqual(response.status_code, 302)
        outbox_event = OutboxEvent.objects.get(event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_id=str(self.lead_a.pk))
        self.assertEqual(outbox_event.tenant, self.tenant_a)
        event = AuditEvent.objects.get(action=ACTION_LEAD_CRM_DISPATCH_RETRIED)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.tenant, self.tenant_a)
        self.assertEqual(event.metadata["outbox_event_id"], str(outbox_event.event_id))

    def test_only_tenant_admin_changes_assistant_profile(self):
        AssistantProfile.objects.create(tenant=self.tenant_a)
        self.login_with_role(TenantMembership.Role.MANAGER, tenant=self.tenant_a)
        response = self.client.get(reverse("operations_portal:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Salvar")
        response = self.client.post(reverse("operations_portal:settings"), {"tenant": self.tenant_a.pk})
        self.assertEqual(response.status_code, 403)

        admin_user = get_user_model().objects.create_user(username="tenant-admin", password="pass")
        self.client.force_login(admin_user)
        TenantMembership.objects.create(tenant=self.tenant_a, user=admin_user, role=TenantMembership.Role.TENANT_ADMIN)
        response = self.client.post(
            reverse("operations_portal:settings"),
            {
                "tenant": self.tenant_a.pk,
                "action": "save_handoff",
                "handoff-human_handoff_enabled": "on",
                "handoff-human_handoff_channel": "disabled",
                "handoff-handoff_whatsapp_number": "",
                "handoff-handoff_whatsapp_label": "Falar",
                "handoff-handoff_whatsapp_message": "Olá",
            },
        )
        self.assertEqual(response.status_code, 302)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class MembershipAdminAuditTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(username="admin", password="pass", email="admin@example.com")
        self.staff = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.member = get_user_model().objects.create_user(username="member", password="pass", email="member@example.com")
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_membership_admin_create_update_and_deactivate_are_audited(self):
        self.client.force_login(self.superuser)
        add_response = self.client.post(
            reverse("admin:tenants_tenantmembership_add"),
            {"tenant": self.tenant.pk, "user": self.member.pk, "role": TenantMembership.Role.VIEWER, "is_active": "on"},
        )
        self.assertEqual(add_response.status_code, 302)
        membership = TenantMembership.objects.get(tenant=self.tenant, user=self.member)
        self.assertEqual(membership.created_by, self.superuser)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_TENANT_MEMBERSHIP_CREATED, tenant=self.tenant).exists())

        change_url = reverse("admin:tenants_tenantmembership_change", kwargs={"object_id": membership.pk})
        self.client.post(change_url, {"tenant": self.tenant.pk, "user": self.member.pk, "role": TenantMembership.Role.MANAGER, "is_active": "on"})
        update_event = AuditEvent.objects.get(action=ACTION_TENANT_MEMBERSHIP_UPDATED)
        self.assertEqual(update_event.before_data["role"], TenantMembership.Role.VIEWER)
        self.assertEqual(update_event.after_data["role"], TenantMembership.Role.MANAGER)

        self.client.post(change_url, {"tenant": self.tenant.pk, "user": self.member.pk, "role": TenantMembership.Role.MANAGER})
        deactivate_event = AuditEvent.objects.get(action=ACTION_TENANT_MEMBERSHIP_DEACTIVATED)
        self.assertEqual(deactivate_event.after_data["is_active"], False)

    def test_non_superuser_cannot_administer_memberships(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin:tenants_tenantmembership_add")).status_code, 403)


class PublicEndpointRegressionTests(TestCase):
    def test_widget_and_chat_endpoints_remain_public(self):
        tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        AssistantProfile.objects.create(tenant=tenant)

        self.assertEqual(self.client.get("/widget.js").status_code, 200)
        self.assertEqual(self.client.get("/api/widget/config/?tenant=tenant").status_code, 200)
        response = self.client.post(
            "/api/chat/",
            data=f'{{"tenant":"tenant","session_id":"public-session","request_id":"{uuid.uuid4()}","message":"Olá"}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
