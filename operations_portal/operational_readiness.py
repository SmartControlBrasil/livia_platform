from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone

from audit.models import AuditEvent
from conversations.models import ChatRequest, Conversation, HandoffRequest
from integrations.models import OutboxEvent
from integrations.side_effect_policy import SideEffectStatus
from knowledge_base.models import TenantOperationalAlert, TenantOperationalMaintenanceWindow
from knowledge_base.services.lifecycle import (
    READINESS_AVAILABLE,
    READINESS_DEGRADED,
    READINESS_EMPTY,
    READINESS_INDEXING,
    READINESS_READY,
    READINESS_STALE,
    KnowledgeLifecycleService,
)
from leads.models import LeadDraft
from leads.services.commercial import CommercialReadinessService
from operations_portal.integration_services import _build_side_effect_decisions
from tenants.services.site_readiness import (
    SITE_READINESS_NOT_READY,
    SITE_READINESS_READY,
    SITE_READINESS_WARNING,
    inspect_tenant_site_readiness,
)

STATUS_READY = "READY"
STATUS_WARNING = "WARNING"
STATUS_DEGRADED = "DEGRADED"
STATUS_NOT_READY = "NOT_READY"
STATUS_MAINTENANCE = "MAINTENANCE"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

OUTBOX_PENDING_WARNING_COUNT = 25
OUTBOX_OLD_PENDING_SECONDS = 15 * 60
CHAT_STUCK_SECONDS = 5 * 60
HANDOFF_PENDING_WARNING_SECONDS = 24 * 60 * 60
RECENT_ACTIVITY_DAYS = 7

STATUS_TONES = {
    STATUS_READY: "success",
    STATUS_WARNING: "warning",
    STATUS_DEGRADED: "danger",
    STATUS_NOT_READY: "danger",
    STATUS_MAINTENANCE: "info",
}


@dataclass(frozen=True)
class OperationalIssue:
    code: str
    component: str
    severity: str
    message: str
    count: int = 0
    metadata: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "code": self.code,
            "component": self.component,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class OperationalComponent:
    name: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)
    issues: list[OperationalIssue] = field(default_factory=list)

    @property
    def tone(self):
        return STATUS_TONES.get(self.status, "secondary")

    def as_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "tone": self.tone,
            "summary": self.summary,
            "details": self.details,
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class TenantOperationalStatus:
    tenant: object
    status: str
    site: OperationalComponent
    knowledge: OperationalComponent
    commercial: OperationalComponent
    integrations: OperationalComponent
    outbox: OperationalComponent
    chat: OperationalComponent
    incidents: list[OperationalIssue] = field(default_factory=list)
    maintenance_windows: list[object] = field(default_factory=list)
    recent_audit_events: list[AuditEvent] = field(default_factory=list)
    last_activity_at: object | None = None

    @property
    def tone(self):
        return STATUS_TONES.get(self.status, "secondary")

    @property
    def warnings(self):
        return [issue for issue in self.incidents if issue.severity in {SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL}]

    @property
    def components(self):
        return [self.site, self.knowledge, self.commercial, self.integrations, self.outbox, self.chat]

    def as_dict(self):
        return {
            "tenant": getattr(self.tenant, "slug", ""),
            "status": self.status,
            "tone": self.tone,
            "site": self.site.as_dict(),
            "knowledge": self.knowledge.as_dict(),
            "commercial": self.commercial.as_dict(),
            "integrations": self.integrations.as_dict(),
            "outbox": self.outbox.as_dict(),
            "chat": self.chat.as_dict(),
            "incidents": [issue.as_dict() for issue in self.incidents],
            "maintenance": [
                {"title": window.title, "starts_at": window.starts_at.isoformat(), "ends_at": window.ends_at.isoformat()}
                for window in self.maintenance_windows
            ],
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
        }


class TenantOperationalReadinessService:
    """Read-only operational aggregator for a single tenant."""

    def __init__(
        self,
        *,
        knowledge_service=None,
        commercial_service=None,
        now=None,
        outbox_pending_warning_count=None,
        outbox_old_pending_seconds=None,
        chat_stuck_seconds=None,
        handoff_pending_warning_seconds=None,
    ):
        self.knowledge_service = knowledge_service or KnowledgeLifecycleService()
        self.commercial_service = commercial_service or CommercialReadinessService()
        self.now = now or timezone.now()
        self.outbox_pending_warning_count = _int_setting("LIVIA_OUTBOX_PENDING_WARNING_COUNT", outbox_pending_warning_count or OUTBOX_PENDING_WARNING_COUNT)
        self.outbox_old_pending_seconds = _int_setting("LIVIA_OUTBOX_OLD_PENDING_SECONDS", outbox_old_pending_seconds or OUTBOX_OLD_PENDING_SECONDS)
        self.chat_stuck_seconds = _int_setting("LIVIA_CHAT_STUCK_SECONDS", chat_stuck_seconds or CHAT_STUCK_SECONDS)
        self.handoff_pending_warning_seconds = _int_setting("LIVIA_HANDOFF_PENDING_WARNING_SECONDS", handoff_pending_warning_seconds or HANDOFF_PENDING_WARNING_SECONDS)

    def for_tenant(self, tenant) -> TenantOperationalStatus:
        site = self._site(tenant)
        knowledge = self._knowledge(tenant)
        commercial = self._commercial(tenant)
        integrations = self._integrations(tenant)
        outbox = self._outbox(tenant)
        chat = self._chat(tenant)
        alert_issues = self._alert_issues(tenant)
        maintenance_windows = self._active_maintenance_windows(tenant)
        issues = [issue for component in [site, knowledge, commercial, integrations, outbox, chat] for issue in component.issues]
        issues.extend(alert_issues)
        status = self._overall_status(
            components=[site, knowledge, commercial, integrations, outbox, chat],
            issues=issues,
            maintenance_windows=maintenance_windows,
        )
        return TenantOperationalStatus(
            tenant=tenant,
            status=status,
            site=site,
            knowledge=knowledge,
            commercial=commercial,
            integrations=integrations,
            outbox=outbox,
            chat=chat,
            incidents=issues,
            maintenance_windows=maintenance_windows,
            recent_audit_events=list(AuditEvent.objects.filter(tenant=tenant).order_by("-created_at")[:10]),
            last_activity_at=self._last_activity_at(tenant),
        )

    def for_tenants(self, tenants):
        return [self.for_tenant(tenant) for tenant in tenants]

    def _site(self, tenant) -> OperationalComponent:
        readiness = inspect_tenant_site_readiness(tenant)
        status = {
            SITE_READINESS_READY: STATUS_READY,
            SITE_READINESS_WARNING: STATUS_WARNING,
            SITE_READINESS_NOT_READY: STATUS_NOT_READY,
        }.get(readiness.overall_status, STATUS_WARNING)
        issues = [
            OperationalIssue(
                code=check.code,
                component="site",
                severity=SEVERITY_CRITICAL if check.status == "FAIL" else SEVERITY_WARNING,
                message=check.message,
            )
            for check in readiness.checks
            if check.status in {"FAIL", "WARN"}
        ]
        return OperationalComponent("site", status, f"{readiness.overall_status} ({len(readiness.checks)} checks)", readiness.to_dict(), issues)

    def _knowledge(self, tenant) -> OperationalComponent:
        readiness = self.knowledge_service.readiness(tenant=tenant)
        status = {
            READINESS_READY: STATUS_READY,
            READINESS_AVAILABLE: STATUS_WARNING,
            READINESS_EMPTY: STATUS_WARNING,
            READINESS_INDEXING: STATUS_WARNING,
            READINESS_STALE: STATUS_NOT_READY,
            READINESS_DEGRADED: STATUS_DEGRADED,
        }.get(readiness.status, STATUS_WARNING)
        issues = []
        if readiness.documents_failed:
            issues.append(OperationalIssue("knowledge_documents_failed", "knowledge", SEVERITY_ERROR, "Há documentos com falha de indexação.", readiness.documents_failed))
        if readiness.documents_stale:
            severity = SEVERITY_CRITICAL if readiness.documents_indexed == 0 else SEVERITY_WARNING
            issues.append(OperationalIssue("knowledge_documents_stale", "knowledge", severity, "Há documentos ativos aguardando reindexação.", readiness.documents_stale))
        if readiness.documents_indexing:
            issues.append(OperationalIssue("knowledge_documents_indexing", "knowledge", SEVERITY_WARNING, "Há documentos ainda em indexação.", readiness.documents_indexing))
        if readiness.status == READINESS_EMPTY:
            issues.append(OperationalIssue("knowledge_empty", "knowledge", SEVERITY_WARNING, "Nenhum conhecimento ativo disponível.", 0))
        return OperationalComponent("knowledge", status, readiness.detail, readiness.as_dict(), issues)

    def _commercial(self, tenant) -> OperationalComponent:
        readiness = self.commercial_service.readiness(tenant=tenant)
        status = {
            "READY": STATUS_READY,
            "PARTIAL": STATUS_WARNING,
            "NOT_CONFIGURED": STATUS_WARNING,
            "DEGRADED": STATUS_DEGRADED,
        }.get(readiness.status, STATUS_WARNING)
        details = dict(readiness.details)
        lead_failed = LeadDraft.objects.filter(tenant=tenant, dispatch_status=LeadDraft.DispatchStatus.FAILED).count()
        lead_retrying = LeadDraft.objects.filter(tenant=tenant, dispatch_status=LeadDraft.DispatchStatus.RETRYING).count()
        handoff_failed = HandoffRequest.objects.filter(tenant=tenant, dispatch_state=HandoffRequest.DispatchState.FAILED).count()
        handoff_retrying = HandoffRequest.objects.filter(tenant=tenant, dispatch_state=HandoffRequest.DispatchState.RETRYING).count()
        cutoff = self.now - timedelta(seconds=self.handoff_pending_warning_seconds)
        pending_old = HandoffRequest.objects.filter(tenant=tenant, status=HandoffRequest.Status.PENDING, created_at__lte=cutoff).count()
        missing_handoff = LeadDraft.objects.filter(
            tenant=tenant,
            qualification_status=LeadDraft.QualificationStatus.QUALIFIED,
            handoff_status=LeadDraft.HandoffStatus.READY,
        ).exclude(handoff_requests__tenant=tenant).count()
        details.update(
            {
                "lead_dispatch_failed": lead_failed,
                "lead_dispatch_retrying": lead_retrying,
                "handoff_dispatch_failed": handoff_failed,
                "handoff_dispatch_retrying": handoff_retrying,
                "pending_handoffs_old": pending_old,
                "qualified_without_handoff": missing_handoff,
            }
        )
        issues = []
        if lead_failed or handoff_failed:
            issues.append(OperationalIssue("commercial_dispatch_failed", "commercial", SEVERITY_ERROR, "Há dispatch comercial em falha.", lead_failed + handoff_failed))
        if lead_retrying or handoff_retrying:
            issues.append(OperationalIssue("commercial_dispatch_retrying", "commercial", SEVERITY_WARNING, "Há dispatch comercial em retry.", lead_retrying + handoff_retrying))
        if pending_old:
            issues.append(OperationalIssue("handoff_pending_old", "commercial", SEVERITY_WARNING, "Há handoffs pendentes além do limite operacional.", pending_old))
        if missing_handoff:
            issues.append(OperationalIssue("qualified_lead_without_handoff", "commercial", SEVERITY_WARNING, "Há lead qualificado pronto sem handoff associado.", missing_handoff))
        if any(issue.severity == SEVERITY_ERROR for issue in issues):
            status = STATUS_DEGRADED
        elif status == STATUS_READY and issues:
            status = STATUS_WARNING
        return OperationalComponent("commercial", status, readiness.status, details, issues)

    def _integrations(self, tenant) -> OperationalComponent:
        decisions = _build_side_effect_decisions(tenant=tenant)
        rows = []
        issues = []
        degraded = False
        for decision in decisions:
            code = str(decision.code or "")
            row_status = STATUS_READY
            if decision.status == SideEffectStatus.BLOCKED and _blocked_decision_is_misconfiguration(code):
                row_status = STATUS_DEGRADED
                degraded = True
                issues.append(OperationalIssue(code, "integrations", SEVERITY_ERROR, decision.reason, 1, {"side_effect": decision.side_effect.value}))
            rows.append(
                {
                    "side_effect": decision.side_effect.value,
                    "status": decision.status.value,
                    "readiness": row_status,
                    "allowed": decision.allowed,
                    "dry_run": decision.dry_run,
                    "code": code,
                    "reason": decision.reason,
                }
            )
        ai_decision = next((item for item in decisions if item.side_effect.value == "OPENAI_CHAT"), None)
        return OperationalComponent(
            "integrations",
            STATUS_DEGRADED if degraded else STATUS_READY,
            "Integrações com side effects bloqueados por política ou configurados.",
            {"decisions": rows, "ai_allowed": bool(ai_decision and ai_decision.allowed), "ai_status": getattr(getattr(ai_decision, "status", None), "value", "")},
            issues,
        )

    def _outbox(self, tenant) -> OperationalComponent:
        queryset = OutboxEvent.objects.filter(tenant=tenant)
        counts = {status: 0 for status, _label in OutboxEvent.Status.choices}
        for row in queryset.values("status").annotate(total=Count("id")):
            counts[row["status"]] = row["total"]
        oldest_pending = queryset.filter(status=OutboxEvent.Status.PENDING).order_by("created_at").values_list("created_at", flat=True).first()
        pending_age = int((self.now - oldest_pending).total_seconds()) if oldest_pending else 0
        issues = []
        if counts[OutboxEvent.Status.DEAD_LETTER]:
            issues.append(OperationalIssue("outbox_dead_letter", "outbox", SEVERITY_ERROR, "Há eventos em dead letter.", counts[OutboxEvent.Status.DEAD_LETTER]))
        if counts[OutboxEvent.Status.RETRY]:
            issues.append(OperationalIssue("outbox_retrying", "outbox", SEVERITY_WARNING, "Há eventos aguardando retry.", counts[OutboxEvent.Status.RETRY]))
        if counts[OutboxEvent.Status.PENDING] >= self.outbox_pending_warning_count or pending_age > self.outbox_old_pending_seconds:
            issues.append(OperationalIssue("outbox_backlog", "outbox", SEVERITY_WARNING, "Backlog de outbox acima do limite operacional.", counts[OutboxEvent.Status.PENDING], {"oldest_pending_age_seconds": pending_age}))
        status = STATUS_DEGRADED if any(issue.severity == SEVERITY_ERROR for issue in issues) else STATUS_WARNING if issues else STATUS_READY
        return OperationalComponent(
            "outbox",
            status,
            f"pending={counts[OutboxEvent.Status.PENDING]} retry={counts[OutboxEvent.Status.RETRY]} dead_letter={counts[OutboxEvent.Status.DEAD_LETTER]}",
            {"counts": counts, "oldest_pending_age_seconds": pending_age},
            issues,
        )

    def _chat(self, tenant) -> OperationalComponent:
        since = self.now - timedelta(days=RECENT_ACTIVITY_DAYS)
        queryset = ChatRequest.objects.filter(tenant=tenant, created_at__gte=since)
        counts = {status: 0 for status, _label in ChatRequest.Status.choices}
        for row in queryset.values("status").annotate(total=Count("id")):
            counts[row["status"]] = row["total"]
        stuck_cutoff = self.now - timedelta(seconds=self.chat_stuck_seconds)
        stuck = ChatRequest.objects.filter(tenant=tenant, status=ChatRequest.Status.PROCESSING, updated_at__lte=stuck_cutoff).count()
        issues = []
        if stuck:
            issues.append(OperationalIssue("chat_request_stuck", "chat", SEVERITY_ERROR, "Há ChatRequest preso em processing.", stuck))
        if counts[ChatRequest.Status.FAILED]:
            issues.append(OperationalIssue("chat_request_failed_recent", "chat", SEVERITY_WARNING, "Há ChatRequest recente com falha.", counts[ChatRequest.Status.FAILED]))
        status = STATUS_DEGRADED if stuck else STATUS_WARNING if issues else STATUS_READY
        return OperationalComponent("chat", status, "Solicitações de chat recentes.", {"counts": counts, "stuck_processing": stuck}, issues)


    def _active_maintenance_windows(self, tenant):
        return list(
            TenantOperationalMaintenanceWindow.objects.filter(
                tenant=tenant,
                status__in=[
                    TenantOperationalMaintenanceWindow.Status.SCHEDULED,
                    TenantOperationalMaintenanceWindow.Status.ACTIVE,
                ],
                starts_at__lte=self.now,
                ends_at__gte=self.now,
            ).order_by("starts_at", "id")
        )

    def _alert_issues(self, tenant):
        alerts = TenantOperationalAlert.objects.filter(
            tenant=tenant,
            status__in=[TenantOperationalAlert.Status.OPEN, TenantOperationalAlert.Status.ACKNOWLEDGED],
        )
        issues = []
        for severity in [TenantOperationalAlert.Severity.CRITICAL, TenantOperationalAlert.Severity.WARNING, TenantOperationalAlert.Severity.INFO]:
            count = alerts.filter(severity=severity).count()
            if not count:
                continue
            mapped = SEVERITY_ERROR if severity == TenantOperationalAlert.Severity.CRITICAL else severity
            issues.append(OperationalIssue("operational_alerts_open", "incidents", mapped, "Há alertas operacionais abertos ou reconhecidos.", count, {"alert_severity": severity}))
        return issues

    def _last_activity_at(self, tenant):
        values = [
            Conversation.objects.filter(tenant=tenant).aggregate(value=Max("updated_at"))["value"],
            LeadDraft.objects.filter(tenant=tenant).aggregate(value=Max("updated_at"))["value"],
            OutboxEvent.objects.filter(tenant=tenant).aggregate(value=Max("created_at"))["value"],
            AuditEvent.objects.filter(tenant=tenant).aggregate(value=Max("created_at"))["value"],
        ]
        values = [value for value in values if value]
        return max(values) if values else None

    def _overall_status(self, *, components, issues, maintenance_windows):
        if maintenance_windows:
            return STATUS_MAINTENANCE
        if any(component.status == STATUS_NOT_READY for component in components):
            return STATUS_NOT_READY
        if any(component.status == STATUS_DEGRADED for component in components):
            return STATUS_DEGRADED
        if any(issue.severity in {SEVERITY_ERROR, SEVERITY_CRITICAL} for issue in issues):
            return STATUS_DEGRADED
        if any(component.status == STATUS_WARNING for component in components):
            return STATUS_WARNING
        if any(issue.severity == SEVERITY_WARNING for issue in issues):
            return STATUS_WARNING
        return STATUS_READY


def attach_operational_statuses(tenants):
    tenant_list = list(tenants)
    service = TenantOperationalReadinessService()
    for tenant, status in zip(tenant_list, service.for_tenants(tenant_list)):
        tenant.operational_status = status
    return tenant_list


def _blocked_decision_is_misconfiguration(code: str) -> bool:
    return "missing" in code or "incomplete" in code


def _int_setting(name: str, default: int) -> int:
    try:
        return max(0, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default
