from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class SideEffectType(StrEnum):
    OPENAI_CHAT = "OPENAI_CHAT"
    OPENAI_EMBEDDING = "OPENAI_EMBEDDING"
    GOOGLE_DRIVE_SYNC = "GOOGLE_DRIVE_SYNC"
    SMART360_LEAD_DISPATCH = "SMART360_LEAD_DISPATCH"
    WEBHOOK_DELIVERY = "WEBHOOK_DELIVERY"
    EMAIL_NOTIFICATION = "EMAIL_NOTIFICATION"
    WHATSAPP_HANDOFF = "WHATSAPP_HANDOFF"


class SideEffectStatus(StrEnum):
    BLOCKED = "BLOCKED"
    DRY_RUN = "DRY_RUN"
    REAL_ENABLED = "REAL_ENABLED"


@dataclass(frozen=True)
class SideEffectDecision:
    side_effect: SideEffectType
    status: SideEffectStatus
    allowed: bool
    dry_run: bool
    code: str
    reason: str

    @property
    def external_call_allowed(self) -> bool:
        return self.status == SideEffectStatus.REAL_ENABLED

    def to_dict(self) -> dict:
        return {
            "side_effect": self.side_effect.value,
            "status": self.status.value,
            "allowed": self.allowed,
            "dry_run": self.dry_run,
            "code": self.code,
            "reason": self.reason,
        }


def evaluate_side_effect_policy(
    *,
    side_effect: SideEffectType,
    tenant=None,
    integration_configured: bool = True,
) -> SideEffectDecision:
    tenant_slug = str(getattr(tenant, "slug", "") or "").strip()
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    running_tests = bool(getattr(settings, "RUNNING_TESTS", False))
    allow_real_in_tests = bool(getattr(settings, "LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS", False))

    if side_effect == SideEffectType.SMART360_LEAD_DISPATCH:
        enabled = bool(getattr(settings, "SMART360_LEAD_DISPATCH_ENABLED", False))
        dry_run = bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True))
        real_enabled = bool(getattr(settings, "SMART360_LEAD_DISPATCH_REAL_ENABLED", False))
        allowed_envs = _csv_set(getattr(settings, "SMART360_LEAD_DISPATCH_REAL_ALLOWED_ENVS", "production"))
        tenant_allowlist = _csv_set(getattr(settings, "SMART360_LEAD_DISPATCH_REAL_TENANT_ALLOWLIST", ""))

        if not enabled and not dry_run:
            return _blocked(side_effect, "smart360_disabled", "Smart360 dispatch está desabilitado.")
        if dry_run:
            return _dry_run(side_effect, "smart360_dry_run", "Smart360 em modo dry-run.")
        if not real_enabled:
            return _blocked(
                side_effect,
                "smart360_real_not_explicitly_enabled",
                "Despacho real Smart360 exige autorização explícita.",
            )
        if running_tests and not allow_real_in_tests:
            return _blocked(side_effect, "smart360_blocked_in_tests", "Despacho real Smart360 bloqueado em testes.")
        if allowed_envs and env not in allowed_envs:
            return _blocked(side_effect, "smart360_env_not_allowed", "Ambiente não autorizado para Smart360 real.")
        if tenant_allowlist and tenant_slug not in tenant_allowlist:
            return _blocked(side_effect, "smart360_tenant_not_allowed", "Tenant não autorizado para Smart360 real.")
        if not integration_configured:
            return _blocked(side_effect, "smart360_missing_config", "Configuração Smart360 incompleta.")
        return _real_enabled(side_effect, "smart360_real_enabled", "Despacho real Smart360 habilitado.")

    if side_effect == SideEffectType.WEBHOOK_DELIVERY:
        enabled = bool(getattr(settings, "LIVIA_WEBHOOKS_ENABLED", False))
        dry_run = bool(getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True))
        real_enabled = bool(getattr(settings, "LIVIA_WEBHOOKS_REAL_ENABLED", False))
        allowed_envs = _csv_set(getattr(settings, "LIVIA_WEBHOOKS_REAL_ALLOWED_ENVS", "production"))
        tenant_allowlist = _csv_set(getattr(settings, "LIVIA_WEBHOOKS_REAL_TENANT_ALLOWLIST", ""))

        if not enabled:
            return _blocked(side_effect, "webhooks_disabled", "Entrega de webhooks desabilitada.")
        if dry_run:
            return _dry_run(side_effect, "webhooks_dry_run", "Entrega de webhooks em dry-run.")
        if not real_enabled:
            return _blocked(side_effect, "webhooks_real_not_enabled", "Webhooks reais exigem autorização explícita.")
        if running_tests and not allow_real_in_tests:
            return _blocked(side_effect, "webhooks_blocked_in_tests", "Webhooks reais bloqueados em testes.")
        if allowed_envs and env not in allowed_envs:
            return _blocked(side_effect, "webhooks_env_not_allowed", "Ambiente não autorizado para webhooks reais.")
        if tenant_allowlist and tenant_slug not in tenant_allowlist:
            return _blocked(side_effect, "webhooks_tenant_not_allowed", "Tenant não autorizado para webhooks reais.")
        return _real_enabled(side_effect, "webhooks_real_enabled", "Entrega real de webhook habilitada.")

    if side_effect == SideEffectType.OPENAI_CHAT:
        enabled = bool(getattr(settings, "LIVIA_AI_ENABLED", False))
        dry_run = bool(getattr(settings, "LIVIA_AI_DRY_RUN", True))
        has_key = bool(str(getattr(settings, "LIVIA_OPENAI_API_KEY", "") or "").strip())
        if not enabled:
            return _blocked(side_effect, "openai_chat_disabled", "OpenAI chat desabilitado.")
        if dry_run:
            return _dry_run(side_effect, "openai_chat_dry_run", "OpenAI chat em dry-run.")
        if running_tests and not allow_real_in_tests:
            return _blocked(side_effect, "openai_chat_blocked_in_tests", "OpenAI chat bloqueado em testes.")
        if not has_key:
            return _blocked(side_effect, "openai_chat_missing_api_key", "OpenAI chat sem chave configurada.")
        return _real_enabled(side_effect, "openai_chat_real_enabled", "OpenAI chat real habilitado.")

    if side_effect == SideEffectType.OPENAI_EMBEDDING:
        rag_enabled = bool(getattr(settings, "LIVIA_RAG_ENABLED", False))
        provider = str(getattr(settings, "LIVIA_RAG_EMBEDDING_PROVIDER", "openai") or "openai").strip().lower()
        has_key = bool(str(getattr(settings, "LIVIA_RAG_EMBEDDING_API_KEY", "") or "").strip())
        if not rag_enabled:
            return _blocked(side_effect, "openai_embedding_rag_disabled", "RAG desabilitado.")
        if provider != "openai":
            return _blocked(side_effect, "openai_embedding_provider_not_openai", "Provider de embedding não é OpenAI.")
        if running_tests and not allow_real_in_tests:
            return _blocked(side_effect, "openai_embedding_blocked_in_tests", "OpenAI embedding bloqueado em testes.")
        if not has_key:
            return _blocked(side_effect, "openai_embedding_missing_api_key", "Embedding OpenAI sem chave configurada.")
        return _real_enabled(side_effect, "openai_embedding_real_enabled", "Embedding OpenAI real habilitado.")

    if side_effect == SideEffectType.GOOGLE_DRIVE_SYNC:
        service_account = str(getattr(settings, "LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE", "") or "").strip()
        real_enabled = bool(getattr(settings, "LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED", False))
        allowed_envs = _csv_set(getattr(settings, "LIVIA_GOOGLE_DRIVE_SYNC_REAL_ALLOWED_ENVS", "production"))
        tenant_allowlist = _csv_set(getattr(settings, "LIVIA_GOOGLE_DRIVE_SYNC_REAL_TENANT_ALLOWLIST", ""))
        if running_tests and not allow_real_in_tests:
            return _blocked(side_effect, "drive_sync_blocked_in_tests", "Google Drive sync bloqueado em testes.")
        if not service_account:
            return _blocked(side_effect, "drive_sync_missing_service_account", "Google Drive sem service account.")
        if not real_enabled:
            return _blocked(side_effect, "drive_sync_real_not_enabled", "Google Drive sync real exige autorização explícita.")
        if allowed_envs and env not in allowed_envs:
            return _blocked(side_effect, "drive_sync_env_not_allowed", "Ambiente não autorizado para Google Drive sync.")
        if tenant_allowlist and tenant_slug not in tenant_allowlist:
            return _blocked(side_effect, "drive_sync_tenant_not_allowed", "Tenant não autorizado para Google Drive sync.")
        return _real_enabled(side_effect, "drive_sync_real_enabled", "Google Drive sync habilitado.")

    if side_effect == SideEffectType.EMAIL_NOTIFICATION:
        handoff_enabled = bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED", False))
        handoff_dry = bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True))
        lead_enabled = bool(getattr(settings, "LIVIA_LEAD_NOTIFICATIONS_ENABLED", False))
        lead_dry = bool(getattr(settings, "LIVIA_LEAD_NOTIFICATIONS_DRY_RUN", True))
        operational_enabled = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED", False))
        operational_dry = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN", True))
        if not handoff_enabled and not operational_enabled and not lead_enabled:
            return _blocked(side_effect, "email_notifications_disabled", "Notificações de e-mail desabilitadas.")
        if handoff_dry or operational_dry or lead_dry:
            return _dry_run(side_effect, "email_notifications_dry_run", "Notificações de e-mail em dry-run.")
        return _real_enabled(side_effect, "email_notifications_real_enabled", "Notificações de e-mail reais habilitadas.")

    if side_effect == SideEffectType.WHATSAPP_HANDOFF:
        return _blocked(
            side_effect,
            "whatsapp_handoff_client_side_only",
            "Handoff WhatsApp é client-side (link wa.me), sem envio externo pelo backend.",
        )

    return _blocked(side_effect, "unknown_side_effect", "Tipo de side effect não reconhecido.")


def log_side_effect_decision(
    decision: SideEffectDecision,
    *,
    tenant=None,
    correlation_id: str = "",
    conversation_id=None,
    lead_id=None,
) -> None:
    tenant_id = getattr(tenant, "id", None)
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    logger.info(
        "side_effect_policy tenant_id=%s integration=%s decision=%s code=%s environment=%s correlation_id=%s conversation_id=%s lead_id=%s",
        tenant_id,
        decision.side_effect.value,
        decision.status.value,
        decision.code,
        env,
        str(correlation_id or ""),
        str(conversation_id or ""),
        str(lead_id or ""),
    )


def _csv_set(raw: str) -> set[str]:
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}


def _blocked(side_effect: SideEffectType, code: str, reason: str) -> SideEffectDecision:
    return SideEffectDecision(
        side_effect=side_effect,
        status=SideEffectStatus.BLOCKED,
        allowed=False,
        dry_run=False,
        code=code,
        reason=reason,
    )


def _dry_run(side_effect: SideEffectType, code: str, reason: str) -> SideEffectDecision:
    return SideEffectDecision(
        side_effect=side_effect,
        status=SideEffectStatus.DRY_RUN,
        allowed=True,
        dry_run=True,
        code=code,
        reason=reason,
    )


def _real_enabled(side_effect: SideEffectType, code: str, reason: str) -> SideEffectDecision:
    return SideEffectDecision(
        side_effect=side_effect,
        status=SideEffectStatus.REAL_ENABLED,
        allowed=True,
        dry_run=False,
        code=code,
        reason=reason,
    )
