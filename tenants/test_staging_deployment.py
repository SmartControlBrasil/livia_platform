from __future__ import annotations

import json
import socket
import stat
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from tenants.services.staging_deployment import (
    PILOT_TENANT_SLUG,
    check_bind_port_available,
    check_git_branch,
    check_git_clean,
    check_staging_env_values,
    is_placeholder_value,
    normalize_allowlist,
    parse_env_file,
    redact_sensitive_text,
    run_predeploy_checks,
    sanitize_database_url,
)
from tenants.services.staging_postdeploy import DEFAULT_ORIGIN, run_postdeploy_checks


class StagingEnvParsingTests(SimpleTestCase):
    def test_parse_env_file_ignores_comments_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export LIVIA_ENVIRONMENT=staging",
                        "DJANGO_DEBUG=False",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            values = parse_env_file(path)
            self.assertEqual(values["LIVIA_ENVIRONMENT"], "staging")
            self.assertEqual(values["DJANGO_DEBUG"], "False")

    def test_sanitize_database_url_redacts_password(self):
        sanitized = sanitize_database_url(
            "postgresql://livia_staging_user:supersecret@127.0.0.1:5432/livia_staging"
        )
        self.assertIn("***", sanitized)
        self.assertNotIn("supersecret", sanitized)

    def test_is_placeholder_value(self):
        self.assertTrue(is_placeholder_value("CHANGE_ME_DB_PASSWORD"))
        self.assertFalse(is_placeholder_value("real-value-123"))

    def test_redact_sensitive_text(self):
        text = redact_sensitive_text("postgresql://user:abc123@127.0.0.1/db sk-proj-abc")
        self.assertNotIn("abc123", text)
        self.assertIn("***", text)


class StagingEnvValidationTests(SimpleTestCase):
    def _valid_env(self) -> dict[str, str]:
        return {
            "LIVIA_ENVIRONMENT": "staging",
            "DJANGO_DEBUG": "False",
            "DATABASE_URL": "postgresql://livia_staging_user:realpass@127.0.0.1:5432/livia_staging",
            "DJANGO_SECRET_KEY": "generated-secret-value",
            "LIVIA_RAG_EMBEDDING_PROVIDER": "openai",
            "LIVIA_ALLOW_FAKE_EMBEDDINGS": "False",
            "LIVIA_RAG_VECTOR_BACKEND": "postgres_pgvector",
            "LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST": PILOT_TENANT_SLUG,
            "LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST": PILOT_TENANT_SLUG,
            "SMART360_LEAD_DISPATCH_DRY_RUN": "True",
            "SMART360_LEAD_DISPATCH_ENABLED": "False",
            "LIVIA_WEBHOOKS_ENABLED": "False",
            "LIVIA_WEBHOOKS_DRY_RUN": "True",
            "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED": "False",
            "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN": "True",
            "LIVIA_ALLOW_ORIGINLESS_PUBLIC_API": "False",
            "LIVIA_OPENAI_API_KEY": "sk-test-key",
            "LIVIA_RAG_EMBEDDING_API_KEY": "sk-test-embedding",
            "SMART360_M2M_TOKEN": "token-value",
            "LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE": "/tmp/fake-service-account.json",
        }

    def test_valid_staging_env_passes(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("{}")
            sa_path = handle.name
        env = self._valid_env()
        env["LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE"] = sa_path
        results = check_staging_env_values(env)
        self.assertFalse(any(item.status == "FAIL" for item in results))

    def test_sqlite_database_fails(self):
        env = self._valid_env()
        env["DATABASE_URL"] = "sqlite:///db.sqlite3"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["database_url"], "FAIL")

    def test_fake_provider_fails(self):
        env = self._valid_env()
        env["LIVIA_RAG_EMBEDDING_PROVIDER"] = "fake"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["embedding_provider"], "FAIL")

    def test_broad_allowlist_fails(self):
        env = self._valid_env()
        env["LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST"] = "a,b"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["rag_allowlist"], "FAIL")

    def test_external_database_host_fails(self):
        env = self._valid_env()
        env["DATABASE_URL"] = "postgresql://u:p@db.example.com:5432/livia_staging"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["database_host"], "FAIL")

    def test_wrong_database_name_fails(self):
        env = self._valid_env()
        env["DATABASE_URL"] = "postgresql://u:p@127.0.0.1:5432/production_db"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["database_name"], "FAIL")

    def test_smart360_real_dispatch_without_dry_run_fails(self):
        env = self._valid_env()
        env["SMART360_LEAD_DISPATCH_DRY_RUN"] = "False"
        codes = {item.code: item.status for item in check_staging_env_values(env)}
        self.assertEqual(codes["smart360_dry_run"], "FAIL")

    def test_normalize_allowlist(self):
        self.assertEqual(normalize_allowlist(" a , b "), ["a", "b"])


class StagingOperationalTests(SimpleTestCase):
    def test_bind_port_detects_conflict(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            result = check_bind_port_available("127.0.0.1", port)
            self.assertEqual(result.status, "FAIL")
        finally:
            sock.close()

    @mock.patch("tenants.services.staging_deployment.subprocess.check_output")
    def test_git_branch_mismatch_fails(self, mock_output):
        mock_output.return_value = "main\n"
        result = check_git_branch()
        self.assertEqual(result.status, "FAIL")

    @mock.patch("tenants.services.staging_deployment.subprocess.check_output")
    def test_git_clean_dirty_fails_without_allow(self, mock_output):
        mock_output.return_value = " M file.py\n"
        result = check_git_clean(allow_dirty=False)
        self.assertEqual(result.status, "FAIL")


class StagingPredeployReportTests(SimpleTestCase):
    def test_predeploy_env_only_skips_django(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text(
                "LIVIA_ENVIRONMENT=staging\nDJANGO_DEBUG=False\n",
                encoding="utf-8",
            )
            report = run_predeploy_checks(
                project_root=root,
                env_path=env_path,
                skip_git=True,
                django_checks=False,
            )
            codes = [item.code for item in report.checks]
            self.assertIn("env_livia_environment", codes)
            self.assertNotIn("django_check", codes)


class StagingPostdeployMockTests(SimpleTestCase):
    def _mock_response(self, status_code=200, headers=None, json_data=None, text="ok"):
        response = mock.Mock()
        response.status_code = status_code
        response.headers = headers or {}
        response.text = text
        if json_data is not None:
            response.json.return_value = json_data
        return response

    @mock.patch("tenants.services.staging_postdeploy.requests.Session.request")
    def test_postdeploy_passes_with_mocked_http(self, mock_request):
        def side_effect(method, url, **kwargs):
            if method == "GET" and "health/?readiness=1" in url:
                return self._mock_response(json_data={"status": "ok", "readiness": "READY"})
            if method == "GET" and url.endswith("health/"):
                return self._mock_response(headers={"X-Content-Type-Options": "nosniff"})
            if method == "GET" and "widget.js" in url:
                return self._mock_response(headers={"Content-Type": "application/javascript"}, text="console.log()")
            if method == "GET" and "widget/config" in url:
                return self._mock_response()
            if method == "OPTIONS":
                origin = kwargs.get("headers", {}).get("Origin", "")
                if origin.endswith("invalid"):
                    return self._mock_response(status_code=204, headers={})
                return self._mock_response(
                    status_code=204,
                    headers={"Access-Control-Allow-Origin": "https://www.granimarmorespitondo.com.br"},
                )
            if method == "POST":
                return self._mock_response(status_code=404, json_data={"error": "tenant_unavailable"})
            if method == "GET":
                return self._mock_response(status_code=404)
            return self._mock_response()

        mock_request.side_effect = side_effect
        report = run_postdeploy_checks(
            base_url="https://staging-livia.example.com",
            tenant=PILOT_TENANT_SLUG,
            origin="https://www.granimarmorespitondo.com.br",
        )
        self.assertNotEqual(report.summary, "FAIL")


from tenants.models import Tenant


class StagingDeploymentReportCommandTests(TestCase):
    @override_settings(
        LIVIA_ENVIRONMENT="development",
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_DRY_RUN=True,
        LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST=PILOT_TENANT_SLUG,
        LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST=PILOT_TENANT_SLUG,
    )
    def test_staging_deployment_report_json(self):
        from tenants.models import AssistantProfile

        tenant = Tenant.objects.create(
            slug=PILOT_TENANT_SLUG,
            name="Granimármores Pitondo",
            domain="granimarmorespitondo.com.br",
            is_active=True,
        )
        AssistantProfile.objects.create(
            tenant=tenant,
            name="Lívia",
            initial_message="Olá",
            tone="consultivo",
            primary_goal="qualificar",
            is_active=True,
            use_ai=True,
            grounded_synthesis_enabled=True,
        )
        out = StringIO()
        call_command("staging_deployment_report", tenant=PILOT_TENANT_SLUG, json=True, stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["environment"], "development")
        self.assertIn("database", payload)
        self.assertIn("sanitized_url", payload["database"])
        self.assertNotIn("supersecret", out.getvalue())
