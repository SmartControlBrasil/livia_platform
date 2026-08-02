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
from tenants.services.widget_config import build_widget_config_for_tenant

DEFAULT_INSTALL_BASE_URL = "https://livia.smartcontrolbrasil.com.br"


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

    def to_dict(self):
        return {
            "tenant": self.tenant.slug,
            "name": self.tenant.name,
            "is_active": self.tenant.is_active,
            "domain": self.tenant.domain,
            "widget_src": self.widget_src,
            "api_url": self.api_url,
            "snippet": self.snippet,
            "allowed_origin": self.allowed_origin,
            "allowed_origins": self.allowed_origins,
            "warnings": self.warnings,
            "widget_config": self.widget_config,
        }


def build_install_url(tenant_slug, base_url=DEFAULT_INSTALL_BASE_URL):
    return f"{str(base_url).rstrip('/')}/install/{tenant_slug}/"


class TenantInstallPackageService:
    def build_for_slug(self, tenant_slug):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise Tenant.DoesNotExist
        return self.build_for_tenant(tenant)

    def build_for_tenant(self, tenant):
        warnings = []
        allowed_origins = list(tenant.allowed_origins.filter(is_active=True).order_by("origin").values_list("origin", flat=True))
        allowed_origin = allowed_origins[0] if allowed_origins else ""
        if not allowed_origins:
            warnings.append("Este tenant ainda não tem origins autorizadas ativas.")
        if tenant.domain:
            try:
                _domain_origin, origin_warnings = normalize_allowed_origin(tenant.domain, required=False)
                warnings.extend(origin_warnings)
            except ValueError:
                warnings.append("O domínio cadastrado não parece ser uma URL válida.")

        if not tenant.is_active:
            warnings.append("Este tenant está inativo. O widget não processará atendimentos até ser ativado.")

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
            warnings=warnings,
            install_instructions=[
                "Copie o snippet do widget.",
                "Cole antes de </body> no site do tenant.",
                "Publique o site.",
                "Teste abrindo a página e enviando uma mensagem curta.",
            ],
            widget_config=build_widget_config_for_tenant(tenant),
        )
