from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count, Q

from conversations.models import Conversation, HandoffRequest
from integrations.models import TenantWebhookConfig
from leads.models import LeadDraft
from tenants.models import Tenant

CONVERSATION_LEAD_STATE_LABELS = {
    Conversation.LeadState.DISCOVERY: "Descoberta",
    Conversation.LeadState.OFFER_HANDOFF: "Oferta de atendimento humano",
    Conversation.LeadState.COLLECT_NEED: "Coleta da necessidade",
    Conversation.LeadState.COLLECT_NAME_COMPANY: "Nome e empresa",
    Conversation.LeadState.COLLECT_CONTACT: "Contato",
    Conversation.LeadState.QUALIFIED: "Qualificada",
    Conversation.LeadState.CLOSED: "Encerrada",
}


@dataclass(frozen=True)
class IntegrationStatus:
    label: str
    state: str
    detail: str
    tone: str


def get_dashboard_context():
    tenant_stats = Tenant.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    conversation_stats = Conversation.objects.aggregate(
        total=Count("id"),
        qualified=Count("id", filter=Q(is_qualified=True)),
    )
    lead_stats = LeadDraft.objects.aggregate(
        total=Count("id"),
        qualified=Count("id", filter=Q(status=LeadDraft.Status.QUALIFIED)),
        sent_to_crm=Count("id", filter=Q(status=LeadDraft.Status.SENT_TO_CRM)),
        failed=Count("id", filter=Q(status=LeadDraft.Status.FAILED)),
    )
    handoff_stats = HandoffRequest.objects.aggregate(
        pending=Count("id", filter=Q(status=HandoffRequest.Status.PENDING)),
        high_priority=Count(
            "id",
            filter=Q(
                status=HandoffRequest.Status.PENDING,
                priority__in=[HandoffRequest.Priority.HIGH, HandoffRequest.Priority.URGENT],
            ),
        ),
    )

    recent_conversations = list(Conversation.objects.select_related("tenant").order_by("-updated_at")[:8])
    for conversation in recent_conversations:
        conversation.lead_state_label = CONVERSATION_LEAD_STATE_LABELS.get(
            conversation.lead_state, conversation.get_lead_state_display()
        )
    recent_leads = LeadDraft.objects.select_related("tenant", "conversation").order_by("-updated_at")[:8]

    return {
        "kpis": {
            "active_tenants": tenant_stats["active"] or 0,
            "total_conversations": conversation_stats["total"] or 0,
            "recent_conversations": len(recent_conversations),
            "total_leads": lead_stats["total"] or 0,
            "qualified_leads": lead_stats["qualified"] or 0,
            "sent_to_crm": lead_stats["sent_to_crm"] or 0,
            "failed_leads": lead_stats["failed"] or 0,
            "pending_handoffs": handoff_stats["pending"] or 0,
            "high_priority_handoffs": handoff_stats["high_priority"] or 0,
        },
        "recent_conversations": recent_conversations,
        "recent_leads": recent_leads,
        "integration_statuses": get_integration_statuses(),
        "active_tenants": Tenant.objects.filter(is_active=True).order_by("name")[:8],
    }


def get_integration_statuses():
    return [
        get_crm_status(),
        _status(
            "OpenAI",
            bool(getattr(settings, "LIVIA_AI_ENABLED", False)),
            bool(getattr(settings, "LIVIA_AI_DRY_RUN", True)),
            "Respostas assistidas por IA",
        ),
        _status(
            "Webhooks",
            bool(getattr(settings, "LIVIA_WEBHOOKS_ENABLED", False)),
            bool(getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True)),
            f"{TenantWebhookConfig.objects.filter(is_active=True).count()} config. ativas",
        ),
        _status(
            "Notificações de handoff",
            bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED", False)),
            bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True)),
            "Alertas para atendimento humano",
        ),
    ]


def get_crm_status():
    enabled = bool(getattr(settings, "SMART360_LEAD_DISPATCH_ENABLED", False))
    dry_run = bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True))
    has_base_url = bool(str(getattr(settings, "SMART360_BASE_URL", "") or "").strip())
    has_token = bool(str(getattr(settings, "SMART360_M2M_TOKEN", "") or "").strip())

    if not enabled:
        return IntegrationStatus(
            label="CRM Smart360",
            state="Desligado",
            detail="Dispatch de leads desabilitado",
            tone="secondary",
        )
    if dry_run:
        return IntegrationStatus(
            label="CRM Smart360",
            state="Dry-run",
            detail="Dispatch real não será chamado",
            tone="warning",
        )
    if not (has_base_url and has_token):
        return IntegrationStatus(
            label="CRM Smart360",
            state="Configuração incompleta",
            detail="Modo real sem endpoint ou credencial configurada",
            tone="danger",
        )
    return IntegrationStatus(
        label="CRM Smart360",
        state="Ativo real",
        detail="Dispatch real habilitado",
        tone="success",
    )


def _status(label, enabled, dry_run, detail):
    if not enabled:
        return IntegrationStatus(label=label, state="Desligado", detail=detail, tone="secondary")
    if dry_run:
        return IntegrationStatus(label=label, state="Dry-run", detail=detail, tone="warning")
    return IntegrationStatus(label=label, state="Ativo", detail=detail, tone="success")


def has_secure_portal_scope(user):
    return bool(user.is_authenticated and user.is_staff and user.is_superuser)


def tenant_scope_note(user):
    if user.is_superuser:
        return "Consolidação administrativa de todos os tenants."
    return "Painel consolidado restrito a superusers até existir vínculo seguro entre usuário e tenant."
