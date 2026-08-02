from __future__ import annotations

from django.conf import settings


def _csv_slugs(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in str(raw or "").split(",") if item.strip())


def is_rag_semantic_context_active(*, tenant_slug: str) -> bool:
    """
    Indica se o contexto semântico RAG deve ser injetado no chat.

    Fail-closed: dry_run global bloqueia todos, exceto slugs na allowlist.
    """
    if not bool(getattr(settings, "LIVIA_RAG_ENABLED", False)):
        return False
    if not bool(getattr(settings, "LIVIA_RAG_DRY_RUN", True)):
        return True
    allowlist = _csv_slugs(getattr(settings, "LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST", ""))
    return str(tenant_slug or "").strip() in allowlist


def is_grounded_synthesis_allowed(*, tenant_slug: str, assistant_profile) -> bool:
    """
    Gate tenant-scoped para síntese grounded.

    Precedência:
    1. LIVIA_AI_ENABLED + profile.use_ai + profile.grounded_synthesis_enabled
    2. allowlist de tenants (se definida) OU flag global LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED
    """
    if not bool(getattr(settings, "LIVIA_AI_ENABLED", False)):
        return False
    if assistant_profile is None or not bool(getattr(assistant_profile, "use_ai", False)):
        return False
    if not bool(getattr(assistant_profile, "grounded_synthesis_enabled", False)):
        return False

    allowlist = _csv_slugs(getattr(settings, "LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST", ""))
    slug = str(tenant_slug or "").strip()
    if allowlist:
        return slug in allowlist
    return bool(getattr(settings, "LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED", False))
