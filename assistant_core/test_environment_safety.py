from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from config.environment_safety import inspect_environment_safety, summarize_environment_readiness


class EnvironmentSafetyTests(SimpleTestCase):
    @override_settings(LIVIA_ENVIRONMENT="staging", SMART360_LEAD_DISPATCH_DRY_RUN=False)
    def test_staging_requires_smart360_dry_run(self):
        checks = inspect_environment_safety()
        status = summarize_environment_readiness(checks)
        self.assertEqual(status, "NOT_READY")
        codes = {item.code for item in checks if not item.ok}
        self.assertIn("smart360_dry_run", codes)

    @override_settings(
        LIVIA_ENVIRONMENT="staging",
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        LIVIA_RAG_EMBEDDING_PROVIDER="openai",
        LIVIA_WEBHOOKS_DRY_RUN=True,
        LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN=True,
        DEBUG=False,
    )
    def test_staging_ready_when_invariants_hold(self):
        status = summarize_environment_readiness(inspect_environment_safety())
        self.assertEqual(status, "READY")

    @override_settings(LIVIA_ENVIRONMENT="staging", LIVIA_RAG_EMBEDDING_PROVIDER="fake")
    def test_staging_blocks_fake_provider(self):
        checks = inspect_environment_safety()
        self.assertEqual(summarize_environment_readiness(checks), "NOT_READY")
