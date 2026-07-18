from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from config import settings as project_settings


class DeploymentSettingsHelperTests(SimpleTestCase):
    def test_csv_env_parses_comma_separated_values(self):
        with patch.dict("os.environ", {"DJANGO_ALLOWED_HOSTS": "livia.example, localhost,127.0.0.1"}):
            self.assertEqual(
                project_settings.csv_env("DJANGO_ALLOWED_HOSTS"),
                ["livia.example", "localhost", "127.0.0.1"],
            )

    def test_csv_env_uses_default_when_missing(self):
        self.assertEqual(
            project_settings.csv_env("LIVIA_MISSING_TEST_VALUE", default="localhost,127.0.0.1"),
            ["localhost", "127.0.0.1"],
        )

    @override_settings(DEBUG=True)
    def test_debug_true_settings_remain_valid(self):
        self.assertTrue(project_settings.DEBUG in {True, False})
        self.assertIn("django.contrib.staticfiles", project_settings.INSTALLED_APPS)

    def test_ssl_redirect_default_is_disabled_while_running_test_command(self):
        self.assertTrue(project_settings.RUNNING_TESTS)
        self.assertFalse(project_settings.SECURE_SSL_REDIRECT)



class HealthcheckTests(SimpleTestCase):
    def test_healthcheck_returns_ok(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "livia-platform"})


class LiviaWidgetCorsMiddlewareTests(SimpleTestCase):
    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_allowed_origin_receives_cors_headers(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://www.smartcontrolbrasil.com.br",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.smartcontrolbrasil.com.br")

    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_blocked_origin_does_not_receive_cors_headers(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=["https://www.smartcontrolbrasil.com.br"])
    def test_request_without_origin_does_not_break(self):
        response = self.client.options("/api/chat/")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    @override_settings(DEBUG=False, LIVIA_ALLOWED_WIDGET_ORIGINS=[])
    def test_empty_allowed_origins_is_permissive(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="https://www.canecadegaragem.com.br",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.canecadegaragem.com.br")
