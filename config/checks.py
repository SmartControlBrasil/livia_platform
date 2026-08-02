from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_staging_environment_safety(app_configs, **kwargs):
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    if env != "staging":
        return []

    errors: list[Error] = []
    if not bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True)):
        errors.append(
            Error(
                "SMART360_LEAD_DISPATCH_DRY_RUN must be True when LIVIA_ENVIRONMENT=staging.",
                id="livia.E001",
            )
        )
    provider = str(getattr(settings, "LIVIA_RAG_EMBEDDING_PROVIDER", "openai") or "openai").strip().lower()
    if provider == "fake":
        errors.append(
            Error(
                "LIVIA_RAG_EMBEDDING_PROVIDER=fake is forbidden when LIVIA_ENVIRONMENT=staging.",
                id="livia.E002",
            )
        )
    if not bool(getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True)):
        errors.append(
            Error(
                "LIVIA_WEBHOOKS_DRY_RUN must be True when LIVIA_ENVIRONMENT=staging.",
                id="livia.E003",
            )
        )
    if not bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True)):
        errors.append(
            Error(
                "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN must be True when LIVIA_ENVIRONMENT=staging.",
                id="livia.E004",
            )
        )
    return errors
