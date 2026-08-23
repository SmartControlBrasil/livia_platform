from __future__ import annotations

import json
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone

from audit.services import record_audit_event
from operations_portal.operational_readiness import (
    STATUS_DEGRADED,
    STATUS_MAINTENANCE,
    STATUS_NOT_READY,
    STATUS_READY,
    STATUS_WARNING,
    TenantOperationalReadinessService,
)
from tenants.models import Tenant
from tenants.origins import normalize_origin, validate_origin_for_tenant
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.site_readiness import SITE_READINESS_READY

ROLLOUT_STATUS_PLANNED = "PLANNED"
ROLLOUT_STATUS_READY = "READY"
ROLLOUT_STATUS_BLOCKED = "BLOCKED"
ROLLOUT_STATUS_VALIDATED = "VALIDATED"
ROLLOUT_STATUS_FAILED = "FAILED"
ROLLOUT_STATUS_ROLLED_BACK = "ROLLED_BACK"

ENVIRONMENT_STAGING = "staging"
ENVIRONMENT_PRODUCTION = "production"
ALLOWED_ENVIRONMENTS = {ENVIRONMENT_STAGING, ENVIRONMENT_PRODUCTION}

ACTION_ROLLOUT_PLANNED = "rollout_planned"
ACTION_ROLLOUT_PREFLIGHT_PASSED = "rollout_preflight_passed"
ACTION_ROLLOUT_PREFLIGHT_BLOCKED = "rollout_preflight_blocked"
ACTION_ROLLOUT_SMOKE_STARTED = "rollout_smoke_started"
ACTION_ROLLOUT_SMOKE_PASSED = "rollout_smoke_passed"
ACTION_ROLLOUT_SMOKE_FAILED = "rollout_smoke_failed"
ACTION_ROLLOUT_ROLLBACK_PLANNED = "rollout_rollback_planned"


@dataclass(frozen=True)
class TenantRolloutSpec:
    tenant: Tenant
    target_origin: str
    environment: str = ENVIRONMENT_STAGING
    dry_run: bool = True
    allow_widget_disabled: bool = False
    allow_knowledge_warning: bool = False

    def normalized_environment(self) -> str:
        return str(self.environment or "").strip().lower()


@dataclass(frozen=True)
class RolloutCheck:
    code: str
    status: str
    detail: str
    blocking: bool = False

    def as_dict(self):
        return {"code": self.code, "status": self.status, "detail": self.detail, "blocking": self.blocking}


@dataclass(frozen=True)
class RolloutInstallPlan:
    target_site: str
    target_origin: str
    snippet: str
    config_endpoint: str
    chat_endpoint: str
    widget_src: str
    widget_position: str
    expected_tenant: str
    allowed_origins: list[str]
    public_config: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "target_site": self.target_site,
            "target_origin": self.target_origin,
            "snippet": self.snippet,
            "config_endpoint": self.config_endpoint,
            "chat_endpoint": self.chat_endpoint,
            "widget_src": self.widget_src,
            "widget_position": self.widget_position,
            "expected_tenant": self.expected_tenant,
            "allowed_origins": self.allowed_origins,
            "public_config": self.public_config,
        }


@dataclass(frozen=True)
class RolloutSmokeStep:
    method: str
    path: str
    code: str
    expected: str

    def as_dict(self):
        return {"method": self.method, "path": self.path, "code": self.code, "expected": self.expected}


@dataclass(frozen=True)
class RolloutRollbackStep:
    code: str
    instruction: str
    safety: str

    def as_dict(self):
        return {"code": self.code, "instruction": self.instruction, "safety": self.safety}


@dataclass(frozen=True)
class RolloutSmokeResult:
    widget_js: bool = False
    config: bool = False
    preflight: bool = False
    cors_options: bool = False
    chat: bool = False
    response_contract: bool = False
    side_effects_safe: bool = False
    rollback_applied: bool = True
    external_calls: dict[str, int] = field(default_factory=dict)
    request_id: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all([self.widget_js, self.config, self.preflight, self.cors_options, self.chat, self.response_contract, self.side_effects_safe])

    def as_dict(self):
        return {
            "widget_js": self.widget_js,
            "config": self.config,
            "preflight": self.preflight,
            "cors_options": self.cors_options,
            "chat": self.chat,
            "response_contract": self.response_contract,
            "side_effects_safe": self.side_effects_safe,
            "rollback_applied": self.rollback_applied,
            "external_calls": self.external_calls,
            "request_id": self.request_id,
            "errors": self.errors,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class TenantRolloutResult:
    tenant: Tenant
    environment: str
    status: str
    dry_run: bool
    target_origin: str
    origin_valid: bool
    operational_status: str
    install_package_status: str
    side_effects_safe: bool
    checks: list[RolloutCheck]
    install_plan: RolloutInstallPlan
    smoke_plan: list[RolloutSmokeStep]
    rollback_plan: list[RolloutRollbackStep]
    smoke_result: RolloutSmokeResult | None = None
    generated_at: Any = None

    @property
    def blocking_checks(self):
        return [check for check in self.checks if check.blocking]

    @property
    def ready(self):
        return self.status in {ROLLOUT_STATUS_READY, ROLLOUT_STATUS_VALIDATED}

    def as_dict(self):
        return {
            "tenant": self.tenant.slug,
            "environment": self.environment,
            "status": self.status,
            "dry_run": self.dry_run,
            "target_origin": self.target_origin,
            "origin_valid": self.origin_valid,
            "operational_status": self.operational_status,
            "install_package_status": self.install_package_status,
            "side_effects_safe": self.side_effects_safe,
            "checks": [check.as_dict() for check in self.checks],
            "install_plan": self.install_plan.as_dict(),
            "smoke_plan": [step.as_dict() for step in self.smoke_plan],
            "rollback_plan": [step.as_dict() for step in self.rollback_plan],
            "smoke_result": self.smoke_result.as_dict() if self.smoke_result else None,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }


class TenantRolloutService:
    def __init__(self, *, operational_service=None, install_package_service=None, now=None):
        self.operational_service = operational_service or TenantOperationalReadinessService(now=now)
        self.install_package_service = install_package_service or TenantInstallPackageService()
        self.now = now or timezone.now()

    def build(self, spec: TenantRolloutSpec, *, run_smoke=False, actor=None, request=None, record_audit=False) -> TenantRolloutResult:
        environment = self._validate_environment(spec.normalized_environment())
        origin, origin_valid, origin_detail = self._validate_origin(spec.tenant, spec.target_origin)
        operational = self.operational_service.for_tenant(spec.tenant)
        package = self.install_package_service.build_for_tenant(spec.tenant)
        checks = self._preflight_checks(
            spec=spec,
            environment=environment,
            origin=origin,
            origin_valid=origin_valid,
            origin_detail=origin_detail,
            operational=operational,
            package=package,
        )
        side_effects_safe = self._side_effects_safe(operational, environment=environment)
        if not side_effects_safe:
            checks.append(RolloutCheck("side_effects_safe", "FAIL", "Há side effect real/mal configurado bloqueante para rollout.", True))
        else:
            checks.append(RolloutCheck("side_effects_safe", "PASS", "Side effects conhecidos, autorizados ou bloqueados com segurança pelo rollout."))
        install_plan = self._install_plan(package=package, origin=origin)
        smoke_plan = self._smoke_plan(spec.tenant)
        rollback_plan = self._rollback_plan(spec=spec, origin=origin)
        status = ROLLOUT_STATUS_BLOCKED if any(check.blocking for check in checks) else ROLLOUT_STATUS_READY
        smoke_result = None
        if record_audit:
            self._audit(ACTION_ROLLOUT_PLANNED, spec=spec, result_status=status, actor=actor, request=request)
            self._audit(ACTION_ROLLOUT_ROLLBACK_PLANNED, spec=spec, result_status=status, actor=actor, request=request)
            self._audit(
                ACTION_ROLLOUT_PREFLIGHT_BLOCKED if status == ROLLOUT_STATUS_BLOCKED else ACTION_ROLLOUT_PREFLIGHT_PASSED,
                spec=spec,
                result_status=status,
                actor=actor,
                request=request,
            )
        if run_smoke and status != ROLLOUT_STATUS_BLOCKED:
            if record_audit:
                self._audit(ACTION_ROLLOUT_SMOKE_STARTED, spec=spec, result_status=status, actor=actor, request=request)
            smoke_result = self.run_local_smoke(spec=spec, origin=origin)
            status = ROLLOUT_STATUS_VALIDATED if smoke_result.ok else ROLLOUT_STATUS_FAILED
            if record_audit:
                self._audit(
                    ACTION_ROLLOUT_SMOKE_PASSED if smoke_result.ok else ACTION_ROLLOUT_SMOKE_FAILED,
                    spec=spec,
                    result_status=status,
                    actor=actor,
                    request=request,
                    extra={"smoke_ok": smoke_result.ok, "request_id": smoke_result.request_id},
                )
        return TenantRolloutResult(
            tenant=spec.tenant,
            environment=environment,
            status=status,
            dry_run=spec.dry_run,
            target_origin=origin,
            origin_valid=origin_valid,
            operational_status=operational.status,
            install_package_status=package.readiness_status,
            side_effects_safe=side_effects_safe,
            checks=checks,
            install_plan=install_plan,
            smoke_plan=smoke_plan,
            rollback_plan=rollback_plan,
            smoke_result=smoke_result,
            generated_at=self.now,
        )

    def run_local_smoke(self, *, spec: TenantRolloutSpec, origin: str) -> RolloutSmokeResult:
        errors: list[str] = []
        external_calls = {"smart360_http": 0, "webhook_http": 0, "openai_chat": 0, "openai_embedding": 0}
        request_id = str(uuid.uuid4())

        def blocked_call(key):
            def _raise(*args, **kwargs):
                external_calls[key] += 1
                raise AssertionError(f"external_call_blocked:{key}")

            return _raise

        widget_js = config = cors_options = chat = response_contract = False
        with transaction.atomic():
            with ExitStack() as stack:
                stack.enter_context(
                    override_settings(
                        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
                        LIVIA_AI_ENABLED=False,
                        LIVIA_AI_DRY_RUN=True,
                        LIVIA_RAG_ENABLED=False,
                        LIVIA_CHAT_RATE_LIMIT_ENABLED=False,
                        SMART360_LEAD_DISPATCH_ENABLED=True,
                        SMART360_LEAD_DISPATCH_DRY_RUN=True,
                        SMART360_LEAD_DISPATCH_REAL_ENABLED=False,
                        LIVIA_WEBHOOKS_ENABLED=False,
                        LIVIA_WEBHOOKS_DRY_RUN=True,
                        LIVIA_WEBHOOKS_REAL_ENABLED=False,
                        LIVIA_GOOGLE_DRIVE_SYNC_REAL_ENABLED=False,
                    )
                )
                stack.enter_context(patch("integrations.smart360.client.requests.post", side_effect=blocked_call("smart360_http")))
                stack.enter_context(patch("integrations.webhooks.service.requests.post", side_effect=blocked_call("webhook_http")))
                stack.enter_context(patch("integrations.openai.client.OpenAIChatClient.create_chat_completion", side_effect=blocked_call("openai_chat")))
                stack.enter_context(patch("knowledge_base.rag.embeddings.OpenAIEmbeddingProvider.embed_texts", side_effect=blocked_call("openai_embedding")))
                client = Client()
                try:
                    widget_response = client.get("/widget.js", HTTP_ORIGIN=origin, HTTP_X_LIVIA_TENANT=spec.tenant.slug)
                    widget_js = widget_response.status_code == 200 and bool(widget_response.content)
                    if not widget_js:
                        errors.append(f"widget_js_http={widget_response.status_code}")
                except Exception as exc:
                    errors.append(f"widget_js:{exc.__class__.__name__}")
                try:
                    config_response = client.get(f"/api/widget/config/?tenant={spec.tenant.slug}", HTTP_ORIGIN=origin, HTTP_X_LIVIA_TENANT=spec.tenant.slug)
                    config = config_response.status_code == 200 and config_response.json().get("tenant") == spec.tenant.slug
                    if not config:
                        errors.append(f"config_http={config_response.status_code}")
                except Exception as exc:
                    errors.append(f"config:{exc.__class__.__name__}")
                try:
                    options_response = client.options("/api/chat/", HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST", HTTP_X_LIVIA_TENANT=spec.tenant.slug)
                    cors_options = options_response.status_code in {200, 204} and options_response.headers.get("Access-Control-Allow-Origin") == origin
                    if not cors_options:
                        errors.append(f"options_http={options_response.status_code}")
                except Exception as exc:
                    errors.append(f"options:{exc.__class__.__name__}")
                try:
                    payload = {
                        "tenant": spec.tenant.slug,
                        "session_id": f"rollout-smoke-{uuid.uuid4().hex[:8]}",
                        "request_id": request_id,
                        "message": "[ROLLOUT SMOKE] ping seguro sem lead/handoff/CRM",
                    }
                    chat_response = client.post(
                        "/api/chat/",
                        data=json.dumps(payload),
                        content_type="application/json",
                        HTTP_ORIGIN=origin,
                        HTTP_X_LIVIA_TENANT=spec.tenant.slug,
                        HTTP_X_LIVIA_REQUEST_ID=request_id,
                    )
                    chat = chat_response.status_code == 200
                    body = chat_response.json() if chat else {}
                    response_contract = chat and isinstance(body.get("reply"), str) and bool(body.get("reply"))
                    if not chat:
                        errors.append(f"chat_http={chat_response.status_code}")
                    if chat and not response_contract:
                        errors.append("response_contract_missing_reply")
                except Exception as exc:
                    errors.append(f"chat:{exc.__class__.__name__}")
            transaction.set_rollback(True)
        side_effects_safe = sum(external_calls.values()) == 0
        if not side_effects_safe:
            errors.append("external_call_attempted")
        return RolloutSmokeResult(
            widget_js=widget_js,
            config=config,
            preflight=True,
            cors_options=cors_options,
            chat=chat,
            response_contract=response_contract,
            side_effects_safe=side_effects_safe,
            rollback_applied=True,
            external_calls=external_calls,
            request_id=request_id,
            errors=errors,
        )

    def _preflight_checks(self, *, spec, environment, origin, origin_valid, origin_detail, operational, package):
        checks = []
        tenant = spec.tenant
        checks.append(RolloutCheck("tenant_active", "PASS" if tenant.is_active else "FAIL", "Tenant ativo." if tenant.is_active else "Tenant inativo.", not tenant.is_active))
        checks.append(RolloutCheck("origin_valid", "PASS" if origin_valid else "FAIL", origin_detail, not origin_valid))
        site_ok = package.readiness_status == SITE_READINESS_READY
        site_allowed_for_staging = environment == ENVIRONMENT_STAGING and package.readiness_status == STATUS_WARNING
        site_allowed_widget_off = self._site_allows_explicit_widget_off(package=package, spec=spec, environment=environment)
        site_allowed = site_ok or site_allowed_for_staging or site_allowed_widget_off
        site_detail = package.readiness_status
        if site_allowed_widget_off:
            site_detail = "NOT_READY aceito em staging porque somente o widget está explicitamente desabilitado."
        checks.append(RolloutCheck("site_readiness", "PASS" if site_allowed else "FAIL", site_detail, not site_allowed))
        widget_enabled = bool(package.widget_config.get("is_widget_enabled"))
        widget_blocking = not widget_enabled and not spec.allow_widget_disabled
        widget_detail = "Widget habilitado." if widget_enabled else "Widget desabilitado exige intenção explícita."
        if not widget_enabled and spec.allow_widget_disabled:
            widget_detail = "Widget desabilitado com intenção explícita; rollout permanece sem habilitar o widget."
        checks.append(RolloutCheck("widget_enabled", "PASS" if not widget_blocking else "FAIL", widget_detail, widget_blocking))
        if environment == ENVIRONMENT_PRODUCTION:
            checks.extend(self._production_checks(operational=operational, spec=spec))
        else:
            checks.extend(self._staging_checks(operational=operational, explicit_widget_off=site_allowed_widget_off))
        return checks

    def _site_allows_explicit_widget_off(self, *, package, spec, environment):
        if environment != ENVIRONMENT_STAGING or not spec.allow_widget_disabled:
            return False
        failed_codes = {
            getattr(check, "code", "")
            for check in getattr(package.readiness, "checks", [])
            if getattr(check, "status", "") == "FAIL"
        }
        return failed_codes and failed_codes <= {"assistant_profile_usable"}

    def _production_checks(self, *, operational, spec):
        knowledge_ok = operational.knowledge.status == STATUS_READY or (spec.allow_knowledge_warning and operational.knowledge.status == STATUS_WARNING)
        return [
            RolloutCheck("operational_ready", "PASS" if operational.status == STATUS_READY else "FAIL", operational.status, operational.status != STATUS_READY),
            RolloutCheck("knowledge_ready", "PASS" if knowledge_ok else "FAIL", operational.knowledge.status, not knowledge_ok),
            RolloutCheck("commercial_ready", "PASS" if operational.commercial.status == STATUS_READY else "FAIL", operational.commercial.status, operational.commercial.status != STATUS_READY),
            RolloutCheck("integrations_known", "PASS" if operational.integrations.status in {STATUS_READY, STATUS_WARNING} else "FAIL", operational.integrations.status, operational.integrations.status == STATUS_DEGRADED),
        ]

    def _staging_checks(self, *, operational, explicit_widget_off=False):
        blocking = operational.status in {STATUS_NOT_READY, STATUS_DEGRADED, STATUS_MAINTENANCE}
        if blocking and explicit_widget_off:
            component_statuses = {
                component.name: component.status
                for component in operational.components
            }
            non_ready = {
                name: status
                for name, status in component_statuses.items()
                if status not in {STATUS_READY, STATUS_WARNING}
            }
            if non_ready == {"site": STATUS_NOT_READY}:
                blocking = False
        detail = operational.status
        if not blocking and operational.status == STATUS_NOT_READY and explicit_widget_off:
            detail = "NOT_READY aceito em staging porque o único bloqueio operacional é widget-off explícito."
        return [
            RolloutCheck("operational_staging_gate", "PASS" if not blocking else "FAIL", detail, blocking),
            RolloutCheck("staging_warnings_allowed", "PASS", "Warnings são permitidos em staging; isolamento/CORS continuam obrigatórios."),
        ]

    def _install_plan(self, *, package, origin):
        return RolloutInstallPlan(
            target_site=origin,
            target_origin=origin,
            snippet=package.snippet,
            config_endpoint=f"/api/widget/config/?tenant={package.tenant.slug}",
            chat_endpoint=package.api_url,
            widget_src=package.widget_src,
            widget_position=str(package.widget_config.get("position") or ""),
            expected_tenant=package.tenant.slug,
            allowed_origins=list(package.allowed_origins),
            public_config=dict(package.widget_config),
        )

    def _smoke_plan(self, tenant):
        return [
            RolloutSmokeStep("GET", "/widget.js", "widget_js", "200 + JavaScript público"),
            RolloutSmokeStep("GET", f"/api/widget/config/?tenant={tenant.slug}", "widget_config", "200 + tenant correto"),
            RolloutSmokeStep("OPTIONS", "/api/chat/", "cors_options", "Origin autorizada no CORS"),
            RolloutSmokeStep("POST", "/api/chat/", "chat_contract", "200 + reply + request_id explícito"),
        ]

    def _rollback_plan(self, *, spec, origin):
        steps = [
            RolloutRollbackStep("remove_snippet", "Remover o snippet público do HTML do site cliente.", "Não altera dados internos da plataforma."),
            RolloutRollbackStep("disable_widget", "Desabilitar explicitamente o widget no AssistantProfile se necessário.", "Mudança interna reversível e auditável."),
            RolloutRollbackStep("revoke_origin", f"Desativar a origin {origin} somente se ela tiver sido criada para este rollout.", "Mantém root/www separados."),
        ]
        if spec.dry_run:
            steps.append(RolloutRollbackStep("dry_run_noop", "Nenhuma alteração remota é aplicada em dry-run.", "Rollback operacional já é noop."))
        return steps

    def _validate_environment(self, environment: str) -> str:
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ValueError("environment deve ser staging ou production")
        return environment

    def _validate_origin(self, tenant, raw_origin: str) -> tuple[str, bool, str]:
        try:
            origin = normalize_origin(raw_origin)
        except ValidationError as exc:
            return str(raw_origin or "").strip(), False, "; ".join(str(item) for item in exc.messages)
        result = validate_origin_for_tenant(tenant, origin)
        if not result.allowed:
            return origin, False, result.reason or "origin_not_allowed"
        return origin, True, "Origin autorizada para o tenant."

    def _side_effects_safe(self, operational, *, environment):
        decisions = operational.integrations.details.get("decisions", [])
        if not decisions:
            return True
        for item in decisions:
            if item.get("readiness") == STATUS_DEGRADED:
                return False
            if item.get("status") == "REAL_ENABLED":
                if not item.get("allowed"):
                    return False
                if environment == ENVIRONMENT_PRODUCTION:
                    return False
        return True

    def _audit(self, action, *, spec, result_status, actor=None, request=None, extra=None):
        metadata = {
            "environment": spec.normalized_environment(),
            "dry_run": spec.dry_run,
            "target_origin": str(spec.target_origin or ""),
            "result_status": result_status,
        }
        metadata.update(extra or {})
        record_audit_event(
            action=action,
            actor=actor,
            tenant=spec.tenant,
            obj=spec.tenant,
            metadata=metadata,
            request=request,
        )
