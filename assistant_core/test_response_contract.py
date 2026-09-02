from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from assistant_core.prompts.livia import DEFAULT_REPLY
from assistant_core.services.deterministic_synthesis import prefer_contextual_reply_over_fallback
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_AI_ENABLED=False,
    LIVIA_RAG_ENABLED=False,
    LIVIA_CHAT_RATE_LIMIT_ENABLED=False,
    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
)
class ChatResponseContractTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="SCB", slug="smart-control-brasil")
        AssistantProfile.objects.create(tenant=self.tenant, name="Lívia")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.example.com")
        self.client = Client()

    def _post(self, message: str):
        request_id = str(uuid.uuid4())
        payload = {
            "tenant": self.tenant.slug,
            "session_id": f"contract-{uuid.uuid4().hex[:8]}",
            "request_id": request_id,
            "message": message,
        }
        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT=self.tenant.slug,
            HTTP_X_LIVIA_REQUEST_ID=request_id,
        )
        return response, request_id

    def test_success_response_always_has_non_empty_reply_string(self):
        response, request_id = self._post("[ROLLOUT SMOKE] ping seguro sem lead/handoff/CRM")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsInstance(body.get("reply"), str)
        self.assertTrue(body["reply"].strip())
        self.assertNotEqual(body["reply"].strip(), "")
        self.assertEqual(body.get("tenant"), self.tenant.slug)
        self.assertIn("session_id", body)

    def test_greeting_has_reply(self):
        response, _ = self._post("Olá")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(str(response.json().get("reply") or "").strip())

    def test_prefer_contextual_never_returns_empty_for_unknown_message(self):
        reply = prefer_contextual_reply_over_fallback(
            knowledge_context="",
            current_message="[ROLLOUT SMOKE] ping seguro sem lead/handoff/CRM",
            history=[],
        )
        self.assertTrue(reply.strip())
        self.assertEqual(reply, DEFAULT_REPLY)
