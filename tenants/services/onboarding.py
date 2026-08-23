from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.text import slugify

from audit.models import (
    ACTION_TENANT_CREATED,
    ACTION_TENANT_ORIGIN_CREATED,
    ACTION_TENANT_ORIGIN_DEACTIVATED,
    ACTION_TENANT_UPDATED,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from knowledge_base.models import KnowledgeDocument, TenantRagChunkEmbedding, TenantRagDocumentChunk
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.origins import normalize_origin

DEFAULT_WIDGET_SRC = "https://livia.smartcontrolbrasil.com.br/widget.js"
DEFAULT_API_URL = "https://livia.smartcontrolbrasil.com.br/api/chat/"

ONBOARDING_CREATED = "CREATED"
ONBOARDING_UPDATED = "UPDATED"
ONBOARDING_UNCHANGED = "UNCHANGED"
ONBOARDING_CONFLICT = "CONFLICT"

KNOWLEDGE_EMPTY = "EMPTY"
KNOWLEDGE_AVAILABLE = "AVAILABLE"
KNOWLEDGE_INDEXED = "INDEXED"

ACTION_TENANT_ONBOARDING_COMPLETED = "tenant.onboarding_completed"
ACTION_TENANT_ONBOARDING_FAILED = "tenant.onboarding_failed"

TENANT_AUDIT_FIELDS = ["name", "slug", "domain", "is_active"]
PROFILE_AUDIT_FIELDS = [
    "name",
    "business_name",
    "business_domain",
    "short_description",
    "primary_goal",
    "tone",
    "initial_message",
    "widget_title",
    "launcher_label",
    "primary_color",
    "position",
    "placeholder_text",
    "show_branding",
    "is_widget_enabled",
    "use_ai",
    "is_active",
]
ORIGIN_AUDIT_FIELDS = ["origin", "is_active"]


class TenantOnboardingConflict(ValueError):
    pass


@dataclass(frozen=True)
class TenantOnboardingSpec:
    slug: str
    name: str
    domain: str = ""
    origins: list[str] = field(default_factory=list)
    assistant_name: str = "Lívia"
    initial_message: str = "Olá! Sou a Lívia. Como posso te ajudar?"
    primary_goal: str = "qualificar leads"
    tone: str = "consultivo, claro e profissional"
    business_name: str = ""
    business_domain: str = ""
    short_description: str = ""
    use_ai: bool = False
    widget_title: str = ""
    launcher_label: str = "Fale com a Lívia"
    primary_color: str = "#2563eb"
    position: str = "bottom_right"
    placeholder_text: str = "Digite sua mensagem..."
    widget_enabled: bool = False
    show_branding: bool = True
    tenant_active: bool = True
    seed_knowledge: bool = False
    dry_run: bool = False
    allow_update_existing: bool = True
    deactivate_missing_origins: bool = False
    actor: object | None = None
    request: object | None = None
    source: str = "tenant_onboarding_service"


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
    origins_added: list[str] = field(default_factory=list)
    origins_existing: list[str] = field(default_factory=list)
    origins_removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = ONBOARDING_UNCHANGED
    readiness: object | None = None
    install_package: object | None = None
    components: dict = field(default_factory=dict)
    knowledge_status: str = KNOWLEDGE_EMPTY
    dry_run: bool = False

    @property
    def readiness_status(self):
        if self.readiness is None:
            return "NOT_READY"
        return getattr(self.readiness, "overall_status", "NOT_READY")


def build_widget_snippet(tenant_slug, api_url=None, widget_src=None, *, include_api_url=True):
    widget_src = widget_src or DEFAULT_WIDGET_SRC
    api_url = api_url or DEFAULT_API_URL
    lines = [
        "<script",
        f'  src="{widget_src}"',
        f'  data-tenant="{tenant_slug}"',
    ]
    if include_api_url:
        lines.append(f'  data-api-url="{api_url}"')
    lines.extend(["  defer>", "</script>"])
    return "\n".join(lines)


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


def _normalize_spec(spec=None, **kwargs):
    if isinstance(spec, TenantOnboardingSpec):
        return spec
    if spec is not None:
        raise TypeError("spec must be TenantOnboardingSpec when provided.")
    if "allowed_origins" in kwargs and "origins" not in kwargs:
        kwargs["origins"] = kwargs.pop("allowed_origins") or []
    return TenantOnboardingSpec(**kwargs)


class TenantOnboardingService:
    def onboard(self, spec: TenantOnboardingSpec | None = None, **kwargs):
        spec = _normalize_spec(spec, **kwargs)
        return self._onboard(spec)

    def _onboard(self, spec: TenantOnboardingSpec):
        slug = self._clean_slug(spec.slug)
        name = str(spec.name or "").strip()
        if not name:
            raise ValidationError("Tenant name is required.")

        allowed_origin, warnings = normalize_allowed_origin(spec.domain or (spec.origins[0] if spec.origins else ""))
        normalized_origins = self._normalize_origins(spec.origins or [allowed_origin])
        if allowed_origin not in normalized_origins:
            normalized_origins.insert(0, allowed_origin)

        existing_tenant = Tenant.objects.filter(slug=slug).first()
        if existing_tenant is not None and not spec.allow_update_existing:
            exc = TenantOnboardingConflict(f"Tenant {slug} already exists.")
            self._record_failure_audit(spec=spec, slug=slug, error=exc)
            raise exc

        if spec.dry_run:
            return self._build_dry_run_result(spec, slug, name, allowed_origin, normalized_origins, warnings, existing_tenant)

        try:
            with transaction.atomic():
                tenant = Tenant.objects.select_for_update().filter(slug=slug).first()
                if tenant is not None and not spec.allow_update_existing:
                    raise TenantOnboardingConflict(f"Tenant {slug} already exists.")
                created_tenant = tenant is None
                tenant_before = audit_model_snapshot(tenant, TENANT_AUDIT_FIELDS) if tenant is not None else {}
                if tenant is None:
                    tenant = Tenant(slug=slug)
                tenant.name = name
                tenant.domain = allowed_origin
                tenant.is_active = bool(spec.tenant_active)
                tenant.full_clean()
                tenant.save()

                profile = AssistantProfile.objects.select_for_update().filter(tenant=tenant).first()
                created_profile = profile is None
                profile_before = audit_model_snapshot(profile, PROFILE_AUDIT_FIELDS) if profile is not None else {}
                if profile is None:
                    profile = AssistantProfile(tenant=tenant)
                self._apply_profile(profile, spec)
                profile.full_clean()
                profile.save()

                created_knowledge_count = 0
                if spec.seed_knowledge:
                    created_knowledge_count = self._seed_base_knowledge(
                        tenant,
                        name,
                        spec.primary_goal,
                        allowed_origin,
                        spec.business_domain,
                        spec.short_description,
                    )

                origin_changes = self._sync_origins(
                    tenant=tenant,
                    origins=normalized_origins,
                    actor=spec.actor,
                    request=spec.request,
                    source=spec.source,
                    deactivate_missing=spec.deactivate_missing_origins,
                )

                self._record_core_audit(
                    tenant=tenant,
                    profile=profile,
                    created_tenant=created_tenant,
                    created_profile=created_profile,
                    tenant_before=tenant_before,
                    profile_before=profile_before,
                    actor=spec.actor,
                    request=spec.request,
                    source=spec.source,
                )
                status = self._status_from_changes(
                    created_tenant=created_tenant,
                    created_profile=created_profile,
                    tenant_before=tenant_before,
                    tenant=tenant,
                    profile_before=profile_before,
                    profile=profile,
                    origin_changes=origin_changes,
                    created_knowledge_count=created_knowledge_count,
                )
                record_audit_event(
                    action=ACTION_TENANT_ONBOARDING_COMPLETED,
                    actor=spec.actor,
                    tenant=tenant,
                    obj=tenant,
                    before_data={},
                    after_data={"status": status, "readiness": "pending", "origins": normalized_origins},
                    metadata={"source": spec.source, "dry_run": False},
                    request=spec.request,
                )
        except Exception as exc:
            self._record_failure_audit(spec=spec, slug=slug, error=exc)
            raise

        return self._build_persisted_result(
            tenant=tenant,
            assistant_profile=profile,
            created_tenant=created_tenant,
            created_profile=created_profile,
            created_knowledge_count=created_knowledge_count,
            allowed_origin=allowed_origin,
            allowed_origins=normalized_origins,
            origin_changes=self._result_origin_changes(origin_changes),
            warnings=warnings,
            status=status,
        )

    def _build_dry_run_result(self, spec, slug, name, allowed_origin, normalized_origins, warnings, existing_tenant):
        tenant = existing_tenant or Tenant(slug=slug)
        tenant.name = name
        tenant.domain = allowed_origin
        tenant.is_active = bool(spec.tenant_active)
        tenant.full_clean(exclude=None if tenant.pk else ["id"])
        existing_profile = AssistantProfile.objects.filter(tenant=existing_tenant).first() if existing_tenant else None
        profile = existing_profile or AssistantProfile(tenant=tenant)
        self._apply_profile(profile, spec)
        profile.full_clean(exclude=["tenant"] if tenant.pk is None else None)
        created_knowledge_count = 1 if spec.seed_knowledge and not self._knowledge_exists(existing_tenant, name) else 0
        status = ONBOARDING_CREATED if existing_tenant is None else ONBOARDING_UPDATED
        from tenants.services.site_readiness import inspect_tenant_site_readiness

        readiness = inspect_tenant_site_readiness(existing_tenant, tenant_slug=slug)
        origin_changes = self._preview_origin_changes(existing_tenant, normalized_origins, spec.deactivate_missing_origins)
        return TenantOnboardingResult(
            tenant=tenant,
            assistant_profile=profile,
            created_tenant=existing_tenant is None,
            created_profile=existing_profile is None,
            created_knowledge_count=created_knowledge_count,
            widget_snippet=build_widget_snippet(slug),
            allowed_origin=allowed_origin,
            allowed_origins=normalized_origins,
            origins_added=origin_changes["added"],
            origins_existing=origin_changes["existing"],
            origins_removed=origin_changes["removed"],
            warnings=warnings,
            status=status,
            readiness=readiness,
            install_package=None,
            components=self._components(status, existing_tenant is None, existing_profile is None, bool(normalized_origins)),
            knowledge_status=KNOWLEDGE_AVAILABLE if created_knowledge_count else self._knowledge_status(existing_tenant),
            dry_run=True,
        )

    def _build_persisted_result(self, *, tenant, assistant_profile, created_tenant, created_profile, created_knowledge_count, allowed_origin, allowed_origins, origin_changes, warnings, status):
        from tenants.services.install_package import TenantInstallPackageService

        from tenants.services.site_readiness import inspect_tenant_site_readiness

        readiness = inspect_tenant_site_readiness(tenant)
        install_package = TenantInstallPackageService().build_for_tenant(tenant)
        return TenantOnboardingResult(
            tenant=tenant,
            assistant_profile=assistant_profile,
            created_tenant=created_tenant,
            created_profile=created_profile,
            created_knowledge_count=created_knowledge_count,
            widget_snippet=install_package.snippet,
            allowed_origin=allowed_origin,
            allowed_origins=allowed_origins,
            origins_added=origin_changes["added"],
            origins_existing=origin_changes["existing"],
            origins_removed=origin_changes["removed"],
            warnings=[*warnings, *install_package.warnings],
            status=status,
            readiness=readiness,
            install_package=install_package,
            components=self._components(status, created_tenant, created_profile, bool(allowed_origins)),
            knowledge_status=self._knowledge_status(tenant),
            dry_run=False,
        )

    def _clean_slug(self, value):
        slug = str(value or "").strip()
        if not slug or slugify(slug) != slug:
            raise ValidationError("Tenant slug must be a valid normalized slug.")
        return slug

    def _normalize_origins(self, origins):
        normalized = []
        for origin_value in origins:
            normalized_origin = normalize_origin(origin_value)
            if normalized_origin not in normalized:
                normalized.append(normalized_origin)
        if not normalized:
            raise ValidationError("At least one origin is required.")
        return normalized

    def _apply_profile(self, profile, spec):
        profile.name = spec.assistant_name
        profile.initial_message = spec.initial_message
        profile.tone = spec.tone
        profile.primary_goal = spec.primary_goal
        profile.business_name = spec.business_name
        profile.business_domain = spec.business_domain
        profile.short_description = spec.short_description
        profile.use_ai = bool(spec.use_ai)
        profile.widget_title = spec.widget_title
        profile.launcher_label = spec.launcher_label
        profile.primary_color = spec.primary_color
        profile.position = spec.position
        profile.placeholder_text = spec.placeholder_text
        profile.show_branding = bool(spec.show_branding)
        profile.is_widget_enabled = bool(spec.widget_enabled)
        profile.is_active = True

    def _sync_origins(self, *, tenant, origins, actor, request, source, deactivate_missing):
        changes = {"created": [], "reactivated": [], "existing": [], "deactivated": []}
        existing = {item.origin: item for item in tenant.allowed_origins.select_for_update().all()}
        for origin in origins:
            item = existing.get(origin)
            if item is None:
                try:
                    item = TenantAllowedOrigin.objects.create(tenant=tenant, origin=origin, is_active=True, created_by=actor if getattr(actor, "pk", None) else None)
                except IntegrityError:
                    item = TenantAllowedOrigin.objects.select_for_update().get(tenant=tenant, origin=origin)
                    if not item.is_active:
                        item.is_active = True
                        item.save(update_fields=["is_active", "updated_at"])
                changes["created"].append(origin)
                record_audit_event(
                    action=ACTION_TENANT_ORIGIN_CREATED,
                    actor=actor,
                    tenant=tenant,
                    obj=item,
                    before_data={},
                    after_data=audit_model_snapshot(item, ORIGIN_AUDIT_FIELDS),
                    metadata={"source": source},
                    request=request,
                )
                continue
            if not item.is_active:
                before = audit_model_snapshot(item, ORIGIN_AUDIT_FIELDS)
                item.is_active = True
                item.save(update_fields=["is_active", "updated_at"])
                changes["reactivated"].append(origin)
                record_audit_event(
                    action=ACTION_TENANT_ORIGIN_CREATED,
                    actor=actor,
                    tenant=tenant,
                    obj=item,
                    before_data=before,
                    after_data=audit_model_snapshot(item, ORIGIN_AUDIT_FIELDS),
                    metadata={"source": source, "reactivated": True},
                    request=request,
                )
            else:
                changes["existing"].append(origin)
        if deactivate_missing:
            desired = set(origins)
            for origin, item in existing.items():
                if origin in desired or not item.is_active:
                    continue
                before = audit_model_snapshot(item, ORIGIN_AUDIT_FIELDS)
                item.is_active = False
                item.save(update_fields=["is_active", "updated_at"])
                changes["deactivated"].append(origin)
                record_audit_event(
                    action=ACTION_TENANT_ORIGIN_DEACTIVATED,
                    actor=actor,
                    tenant=tenant,
                    obj=item,
                    before_data=before,
                    after_data=audit_model_snapshot(item, ORIGIN_AUDIT_FIELDS),
                    metadata={"source": source},
                    request=request,
                )
        return changes

    def _preview_origin_changes(self, tenant, origins, deactivate_missing):
        changes = {"added": [], "existing": [], "removed": []}
        existing = {}
        if tenant is not None and tenant.pk is not None:
            existing = {item.origin: item for item in tenant.allowed_origins.all()}
        for origin in origins:
            item = existing.get(origin)
            if item is not None and item.is_active:
                changes["existing"].append(origin)
            else:
                changes["added"].append(origin)
        if deactivate_missing:
            desired = set(origins)
            changes["removed"] = [
                origin
                for origin, item in existing.items()
                if origin not in desired and item.is_active
            ]
        return changes

    def _result_origin_changes(self, changes):
        return {
            "added": [*changes.get("created", []), *changes.get("reactivated", [])],
            "existing": changes.get("existing", []),
            "removed": changes.get("deactivated", []),
        }

    def _record_core_audit(self, *, tenant, profile, created_tenant, created_profile, tenant_before, profile_before, actor, request, source):
        tenant_after = audit_model_snapshot(tenant, TENANT_AUDIT_FIELDS)
        if created_tenant:
            record_audit_event(action=ACTION_TENANT_CREATED, actor=actor, tenant=tenant, obj=tenant, before_data={}, after_data=tenant_after, metadata={"source": source}, request=request)
        else:
            changes = changed_fields(tenant_before, tenant_after)
            if changes["before"] or changes["after"]:
                record_audit_event(action=ACTION_TENANT_UPDATED, actor=actor, tenant=tenant, obj=tenant, before_data=changes["before"], after_data=changes["after"], metadata={"source": source}, request=request)
        profile_after = audit_model_snapshot(profile, PROFILE_AUDIT_FIELDS)
        if created_profile or changed_fields(profile_before, profile_after)["after"]:
            record_audit_event(action="assistant_profile.updated", actor=actor, tenant=tenant, obj=profile, before_data=profile_before, after_data=profile_after, metadata={"source": source, "created": created_profile}, request=request)

    def _record_failure_audit(self, *, spec, slug, error):
        record_audit_event(
            action=ACTION_TENANT_ONBOARDING_FAILED,
            actor=spec.actor,
            object_type="Tenant",
            object_id=slug,
            object_repr=slug,
            before_data={},
            after_data={"status": ONBOARDING_CONFLICT, "slug": slug},
            metadata={"source": spec.source, "error_type": type(error).__name__},
            request=spec.request,
        )

    def _status_from_changes(self, *, created_tenant, created_profile, tenant_before, tenant, profile_before, profile, origin_changes, created_knowledge_count):
        if created_tenant:
            return ONBOARDING_CREATED
        tenant_changed = bool(changed_fields(tenant_before, audit_model_snapshot(tenant, TENANT_AUDIT_FIELDS))["after"])
        profile_changed = bool(changed_fields(profile_before, audit_model_snapshot(profile, PROFILE_AUDIT_FIELDS))["after"])
        origins_changed = any(
            origin_changes.get(key)
            for key in ("created", "reactivated", "deactivated")
        )
        if created_profile or tenant_changed or profile_changed or origins_changed or created_knowledge_count:
            return ONBOARDING_UPDATED
        return ONBOARDING_UNCHANGED

    def _components(self, status, created_tenant, created_profile, origins_present):
        return {
            "tenant": "created" if created_tenant else ("updated" if status == ONBOARDING_UPDATED else "unchanged"),
            "assistant_profile": "created" if created_profile else ("updated" if status == ONBOARDING_UPDATED else "unchanged"),
            "origins": "configured" if origins_present else "missing",
            "widget_config": "configured",
        }

    def _knowledge_status(self, tenant):
        if tenant is None or tenant.pk is None:
            return KNOWLEDGE_EMPTY
        from knowledge_base.services.lifecycle import KnowledgeLifecycleService

        readiness = KnowledgeLifecycleService().readiness(tenant=tenant)
        if readiness.status == "READY":
            return KNOWLEDGE_INDEXED
        if readiness.status == "EMPTY":
            return KNOWLEDGE_EMPTY
        return readiness.status

    def _knowledge_exists(self, tenant, name):
        if tenant is None or tenant.pk is None:
            return False
        return KnowledgeDocument.objects.filter(tenant=tenant, title=f"Sobre {name}").exists()

    def _seed_base_knowledge(self, tenant, name, primary_goal, allowed_origin, business_domain="", short_description=""):
        content = (
            f"{name} é um cliente atendido pela Lívia Platform. "
            f"O objetivo principal da assistente é {primary_goal}. "
            f"Domínio de atuação configurado: {business_domain or 'não informado'}. "
            f"Descrição curta configurada: {short_description or 'não informada'}. "
            f"Use este documento como base institucional inicial e complemente com informações reais do negócio. "
            f"Domínio informado para o widget: {allowed_origin}."
        )
        from knowledge_base.services.lifecycle import IMPORT_CREATED, KnowledgeLifecycleService

        result = KnowledgeLifecycleService().upsert_document(
            tenant=tenant,
            title=f"Sobre {name}",
            slug=f"sobre-{tenant.slug}",
            content=content,
            source_type="manual",
            source_url="",
            tags=["institucional", "onboarding"],
            status=KnowledgeDocument.Status.ACTIVE,
            source="tenant_onboarding.seed_knowledge",
        )
        return 1 if result.status == IMPORT_CREATED else 0
