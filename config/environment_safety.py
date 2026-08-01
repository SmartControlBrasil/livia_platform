from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class EnvironmentCheck:
    ok: bool
    code: str
    detail: str
    level: str = "critical"  # critical | warning | info


def inspect_environment_safety(*, tenant_slug: str | None = None) -> list[EnvironmentCheck]:
    """Readiness de configuração operacional (sem secrets, sem side effects)."""
    checks: list[EnvironmentCheck] = []
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    running_tests = bool(getattr(settings, "RUNNING_TESTS", False))

    checks.append(
        EnvironmentCheck(
            ok=True,
            code="environment_name",
            detail=f"LIVIA_ENVIRONMENT={env or 'development'}",
            level="info",
        )
    )

    provider = str(getattr(settings, "LIVIA_RAG_EMBEDDING_PROVIDER", "openai") or "openai").strip().lower()
    if env in {"staging", "production"} and provider == "fake":
        checks.append(
            EnvironmentCheck(
                ok=False,
                code="embedding_provider_fake",
                detail=f"LIVIA_RAG_EMBEDDING_PROVIDER=fake is forbidden in {env}",
                level="critical",
            )
        )
    else:
        checks.append(
            EnvironmentCheck(
                ok=True,
                code="embedding_provider",
                detail=f"provider={provider}",
                level="info",
            )
        )

    crm_dry_run = bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True))
    crm_enabled = bool(getattr(settings, "SMART360_LEAD_DISPATCH_ENABLED", False))
    if env == "staging" and not crm_dry_run:
        checks.append(
            EnvironmentCheck(
                ok=False,
                code="smart360_dry_run",
                detail="SMART360_LEAD_DISPATCH_DRY_RUN must be True in staging",
                level="critical",
            )
        )
    elif env == "staging" and crm_enabled and not crm_dry_run:
        checks.append(
            EnvironmentCheck(
                ok=False,
                code="smart360_real_dispatch",
                detail="SMART360 real dispatch must stay disabled in staging",
                level="critical",
            )
        )
    else:
        checks.append(
            EnvironmentCheck(
                ok=True,
                code="smart360_dry_run",
                detail=f"enabled={crm_enabled} dry_run={crm_dry_run}",
                level="info",
            )
        )

    if env == "staging":
        if not bool(getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True)):
            checks.append(
                EnvironmentCheck(
                    ok=False,
                    code="webhooks_dry_run",
                    detail="LIVIA_WEBHOOKS_DRY_RUN must be True in staging",
                    level="critical",
                )
            )
        if not bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True)):
            checks.append(
                EnvironmentCheck(
                    ok=False,
                    code="handoff_notifications_dry_run",
                    detail="LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN must be True in staging",
                    level="critical",
                )
            )
        if bool(getattr(settings, "DEBUG", False)):
            checks.append(
                EnvironmentCheck(
                    ok=False,
                    code="debug_disabled",
                    detail="DEBUG should be False in staging",
                    level="warning",
                )
            )

    if env in {"staging", "production"} and bool(getattr(settings, "LIVIA_ALLOW_FAKE_EMBEDDINGS", False)):
        checks.append(
            EnvironmentCheck(
                ok=False,
                code="fake_embeddings_flag",
                detail="LIVIA_ALLOW_FAKE_EMBEDDINGS must be False in staging/production",
                level="warning",
            )
        )

    if tenant_slug:
        checks.extend(_tenant_gate_checks(tenant_slug))

    if running_tests:
        checks.append(
            EnvironmentCheck(
                ok=True,
                code="running_tests",
                detail="RUNNING_TESTS=True (checks relaxed for unit tests)",
                level="info",
            )
        )

    return checks


def _tenant_gate_checks(tenant_slug: str) -> list[EnvironmentCheck]:
    from assistant_core.services.ai_feature_gates import (
        is_grounded_synthesis_allowed,
        is_rag_semantic_context_active,
    )
    from tenants.models import AssistantProfile, Tenant

    checks: list[EnvironmentCheck] = []
    tenant = Tenant.objects.filter(slug=str(tenant_slug).strip()).first()
    if tenant is None:
        return [
            EnvironmentCheck(
                ok=False,
                code="tenant_missing",
                detail=f"tenant {tenant_slug} not found",
                level="critical",
            )
        ]
    profile = AssistantProfile.objects.filter(tenant=tenant, is_active=True).first()
    rag_active = is_rag_semantic_context_active(tenant_slug=tenant.slug)
    grounded = bool(
        profile and is_grounded_synthesis_allowed(tenant_slug=tenant.slug, assistant_profile=profile)
    )
    checks.append(
        EnvironmentCheck(
            ok=rag_active,
            code="tenant_rag_gate",
            detail=f"rag_semantic_active={rag_active}",
            level="critical" if not rag_active else "info",
        )
    )
    checks.append(
        EnvironmentCheck(
            ok=grounded,
            code="tenant_grounded_gate",
            detail=f"grounded_synthesis_allowed={grounded}",
            level="warning" if not grounded else "info",
        )
    )
    return checks


def summarize_environment_readiness(checks: list[EnvironmentCheck]) -> str:
    if any(not item.ok and item.level == "critical" for item in checks):
        return "NOT_READY"
    if any(not item.ok and item.level == "warning" for item in checks):
        return "READY_WITH_WARNINGS"
    return "READY"
