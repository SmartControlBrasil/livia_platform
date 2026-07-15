from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from tenants.models import AssistantProfile, Tenant
from tenants.services.onboarding import (
    DEFAULT_API_URL,
    DEFAULT_WIDGET_SRC,
    build_widget_snippet,
    normalize_allowed_origin,
)

DEFAULT_INSTALL_BASE_URL = "https://livia.smartcontrolbrasil.com.br"


@dataclass(frozen=True)
class TenantInstallPackage:
    tenant: Tenant
    assistant_profile: AssistantProfile | None
    widget_src: str
    api_url: str
    snippet: str
    allowed_origin: str
    warnings: list[str] = field(default_factory=list)
    install_instructions: list[str] = field(default_factory=list)

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
            "warnings": self.warnings,
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
        allowed_origin = ""
        if tenant.domain:
            try:
                allowed_origin, origin_warnings = normalize_allowed_origin(tenant.domain, required=False)
                warnings.extend(origin_warnings)
            except ValueError:
                warnings.append("O domínio cadastrado não parece ser uma URL válida.")
        else:
            warnings.append("Este tenant ainda não tem domínio/origin configurado.")

        configured_origins = set(getattr(settings, "LIVIA_ALLOWED_WIDGET_ORIGINS", []) or [])
        if allowed_origin and configured_origins and allowed_origin not in configured_origins:
            warnings.append("O domínio/origin deste tenant não está em LIVIA_ALLOWED_WIDGET_ORIGINS.")

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
            warnings=warnings,
            install_instructions=[
                "Copie o snippet do widget.",
                "Cole antes de </body> no site do tenant.",
                "Publique o site.",
                "Teste abrindo a página e enviando uma mensagem curta.",
            ],
        )
