from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from tenants.models import AssistantProfile, Tenant
from tenants.origins import normalize_origin
from tenants.services.onboarding import DEFAULT_API_URL, DEFAULT_WIDGET_SRC
from tenants.services.widget_config import build_widget_config_for_tenant

SITE_READINESS_READY = "READY"
SITE_READINESS_NOT_READY = "NOT_READY"
SITE_READINESS_WARNING = "WARNING"

CHECK_PASS = "PASS"
CHECK_FAIL = "FAIL"
CHECK_WARN = "WARN"


@dataclass(frozen=True)
class SiteReadinessCheck:
    code: str
    status: str
    message: str
    action: str = ""

    def to_dict(self):
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "action": self.action,
        }


@dataclass(frozen=True)
class SiteReadinessReport:
    tenant_slug: str
    overall_status: str
    checks: list[SiteReadinessCheck] = field(default_factory=list)
    blocking: bool = False

    def to_dict(self):
        return {
            "tenant": self.tenant_slug,
            "readiness": self.overall_status,
            "blocking": self.blocking,
            "checks": [check.to_dict() for check in self.checks],
        }


def inspect_tenant_site_readiness(tenant: Tenant | None, *, tenant_slug: str = "") -> SiteReadinessReport:
    checks: list[SiteReadinessCheck] = []
    slug = tenant_slug or getattr(tenant, "slug", "") or ""

    if tenant is None:
        checks.append(
            SiteReadinessCheck(
                code="tenant_exists",
                status=CHECK_FAIL,
                message="Tenant não encontrado.",
                action="Cadastre o tenant ou confira o slug informado.",
            )
        )
        return _finalize_report(slug, checks)

    checks.append(
        SiteReadinessCheck(
            code="tenant_exists",
            status=CHECK_PASS,
            message="Tenant encontrado.",
        )
    )

    if tenant.is_active:
        checks.append(
            SiteReadinessCheck(
                code="tenant_active",
                status=CHECK_PASS,
                message="Tenant ativo.",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="tenant_active",
                status=CHECK_FAIL,
                message="Tenant inativo.",
                action="Ative o tenant no Django Admin antes de publicar o widget.",
            )
        )

    profile = _load_assistant_profile(tenant)
    if profile is None:
        checks.append(
            SiteReadinessCheck(
                code="assistant_profile_exists",
                status=CHECK_FAIL,
                message="Perfil do assistente não configurado.",
                action="Crie um AssistantProfile para o tenant.",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="assistant_profile_exists",
                status=CHECK_PASS,
                message="Perfil do assistente configurado.",
            )
        )
        checks.extend(_assistant_profile_usability_checks(profile))

    checks.extend(_origin_checks(tenant))
    checks.extend(_endpoint_checks())
    checks.extend(_safe_response_checks(profile))
    checks.extend(_widget_config_checks(tenant))

    return _finalize_report(slug, checks)


def site_readiness_has_blocking_errors(report: SiteReadinessReport) -> bool:
    return report.blocking


def _load_assistant_profile(tenant: Tenant):
    return AssistantProfile.objects.filter(tenant=tenant).first()


def _assistant_profile_usability_checks(profile) -> list[SiteReadinessCheck]:
    checks: list[SiteReadinessCheck] = []
    if profile.is_active and profile.is_widget_enabled:
        checks.append(
            SiteReadinessCheck(
                code="assistant_profile_usable",
                status=CHECK_PASS,
                message="Perfil do assistente utilizável para o widget.",
            )
        )
        return checks

    if not profile.is_active:
        checks.append(
            SiteReadinessCheck(
                code="assistant_profile_usable",
                status=CHECK_FAIL,
                message="Perfil do assistente inativo.",
                action="Ative o perfil do assistente no Django Admin.",
            )
        )
    elif not profile.is_widget_enabled:
        checks.append(
            SiteReadinessCheck(
                code="assistant_profile_usable",
                status=CHECK_FAIL,
                message="Widget desabilitado no perfil do assistente.",
                action="Habilite is_widget_enabled no perfil do assistente.",
            )
        )
    return checks


def _origin_checks(tenant: Tenant) -> list[SiteReadinessCheck]:
    checks: list[SiteReadinessCheck] = []
    origins = list(tenant.allowed_origins.order_by("origin"))
    active_origins = [item.origin for item in origins if item.is_active]

    if active_origins:
        checks.append(
            SiteReadinessCheck(
                code="active_origin_present",
                status=CHECK_PASS,
                message="Há pelo menos uma origin ativa cadastrada.",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="active_origin_present",
                status=CHECK_FAIL,
                message="Nenhuma origin ativa cadastrada.",
                action="Cadastre ao menos uma origin HTTPS do site em TenantAllowedOrigin.",
            )
        )

    invalid_origins: list[str] = []
    permissive_origins: list[str] = []
    http_origins: list[str] = []

    for item in origins:
        raw_origin = str(item.origin or "").strip()
        if not raw_origin:
            invalid_origins.append(raw_origin or "(vazio)")
            continue
        if raw_origin == "*" or "*" in raw_origin:
            permissive_origins.append(raw_origin)
            continue
        try:
            normalized = normalize_origin(raw_origin)
        except ValidationError:
            invalid_origins.append(raw_origin)
            continue
        if normalized.startswith("http://"):
            http_origins.append(normalized)

    if invalid_origins:
        checks.append(
            SiteReadinessCheck(
                code="origin_format_valid",
                status=CHECK_FAIL,
                message="Existem origins inválidas cadastradas.",
                action="Corrija ou remova origins malformadas no Django Admin.",
            )
        )
    elif permissive_origins:
        checks.append(
            SiteReadinessCheck(
                code="origin_format_valid",
                status=CHECK_FAIL,
                message="Existem origins excessivamente permissivas cadastradas.",
                action="Remova wildcards e use apenas scheme + host (+ porta quando necessário).",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="origin_format_valid",
                status=CHECK_PASS,
                message="Todas as origins cadastradas usam http ou https válidos.",
            )
        )

    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    if http_origins and env in {"staging", "production"}:
        checks.append(
            SiteReadinessCheck(
                code="origin_https_recommended",
                status=CHECK_WARN,
                message="Há origins HTTP em ambiente não local.",
                action="Prefira https:// nas origins de staging e production.",
            )
        )
    elif http_origins:
        checks.append(
            SiteReadinessCheck(
                code="origin_https_recommended",
                status=CHECK_WARN,
                message="Há origins HTTP cadastradas.",
                action="Use https:// em produção sempre que possível.",
            )
        )

    return checks


def _endpoint_checks() -> list[SiteReadinessCheck]:
    checks: list[SiteReadinessCheck] = []
    if DEFAULT_WIDGET_SRC.startswith(("http://", "https://")):
        checks.append(
            SiteReadinessCheck(
                code="widget_url_resolvable",
                status=CHECK_PASS,
                message="URL pública do widget.js está definida.",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="widget_url_resolvable",
                status=CHECK_FAIL,
                message="URL pública do widget.js não pôde ser determinada.",
                action="Configure a URL base pública do widget.",
            )
        )

    if DEFAULT_API_URL.startswith(("http://", "https://")):
        checks.append(
            SiteReadinessCheck(
                code="chat_endpoint_resolvable",
                status=CHECK_PASS,
                message="Endpoint público de chat está definido.",
            )
        )
    else:
        checks.append(
            SiteReadinessCheck(
                code="chat_endpoint_resolvable",
                status=CHECK_FAIL,
                message="Endpoint público de chat não pôde ser determinado.",
                action="Configure a URL pública do endpoint /api/chat/.",
            )
        )
    return checks


def _safe_response_checks(profile) -> list[SiteReadinessCheck]:
    if profile is None:
        return []

    ai_enabled_globally = bool(getattr(settings, "LIVIA_AI_ENABLED", False))
    if profile.use_ai and not ai_enabled_globally:
        return [
            SiteReadinessCheck(
                code="safe_response_mode",
                status=CHECK_WARN,
                message="Perfil com use_ai ativo, mas IA global permanece desligada.",
                action="O atendimento segue em modo seguro/determinístico até LIVIA_AI_ENABLED=True.",
            )
        ]

    return [
        SiteReadinessCheck(
            code="safe_response_mode",
            status=CHECK_PASS,
            message="Tenant não depende de integrações reais para responder em modo seguro.",
        )
    ]


def _widget_config_checks(tenant: Tenant) -> list[SiteReadinessCheck]:
    profile = _load_assistant_profile(tenant)
    if profile is None:
        return [
            SiteReadinessCheck(
                code="widget_config_present",
                status=CHECK_FAIL,
                message="Configuração pública obrigatória do widget está incompleta.",
                action="Revise o AssistantProfile e os defaults do widget.",
            )
        ]

    config = build_widget_config_for_tenant(tenant)
    required_fields = ("tenant", "widget_title", "launcher_label", "primary_color", "position")
    missing = [field_name for field_name in required_fields if not str(config.get(field_name, "")).strip()]
    if missing:
        return [
            SiteReadinessCheck(
                code="widget_config_present",
                status=CHECK_FAIL,
                message="Configuração pública obrigatória do widget está incompleta.",
                action="Revise o AssistantProfile e os defaults do widget.",
            )
        ]
    return [
        SiteReadinessCheck(
            code="widget_config_present",
            status=CHECK_PASS,
            message="Configuração pública obrigatória do widget está presente.",
        )
    ]


def _finalize_report(tenant_slug: str, checks: list[SiteReadinessCheck]) -> SiteReadinessReport:
    has_fail = any(check.status == CHECK_FAIL for check in checks)
    has_warn = any(check.status == CHECK_WARN for check in checks)
    if has_fail:
        overall = SITE_READINESS_NOT_READY
    elif has_warn:
        overall = SITE_READINESS_WARNING
    else:
        overall = SITE_READINESS_READY
    return SiteReadinessReport(
        tenant_slug=tenant_slug,
        overall_status=overall,
        checks=checks,
        blocking=has_fail,
    )
