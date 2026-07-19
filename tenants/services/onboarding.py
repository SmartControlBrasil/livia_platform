from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Q

from tenants.origins import normalize_origin

from knowledge_base.models import KnowledgeDocument
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin

DEFAULT_WIDGET_SRC = "https://livia.smartcontrolbrasil.com.br/widget.js"
DEFAULT_API_URL = "https://livia.smartcontrolbrasil.com.br/api/chat/"


@dataclass
class TenantOnboardingResult:
    tenant: Tenant
    assistant_profile: AssistantProfile
    created_tenant: bool
    created_profile: bool
    created_knowledge_count: int
    widget_snippet: str
    allowed_origin: str
    allowed_origins: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_widget_snippet(tenant_slug, api_url=None, widget_src=None):
    widget_src = widget_src or DEFAULT_WIDGET_SRC
    api_url = api_url or DEFAULT_API_URL
    return "\n".join([
        "<script",
        f'  src="{widget_src}"',
        f'  data-tenant="{tenant_slug}"',
        f'  data-api-url="{api_url}">',
        "</script>",
    ])


def normalize_allowed_origin(domain, *, required=True):
    raw_domain = str(domain or "").strip()
    warnings = []
    if not raw_domain:
        if required:
            raise ValueError("Domain is required for tenant onboarding.")
        return "", warnings

    candidate = raw_domain.rstrip("/")
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
        warnings.append("Domain did not include a scheme; https:// was assumed.")

    try:
        allowed_origin = normalize_origin(candidate)
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    parsed = urlparse(allowed_origin)
    host = parsed.hostname or ""
    if parsed.scheme == "http":
        warnings.append("Domain uses http; production embeds should use https.")
    if host in {"localhost", "127.0.0.1"} or host.endswith(".localhost"):
        warnings.append("Domain appears to be localhost; avoid this for production onboarding.")

    return allowed_origin, warnings


class TenantOnboardingService:
    def onboard(
        self,
        *,
        slug,
        name,
        domain,
        assistant_name="Lívia",
        initial_message="Olá! Sou a Lívia. Como posso te ajudar?",
        primary_goal="qualificar leads",
        tone="consultivo, claro e profissional",
        use_ai=False,
        widget_title="",
        launcher_label="Fale com a Lívia",
        primary_color="#2563eb",
        position="bottom_right",
        placeholder_text="Digite sua mensagem...",
        widget_enabled=True,
        seed_knowledge=False,
        dry_run=False,
        allowed_origins=None,
    ):
        allowed_origin, warnings = normalize_allowed_origin(domain)
        explicit_origins = list(allowed_origins or [])
        origin_values = explicit_origins or [allowed_origin]
        normalized_origins = []
        for origin_value in origin_values:
            normalized = normalize_origin(origin_value)
            if normalized not in normalized_origins:
                normalized_origins.append(normalized)
        tenant_domain = allowed_origin
        existing_tenant = Tenant.objects.filter(slug=slug).first()
        created_tenant = existing_tenant is None

        if dry_run:
            tenant = existing_tenant or Tenant(slug=slug)
            tenant.name = name
            tenant.domain = tenant_domain
            tenant.is_active = True
            existing_profile = None
            if existing_tenant is not None:
                existing_profile = AssistantProfile.objects.filter(tenant=existing_tenant).first()
            created_profile = existing_profile is None
            assistant_profile = existing_profile or AssistantProfile(tenant=tenant)
            assistant_profile.name = assistant_name
            assistant_profile.initial_message = initial_message
            assistant_profile.tone = tone
            assistant_profile.primary_goal = primary_goal
            assistant_profile.use_ai = use_ai
            assistant_profile.widget_title = widget_title
            assistant_profile.launcher_label = launcher_label
            assistant_profile.primary_color = primary_color
            assistant_profile.position = position
            assistant_profile.placeholder_text = placeholder_text
            assistant_profile.is_widget_enabled = widget_enabled
            assistant_profile.is_active = True
            clean_excludes = ["tenant"] if tenant.pk is None else None
            assistant_profile.full_clean(exclude=clean_excludes)
            created_knowledge_count = 0
            if seed_knowledge and not self._knowledge_exists(existing_tenant, name):
                created_knowledge_count = 1
        else:
            with transaction.atomic():
                tenant, created_tenant = Tenant.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "name": name,
                        "domain": tenant_domain,
                        "is_active": True,
                    },
                )
                assistant_profile = AssistantProfile.objects.filter(tenant=tenant).first()
                created_profile = assistant_profile is None
                if assistant_profile is None:
                    assistant_profile = AssistantProfile(tenant=tenant)
                assistant_profile.name = assistant_name
                assistant_profile.initial_message = initial_message
                assistant_profile.tone = tone
                assistant_profile.primary_goal = primary_goal
                assistant_profile.use_ai = use_ai
                assistant_profile.widget_title = widget_title
                assistant_profile.launcher_label = launcher_label
                assistant_profile.primary_color = primary_color
                assistant_profile.position = position
                assistant_profile.placeholder_text = placeholder_text
                assistant_profile.is_widget_enabled = widget_enabled
                assistant_profile.is_active = True
                assistant_profile.full_clean()
                assistant_profile.save()
                created_knowledge_count = 0
                if seed_knowledge:
                    created_knowledge_count = self._seed_base_knowledge(tenant, name, primary_goal, allowed_origin)
                for origin in normalized_origins:
                    TenantAllowedOrigin.objects.update_or_create(
                        tenant=tenant,
                        origin=origin,
                        defaults={"is_active": True},
                    )

        if use_ai:
            warnings.append("Profile use_ai is enabled; real AI still requires LIVIA_AI_ENABLED=True globally.")

        return TenantOnboardingResult(
            tenant=tenant,
            assistant_profile=assistant_profile,
            created_tenant=created_tenant,
            created_profile=created_profile,
            created_knowledge_count=created_knowledge_count,
            widget_snippet=build_widget_snippet(slug),
            allowed_origin=allowed_origin,
            allowed_origins=normalized_origins,
            warnings=warnings,
        )

    def _knowledge_exists(self, tenant, name):
        if tenant is None or tenant.pk is None:
            return False
        return KnowledgeDocument.objects.filter(tenant=tenant, title=f"Sobre {name}").exists()

    def _seed_base_knowledge(self, tenant, name, primary_goal, allowed_origin):
        title = f"Sobre {name}"
        content = (
            f"{name} é um cliente atendido pela Lívia Platform. "
            f"O objetivo principal da assistente é {primary_goal}. "
            f"Use este documento como base institucional inicial e complemente com informações reais do negócio. "
            f"Domínio informado para o widget: {allowed_origin}."
        )
        document_slug = f"sobre-{tenant.slug}"
        existing = KnowledgeDocument.objects.filter(tenant=tenant).filter(
            Q(title=title) | Q(slug=document_slug)
        ).first()
        defaults = {
            "title": title,
            "content": content,
            "source_type": "manual",
            "source_url": "",
            "tags": ["institucional", "onboarding"],
            "status": KnowledgeDocument.Status.ACTIVE,
        }
        if existing is not None:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.save(update_fields=[*defaults.keys(), "updated_at"])
            return 0

        KnowledgeDocument.objects.create(
            tenant=tenant,
            slug=document_slug,
            **defaults,
        )
        return 1
