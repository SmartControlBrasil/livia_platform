import json
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tenants.models import Tenant

from .models import ChatRequest, Conversation


class ChatRequestModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_unique_constraint_for_tenant_session_and_request_id(self):
        request_id = uuid.uuid4()
        ChatRequest.objects.create(
            tenant=self.tenant,
            session_id="session-1",
            request_id=request_id,
            request_fingerprint="a" * 64,
        )

        with self.assertRaises(IntegrityError):
            ChatRequest.objects.create(
                tenant=self.tenant,
                session_id="session-1",
                request_id=request_id,
                request_fingerprint="a" * 64,
            )

    def test_same_request_id_can_exist_in_different_tenants(self):
        other = Tenant.objects.create(name="Other", slug="other")
        request_id = uuid.uuid4()

        ChatRequest.objects.create(tenant=self.tenant, session_id="session-1", request_id=request_id, request_fingerprint="a" * 64)
        ChatRequest.objects.create(tenant=other, session_id="session-1", request_id=request_id, request_fingerprint="a" * 64)

        self.assertEqual(ChatRequest.objects.count(), 2)

    def test_status_choices_indexes_and_response_payload_default(self):
        chat_request = ChatRequest.objects.create(
            tenant=self.tenant,
            session_id="session-1",
            request_id=uuid.uuid4(),
            request_fingerprint="b" * 64,
        )

        self.assertEqual(chat_request.status, ChatRequest.Status.PROCESSING)
        self.assertEqual(chat_request.response_payload, {})
        index_fields = {tuple(index.fields) for index in ChatRequest._meta.indexes}
        self.assertIn(("tenant", "created_at"), index_fields)
        self.assertIn(("status", "updated_at"), index_fields)
        self.assertIn(("tenant", "session_id"), index_fields)


class ChatRequestAdminTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        self.chat_request = ChatRequest.objects.create(
            tenant=self.tenant,
            session_id="session-admin",
            request_id=uuid.uuid4(),
            request_fingerprint="c" * 64,
            status=ChatRequest.Status.COMPLETED,
            response_payload={"reply": "ok"},
            completed_at=timezone.now(),
        )
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser("root", "root@example.com", "pass")
        self.staff = user_model.objects.create_user("staff", "staff@example.com", "pass", is_staff=True)

    def test_superuser_can_view_readonly_chat_requests(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:conversations_chatrequest_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.chat_request.request_id))

    def test_non_superuser_cannot_access_chat_requests(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin:conversations_chatrequest_changelist"))

        self.assertEqual(response.status_code, 403)

    def test_admin_does_not_allow_add_change_or_delete(self):
        self.client.force_login(self.superuser)

        add_response = self.client.get(reverse("admin:conversations_chatrequest_add"))
        change_response = self.client.get(reverse("admin:conversations_chatrequest_change", args=[self.chat_request.pk]))
        delete_response = self.client.get(reverse("admin:conversations_chatrequest_delete", args=[self.chat_request.pk]))

        self.assertEqual(add_response.status_code, 403)
        self.assertEqual(change_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 403)


class ChatRequestReportCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_report_is_readonly_by_default(self):
        old = ChatRequest.objects.create(
            tenant=self.tenant,
            session_id="old",
            request_id=uuid.uuid4(),
            request_fingerprint="d" * 64,
            status=ChatRequest.Status.COMPLETED,
        )
        ChatRequest.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=40))
        output = StringIO()

        call_command("chat_request_report", stdout=output)

        data = json.loads(output.getvalue())
        self.assertTrue(data["cleanup"]["dry_run"])
        self.assertEqual(data["cleanup"]["eligible_chat_requests"], 1)
        self.assertEqual(ChatRequest.objects.count(), 1)

    def test_report_cleanup_requires_explicit_execute(self):
        old = ChatRequest.objects.create(
            tenant=self.tenant,
            session_id="old",
            request_id=uuid.uuid4(),
            request_fingerprint="e" * 64,
            status=ChatRequest.Status.FAILED,
        )
        Conversation.objects.create(tenant=self.tenant, session_id="keep-conversation")
        ChatRequest.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=40))
        output = StringIO()

        call_command("chat_request_report", "--execute-cleanup", stdout=output)

        data = json.loads(output.getvalue())
        self.assertFalse(data["cleanup"]["dry_run"])
        self.assertEqual(data["cleanup"]["deleted_chat_requests"], 1)
        self.assertEqual(ChatRequest.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 1)
