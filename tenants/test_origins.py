import json
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import (
    ACTION_TENANT_ORIGIN_CREATED,
    ACTION_TENANT_ORIGIN_DEACTIVATED,
    ACTION_TENANT_ORIGIN_UPDATED,
    AuditEvent,
)
from conversations.models import Conversation, HandoffRequest, Message
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.origins import is_origin_allowed, normalize_origin, resolve_public_tenant
from tenants.services.onboarding import TenantOnboardingService


class OriginNormalizationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_accepts_and_normalizes_valid_http_https(self):
        self.assertEqual(normalize_origin("HTTPS://WWW.Example.COM/"), "https://www.example.com")
        self.assertEqual(normalize_origin("http://localhost:8000"), "http://localhost:8000")

    def test_rejects_invalid_origin_parts(self):
        invalid = [
            "",
            "*",
            "https://example.com/path",
            "https://example.com?x=1",
            "https://example.com#frag",
            "https://user:pass@example.com",
            "ftp://example.com",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_origin(value)

    def test_unique_per_tenant_and_same_origin_across_tenants(self):
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://example.com")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://example.com")
        other = Tenant.objects.create(name="Other", slug="other")
        TenantAllowedOrigin.objects.create(tenant=other, origin="https://example.com")
        self.assertEqual(TenantAllowedOrigin.objects.count(), 2)


@override_settings(DEBUG=False, LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=False)
class OriginAuthorizationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        self.other = Tenant.objects.create(name="Other", slug="other")
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.example.com")

    def test_exact_authorized_origin_passes(self):
        self.assertTrue(is_origin_allowed(self.tenant, "https://www.example.com"))

    def test_other_tenant_subdomain_malicious_and_inactive_fail(self):
        self.assertFalse(is_origin_allowed(self.other, "https://www.example.com"))
        self.assertFalse(is_origin_allowed(self.tenant, "https://app.example.com"))
        self.assertFalse(is_origin_allowed(self.tenant, "https://evil-example.com"))
        TenantAllowedOrigin.objects.filter(tenant=self.tenant).update(is_active=False)
        self.assertFalse(is_origin_allowed(self.tenant, "https://www.example.com"))

    def test_origin_missing_malformed_and_no_origins_fail(self):
        self.assertFalse(is_origin_allowed(self.tenant, ""))
        self.assertFalse(is_origin_allowed(self.tenant, "not an origin"))
        TenantAllowedOrigin.objects.filter(tenant=self.tenant).delete()
        self.assertFalse(is_origin_allowed(self.tenant, "https://www.example.com"))

    @override_settings(DEBUG=True, LIVIA_DEV_ALLOWED_WIDGET_ORIGINS=["http://localhost:8000"])
    def test_localhost_follows_explicit_development_policy(self):
        self.assertTrue(is_origin_allowed(self.tenant, "http://localhost:8000"))
        self.assertFalse(is_origin_allowed(self.tenant, "http://localhost:9000"))


@override_settings(DEBUG=False, LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=False)
class PublicEndpointOriginTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        AssistantProfile.objects.create(tenant=self.tenant)
        TenantAllowedOrigin.objects.create(tenant=self.tenant, origin="https://www.example.com")

    def test_chat_authorized_post_and_blocked_origin(self):
        request_id = str(uuid.uuid4())
        payload = {"tenant": "tenant", "session_id": "ok", "request_id": request_id, "message": "Olá"}
        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT="tenant",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("reply", response.json())
        self.assertNotEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.example.com")
        self.assertIn("X-Livia-Request-ID", response["Access-Control-Allow-Headers"])

        response = self.client.post(
            "/api/chat/",
            data=json.dumps({**payload, "session_id": "blocked", "request_id": str(uuid.uuid4())}),
            content_type="application/json",
            HTTP_ORIGIN="https://evil-example.com",
            HTTP_X_LIVIA_TENANT="tenant",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Conversation.objects.filter(session_id="blocked").exists())
        self.assertEqual(Message.objects.filter(conversation__session_id="blocked").count(), 0)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(HandoffRequest.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_chat_header_payload_mismatch_and_inactive_tenant_fail(self):
        response = self.client.post(
            "/api/chat/",
            data=json.dumps({"tenant": "tenant", "session_id": "bad", "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT="other",
        )
        self.assertEqual(response.status_code, 400)
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])
        response = self.client.post(
            "/api/chat/",
            data=json.dumps({"tenant": "tenant", "session_id": "inactive", "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT="tenant",
        )
        self.assertEqual(response.status_code, 403)

    def test_preflight_authorized_and_unauthorized(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT="tenant",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.example.com")
        self.assertIn("X-Livia-Tenant", response["Access-Control-Allow-Headers"])
        self.assertIn("X-Livia-Request-ID", response["Access-Control-Allow-Headers"])
        self.assertNotEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertIn("Origin", response["Vary"])

        query_response = self.client.options(
            "/api/chat/?tenant=tenant",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-livia-request-id,x-livia-tenant",
        )
        self.assertEqual(query_response.status_code, 204)
        self.assertEqual(query_response["Access-Control-Allow-Origin"], "https://www.example.com")
        self.assertIn("X-Livia-Request-ID", query_response["Access-Control-Allow-Headers"])

        response = self.client.options(
            "/api/chat/?tenant=tenant",
            HTTP_ORIGIN="https://evil-example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_config_authorized_and_blocked(self):
        response = self.client.get(
            "/api/widget/config/?tenant=tenant",
            HTTP_ORIGIN="https://www.example.com",
            HTTP_X_LIVIA_TENANT="tenant",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_widget_enabled"])

        response = self.client.get(
            "/api/widget/config/?tenant=tenant",
            HTTP_ORIGIN="https://evil-example.com",
            HTTP_X_LIVIA_TENANT="tenant",
        )
        self.assertEqual(response.status_code, 403)


@override_settings(
    DEBUG=False,
    LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=False,
    LIVIA_AI_ENABLED=False,
    SMART360_LEAD_DISPATCH_ENABLED=False,
    SMART360_LEAD_DISPATCH_DRY_RUN=True,
    SMART360_LEAD_DISPATCH_REAL_ENABLED=False,
    LIVIA_WEBHOOKS_ENABLED=False,
    LIVIA_WEBHOOKS_DRY_RUN=True,
    LIVIA_WEBHOOKS_REAL_ENABLED=False,
    LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED=False,
    LIVIA_CHAT_RATE_LIMIT_ENABLED=False,
)
class PublicTenantContractTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        AssistantProfile.objects.create(tenant=self.tenant_a, name="Lívia A")
        AssistantProfile.objects.create(tenant=self.tenant_b, name="Lívia B")
        TenantAllowedOrigin.objects.create(tenant=self.tenant_a, origin="https://a.example")
        TenantAllowedOrigin.objects.create(tenant=self.tenant_b, origin="https://b.example")

    def test_public_tenant_resolution_order_and_mismatch(self):
        request = self.client.request().wsgi_request
        request.META["HTTP_X_LIVIA_TENANT"] = "tenant-a"
        resolution = resolve_public_tenant(request, payload={"tenant": "tenant-a"})
        self.assertTrue(resolution.ok)
        self.assertEqual(resolution.tenant, self.tenant_a)

        response = self.client.post(
            "/api/chat/?tenant=tenant-b",
            data=json.dumps({"tenant": "tenant-a", "session_id": "mismatch", "request_id": str(uuid.uuid4()), "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://a.example",
            HTTP_X_LIVIA_TENANT="tenant-a",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "tenant_mismatch")
        self.assertFalse(Conversation.objects.filter(session_id="mismatch").exists())

    def test_origin_is_authorized_against_tenant_pair(self):
        allowed_a = self.client.get("/api/widget/config/?tenant=tenant-a", HTTP_ORIGIN="https://a.example", HTTP_X_LIVIA_TENANT="tenant-a")
        denied_cross = self.client.get("/api/widget/config/?tenant=tenant-b", HTTP_ORIGIN="https://a.example", HTTP_X_LIVIA_TENANT="tenant-b")
        allowed_b = self.client.get("/api/widget/config/?tenant=tenant-b", HTTP_ORIGIN="https://b.example", HTTP_X_LIVIA_TENANT="tenant-b")

        self.assertEqual(allowed_a.status_code, 200)
        self.assertEqual(allowed_a["Access-Control-Allow-Origin"], "https://a.example")
        self.assertEqual(denied_cross.status_code, 403)
        self.assertEqual(denied_cross.json()["error"], "origin_not_allowed")
        self.assertNotIn("Access-Control-Allow-Origin", denied_cross)
        self.assertEqual(allowed_b.status_code, 200)
        self.assertEqual(allowed_b["Access-Control-Allow-Origin"], "https://b.example")

    def test_root_and_www_origins_are_not_equivalent(self):
        tenant = Tenant.objects.create(name="Cliente", slug="cliente")
        AssistantProfile.objects.create(tenant=tenant)
        TenantAllowedOrigin.objects.create(tenant=tenant, origin="https://cliente.com.br")

        root = self.client.get("/api/widget/config/?tenant=cliente", HTTP_ORIGIN="https://cliente.com.br", HTTP_X_LIVIA_TENANT="cliente")
        www = self.client.get("/api/widget/config/?tenant=cliente", HTTP_ORIGIN="https://www.cliente.com.br", HTTP_X_LIVIA_TENANT="cliente")

        self.assertEqual(root.status_code, 200)
        self.assertEqual(www.status_code, 403)
        self.assertEqual(www.json()["error"], "origin_not_allowed")

    def test_full_public_widget_flow_for_valid_site(self):
        widget = self.client.get("/widget.js")
        self.assertEqual(widget.status_code, 200)
        self.assertIn("data-tenant", widget.content.decode("utf-8"))

        config = self.client.get("/api/widget/config/?tenant=tenant-a", HTTP_ORIGIN="https://a.example", HTTP_X_LIVIA_TENANT="tenant-a")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config["Access-Control-Allow-Origin"], "https://a.example")
        self.assertEqual(config.json()["api_contract_version"], "2026-08-23")
        self.assertNotEqual(config["Access-Control-Allow-Origin"], "*")

        options = self.client.options(
            "/api/chat/?tenant=tenant-a",
            HTTP_ORIGIN="https://a.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-livia-tenant,x-livia-request-id",
        )
        self.assertEqual(options.status_code, 204)
        self.assertEqual(options["Access-Control-Allow-Origin"], "https://a.example")
        self.assertIn("GET", options["Access-Control-Allow-Methods"])
        self.assertIn("POST", options["Access-Control-Allow-Methods"])
        self.assertIn("OPTIONS", options["Access-Control-Allow-Methods"])
        self.assertIn("Origin", options["Vary"])

        request_id = str(uuid.uuid4())
        chat = self.client.post(
            "/api/chat/?tenant=tenant-a",
            data=json.dumps({"session_id": "full-flow", "request_id": request_id, "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://a.example",
            HTTP_X_LIVIA_TENANT="tenant-a",
            HTTP_X_LIVIA_REQUEST_ID=request_id,
        )
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat["Access-Control-Allow-Origin"], "https://a.example")
        self.assertEqual(chat.json()["tenant"], "tenant-a")
        self.assertTrue(Conversation.objects.filter(tenant=self.tenant_a, session_id="full-flow").exists())

    def test_tenant_isolation_blocks_cross_origin_and_identity_reuse(self):
        cross_origin = self.client.post(
            "/api/chat/?tenant=tenant-a",
            data=json.dumps({"tenant": "tenant-a", "session_id": "cross-origin", "request_id": str(uuid.uuid4()), "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://b.example",
            HTTP_X_LIVIA_TENANT="tenant-a",
        )
        self.assertEqual(cross_origin.status_code, 403)

        ambiguous_config = self.client.get(
            "/api/widget/config/?tenant=tenant-b",
            HTTP_ORIGIN="https://b.example",
            HTTP_X_LIVIA_TENANT="tenant-a",
        )
        self.assertEqual(ambiguous_config.status_code, 400)
        self.assertEqual(ambiguous_config.json()["error"], "tenant_mismatch")

        incompatible_identity = self.client.post(
            "/api/chat/?tenant=tenant-b",
            data=json.dumps({"tenant": "tenant-b", "session_id": "cross-identity", "request_id": str(uuid.uuid4()), "message": "Olá"}),
            content_type="application/json",
            HTTP_ORIGIN="https://b.example",
            HTTP_X_LIVIA_TENANT="tenant-a",
        )
        self.assertEqual(incompatible_identity.status_code, 400)
        self.assertEqual(incompatible_identity.json()["error"], "tenant_mismatch")
        self.assertFalse(Conversation.objects.filter(session_id__in=["cross-origin", "cross-identity"]).exists())


class WidgetAndOnboardingOriginTests(TestCase):
    def test_widget_sends_livia_tenant_header_without_credentials(self):
        response = self.client.get("/widget.js")
        content = response.content.decode()
        self.assertIn('"X-Livia-Tenant": tenant', content)
        self.assertIn("tenant: tenant", content)
        self.assertIn("textContent", content)
        self.assertNotIn("credentials:", content)

    def test_onboarding_creates_origins_idempotently_and_invalid_writes_nothing(self):
        service = TenantOnboardingService()
        service.onboard(slug="tenant", name="Tenant", domain="https://www.example.com", allowed_origins=["https://www.example.com"])
        service.onboard(slug="tenant", name="Tenant", domain="https://www.example.com", allowed_origins=["https://www.example.com/"])
        self.assertEqual(TenantAllowedOrigin.objects.count(), 1)

        with self.assertRaises(ValidationError):
            service.onboard(slug="bad", name="Bad", domain="https://bad.example", allowed_origins=["https://bad.example/path"])
        self.assertFalse(Tenant.objects.filter(slug="bad").exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class TenantAllowedOriginAdminTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(username="admin", password="pass", email="admin@example.com")
        self.staff = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_admin_created_by_and_audit_events(self):
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("admin:tenants_tenantallowedorigin_add"),
            {"tenant": self.tenant.pk, "origin": "HTTPS://WWW.Example.COM/", "is_active": "on"},
        )
        self.assertEqual(response.status_code, 302)
        origin = TenantAllowedOrigin.objects.get()
        self.assertEqual(origin.origin, "https://www.example.com")
        self.assertEqual(origin.created_by, self.superuser)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_TENANT_ORIGIN_CREATED).exists())

        change_url = reverse("admin:tenants_tenantallowedorigin_change", kwargs={"object_id": origin.pk})
        self.client.post(change_url, {"tenant": self.tenant.pk, "origin": "https://app.example.com", "is_active": "on"})
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_TENANT_ORIGIN_UPDATED).exists())
        self.client.post(change_url, {"tenant": self.tenant.pk, "origin": "https://app.example.com"})
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_TENANT_ORIGIN_DEACTIVATED).exists())

    def test_non_superuser_cannot_administer_origins(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin:tenants_tenantallowedorigin_add")).status_code, 403)
