from django.test import TestCase, override_settings

from tenants.models import AssistantProfile, Tenant


class WidgetTests(TestCase):
    def test_widget_js_contains_fetch(self):
        response = self.client.get("/widget.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript; charset=utf-8")
        content = response.content.decode("utf-8")
        self.assertIn("fetch", content)
        self.assertIn('getAttribute("data-api-url")', content)
        self.assertIn('new URL("/api/chat/", scriptEl.src).href', content)
        self.assertIn("session_key: sessionId", content)
        self.assertIn("/api/widget/config/", content)
        self.assertIn("loadConfig", content)
        self.assertIn("primary_color", content)
        self.assertIn("bottom_left", content)
        self.assertIn("getAttribute(\"data-api-url\")", content)

    def test_demo_page_loads_widget_script(self):
        response = self.client.get("/demo/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('/widget.js" data-tenant="smart-control-brasil"', response.content.decode("utf-8"))


class WidgetConfigEndpointTests(TestCase):
    def test_widget_config_returns_active_tenant_config(self):
        tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil", domain="smartcontrolbrasil.com.br")
        AssistantProfile.objects.create(
            tenant=tenant,
            name="Lívia",
            widget_title="Lívia Smart Control",
            launcher_label="Fale conosco",
            initial_message="Olá pela config.",
            primary_color="#123abc",
            position="bottom_left",
            placeholder_text="Digite aqui...",
            show_branding=False,
        )

        response = self.client.get("/api/widget/config/?tenant=smart-control-brasil")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tenant"], "smart-control-brasil")
        self.assertEqual(data["assistant_name"], "Lívia")
        self.assertEqual(data["widget_title"], "Lívia Smart Control")
        self.assertEqual(data["launcher_label"], "Fale conosco")
        self.assertEqual(data["initial_message"], "Olá pela config.")
        self.assertEqual(data["primary_color"], "#123abc")
        self.assertEqual(data["position"], "bottom_left")
        self.assertEqual(data["placeholder_text"], "Digite aqui...")
        self.assertFalse(data["show_branding"])
        self.assertTrue(data["is_widget_enabled"])

    def test_widget_config_inactive_tenant_returns_disabled_config(self):
        tenant = Tenant.objects.create(
            name="Inactive",
            slug="inactive",
            domain="inactive.example",
            is_active=False,
        )
        AssistantProfile.objects.create(tenant=tenant, name="Lívia")

        response = self.client.get("/api/widget/config/?tenant=inactive")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_widget_enabled"])

    def test_widget_config_missing_tenant_returns_disabled_config(self):
        response = self.client.get("/api/widget/config/?tenant=missing")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tenant"], "missing")
        self.assertFalse(response.json()["is_widget_enabled"])

    def test_widget_config_uses_defaults_and_contains_no_secrets(self):
        tenant = Tenant.objects.create(name="Defaults", slug="defaults", domain="defaults.example")
        AssistantProfile.objects.create(tenant=tenant, name="Lívia Defaults")

        response = self.client.get("/api/widget/config/?tenant=defaults")

        data = response.json()
        self.assertEqual(data["widget_title"], "Lívia Defaults")
        self.assertEqual(data["launcher_label"], "Fale com a Lívia")
        self.assertEqual(data["primary_color"], "#2563eb")
        self.assertEqual(data["position"], "bottom_right")
        self.assertEqual(data["placeholder_text"], "Digite sua mensagem...")
        payload_text = str(data)
        self.assertNotIn("secret", payload_text.lower())
        self.assertNotIn("token", payload_text.lower())


class WidgetCorsMiddlewareTests(TestCase):
    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_chat_options_allows_configured_origin(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://www.smartcontrolbrasil.com.br",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.smartcontrolbrasil.com.br")
        self.assertIn("POST", response["Access-Control-Allow-Methods"])

    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_chat_options_does_not_allow_unknown_origin(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response)


class WidgetConfigCorsMiddlewareTests(TestCase):
    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_config_get_allows_configured_origin(self):
        response = self.client.get(
            "/api/widget/config/?tenant=smart-control-brasil",
            HTTP_ORIGIN="https://www.smartcontrolbrasil.com.br",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.smartcontrolbrasil.com.br")

    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_config_options_allows_configured_origin(self):
        response = self.client.options(
            "/api/widget/config/",
            HTTP_ORIGIN="https://www.smartcontrolbrasil.com.br",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.smartcontrolbrasil.com.br")
        self.assertIn("GET", response["Access-Control-Allow-Methods"])
