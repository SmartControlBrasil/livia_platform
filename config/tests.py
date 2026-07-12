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
