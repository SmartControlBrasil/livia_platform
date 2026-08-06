from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ObjectDoesNotExist

from tenants.models import AssistantProfile, Tenant
from tenants.services.onboarding import (
    DEFAULT_API_URL,
    DEFAULT_WIDGET_SRC,
    build_widget_snippet,
    normalize_allowed_origin,
)
from tenants.services.site_readiness import (
    SITE_READINESS_NOT_READY,
    SITE_READINESS_READY,
    SITE_READINESS_WARNING,
    SiteReadinessReport,
    inspect_tenant_site_readiness,
)
from tenants.services.widget_config import build_widget_config_for_tenant

DEFAULT_INSTALL_BASE_URL = "https://livia.smartcontrolbrasil.com.br"

INSTALL_INSTRUCTIONS = [
    "Copie o snippet do widget abaixo.",
    "Cole antes de </body> no HTML do site do tenant.",
    "Publique o site usando uma origin já cadastrada.",
    "Abra a página publicada e envie uma mensagem curta de teste.",
]


@dataclass(frozen=True)
class TenantInstallPackage:
    tenant: Tenant
    assistant_profile: AssistantProfile | None
    widget_src: str
    api_url: str
    snippet: str
    allowed_origin: str
    allowed_origins: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    install_instructions: list[str] = field(default_factory=list)
    widget_config: dict = field(default_factory=dict)
    readiness: SiteReadinessReport | None = None

    @property
    def readiness_status(self) -> str:
        if self.readiness is None:
            return SITE_READINESS_NOT_READY
        return self.readiness.overall_status

    @property
    def assistant_name(self) -> str:
        if self.assistant_profile is None:
            return "Lívia"
        return self.assistant_profile.name

    @property
    def is_ready_for_install(self) -> bool:
        return self.readiness_status in {SITE_READINESS_READY, SITE_READINESS_WARNING}

    def to_dict(self):
        payload = {
            "tenant": self.tenant.slug,
            "name": self.tenant.name,
            "assistant_name": self.assistant_name,
            "is_active": self.tenant.is_active,
            "readiness": self.readiness_status,
            "readiness_checks": [check.to_dict() for check in (self.readiness.checks if self.readiness else [])],
            "widget_src": self.widget_src,
            "api_url": self.api_url,
            "snippet": self.snippet,
            "allowed_origin": self.allowed_origin,
            "allowed_origins": self.allowed_origins,
            "warnings": self.warnings,
            "install_instructions": self.install_instructions,
            "widget_config": self.widget_config,
        }
        return payload


def build_install_url(tenant_slug, base_url=DEFAULT_INSTALL_BASE_URL):
    return f"{str(base_url).rstrip('/')}/install/{tenant_slug}/"


class TenantInstallPackageService:
    def build_for_slug(self, tenant_slug):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise Tenant.DoesNotExist
        return self.build_for_tenant(tenant)

    def build_for_tenant(self, tenant):
        readiness = inspect_tenant_site_readiness(tenant)
        warnings = _warnings_from_readiness(readiness)
        allowed_origins = list(
            tenant.allowed_origins.filter(is_active=True).order_by("origin").values_list("origin", flat=True)
        )
        allowed_origin = allowed_origins[0] if allowed_origins else ""

        if tenant.domain:
            try:
                _domain_origin, origin_warnings = normalize_allowed_origin(tenant.domain, required=False)
                warnings.extend(origin_warnings)
            except ValueError:
                warnings.append("O domínio cadastrado não parece ser uma URL válida.")

        try:
            assistant_profile = tenant.assistant_profile
        except ObjectDoesNotExist:
            assistant_profile = None

        return TenantInstallPackage(
            tenant=tenant,
            assistant_profile=assistant_profile,
            widget_src=DEFAULT_WIDGET_SRC,
            api_url=DEFAULT_API_URL,
            snippet=build_widget_snippet(tenant.slug, api_url=DEFAULT_API_URL, widget_src=DEFAULT_WIDGET_SRC),
            allowed_origin=allowed_origin,
            allowed_origins=allowed_origins,
            warnings=_dedupe_warnings(warnings),
            install_instructions=list(INSTALL_INSTRUCTIONS),
            widget_config=build_widget_config_for_tenant(tenant),
            readiness=readiness,
        )


def _warnings_from_readiness(readiness: SiteReadinessReport) -> list[str]:
    warnings: list[str] = []
    for check in readiness.checks:
        if check.status in {"FAIL", "WARN"} and check.message:
            warnings.append(check.message)
    return warnings


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        unique.append(warning)
    return unique
