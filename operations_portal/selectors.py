from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404

from conversations.models import Conversation, HandoffRequest, Message
from integrations.models import TenantWebhookConfig
from leads.models import LeadDraft
from tenants.models import Tenant

from .formatters import (
    can_retry_crm_dispatch,
    compact_external_id,
    contact_summary,
    lead_crm_state,
    lead_status_label,
    lead_status_tone,
    mask_email,
    mask_phone,
    short_text,
)

CONVERSATION_LEAD_STATE_LABELS = {
    Conversation.LeadState.DISCOVERY: "Descoberta",
    Conversation.LeadState.OFFER_HANDOFF: "Oferta de atendimento humano",
    Conversation.LeadState.COLLECT_NEED: "Coleta da necessidade",
    Conversation.LeadState.COLLECT_NAME_COMPANY: "Nome e empresa",
    Conversation.LeadState.COLLECT_CONTACT: "Contato",
    Conversation.LeadState.QUALIFIED: "Qualificada",
    Conversation.LeadState.CLOSED: "Encerrada",
}

HANDOFF_STATUS_LABELS = {
    HandoffRequest.Status.PENDING: "Pendente",
    HandoffRequest.Status.SENT: "Enviado",
    HandoffRequest.Status.RESOLVED: "Resolvido",
    HandoffRequest.Status.CANCELLED: "Cancelado",
}

HANDOFF_PRIORITY_LABELS = {
    HandoffRequest.Priority.LOW: "Baixa",
    HandoffRequest.Priority.NORMAL: "Normal",
    HandoffRequest.Priority.HIGH: "Alta",
    HandoffRequest.Priority.URGENT: "Urgente",
}

PAGE_SIZE = 12


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
        decorate_conversation(conversation)
    recent_leads = list(LeadDraft.objects.select_related("tenant", "conversation").order_by("-updated_at")[:8])
    for lead in recent_leads:
        decorate_lead(lead)

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


def clean_querystring(querydict):
    params = querydict.copy()
    params.pop("page", None)
    return params.urlencode()


def get_conversation_list(form, *, page_number=1):
    queryset = (
        Conversation.objects.select_related("tenant", "lead_draft")
        .prefetch_related(
            Prefetch(
                "handoff_requests",
                queryset=HandoffRequest.objects.select_related("lead_draft").order_by("-created_at"),
                to_attr="prefetched_handoffs",
            )
        )
        .annotate(message_count=Count("messages", distinct=True))
        .order_by("-updated_at")
    )
    queryset = _filter_conversations(queryset, form)
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    for conversation in page.object_list:
        decorate_conversation(conversation)
    return page


def get_conversation_detail(pk):
    conversation = get_object_or_404(
        Conversation.objects.select_related("tenant", "lead_draft").prefetch_related(
            Prefetch("messages", queryset=Message.objects.order_by("created_at")),
            Prefetch(
                "handoff_requests",
                queryset=HandoffRequest.objects.select_related("lead_draft").order_by("-created_at"),
                to_attr="prefetched_handoffs",
            ),
        ),
        pk=pk,
    )
    decorate_conversation(conversation)
    if hasattr(conversation, "lead_draft"):
        decorate_lead(conversation.lead_draft)
    for handoff in getattr(conversation, "prefetched_handoffs", []):
        decorate_handoff(handoff)
    return conversation


def get_lead_list(form, *, page_number=1):
    queryset = (
        LeadDraft.objects.select_related("tenant", "conversation")
        .prefetch_related(
            Prefetch(
                "handoff_requests",
                queryset=HandoffRequest.objects.select_related("conversation").order_by("-created_at"),
                to_attr="prefetched_handoffs",
            )
        )
        .order_by("-updated_at", "-created_at")
    )
    queryset = _filter_leads(queryset, form)
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    for lead in page.object_list:
        decorate_lead(lead)
    return page


def get_lead_detail(pk):
    lead = get_object_or_404(
        LeadDraft.objects.select_related("tenant", "conversation").prefetch_related(
            Prefetch(
                "handoff_requests",
                queryset=HandoffRequest.objects.select_related("conversation").order_by("-created_at"),
                to_attr="prefetched_handoffs",
            )
        ),
        pk=pk,
    )
    decorate_lead(lead)
    for handoff in getattr(lead, "prefetched_handoffs", []):
        decorate_handoff(handoff)
    if lead.conversation is not None:
        decorate_conversation(lead.conversation)
    return lead


def decorate_conversation(conversation):
    conversation.lead_state_label = CONVERSATION_LEAD_STATE_LABELS.get(
        conversation.lead_state, conversation.get_lead_state_display()
    )
    handoffs = list(getattr(conversation, "prefetched_handoffs", []))
    conversation.latest_handoff = handoffs[0] if handoffs else None
    if conversation.latest_handoff is not None:
        decorate_handoff(conversation.latest_handoff)
    return conversation


def decorate_lead(lead):
    lead.status_label = lead_status_label(lead.status)
    lead.status_tone = lead_status_tone(lead.status)
    lead.crm_state = lead_crm_state(lead)
    lead.crm_external_id_compact = compact_external_id(lead.crm_external_id)
    lead.need_summary_short = short_text(lead.need_summary, limit=100)
    lead.contact = contact_summary(lead)
    lead.masked_email = mask_email(lead.email)
    lead.masked_phone = mask_phone(lead.phone)
    lead.can_retry_crm_dispatch = can_retry_crm_dispatch(lead)
    handoffs = list(getattr(lead, "prefetched_handoffs", []))
    lead.latest_handoff = handoffs[0] if handoffs else None
    if lead.latest_handoff is not None:
        decorate_handoff(lead.latest_handoff)
    return lead


def decorate_handoff(handoff):
    handoff.status_label = HANDOFF_STATUS_LABELS.get(handoff.status, handoff.get_status_display())
    handoff.priority_label = HANDOFF_PRIORITY_LABELS.get(handoff.priority, handoff.get_priority_display())
    return handoff


def _filter_conversations(queryset, form):
    if not form.is_valid():
        return queryset
    data = form.cleaned_data
    if data.get("tenant"):
        queryset = queryset.filter(tenant=data["tenant"])
    if data.get("lead_state"):
        queryset = queryset.filter(lead_state=data["lead_state"])
    if data.get("qualified") == "yes":
        queryset = queryset.filter(is_qualified=True)
    if data.get("qualified") == "no":
        queryset = queryset.filter(is_qualified=False)
    if data.get("start_date"):
        queryset = queryset.filter(updated_at__date__gte=data["start_date"])
    if data.get("end_date"):
        queryset = queryset.filter(updated_at__date__lte=data["end_date"])
    if data.get("q"):
        queryset = queryset.filter(session_id__icontains=data["q"].strip())
    return queryset


def _filter_leads(queryset, form):
    if not form.is_valid():
        return queryset
    data = form.cleaned_data
    sent_lookup = Q(status=LeadDraft.Status.SENT_TO_CRM) | Q(crm_external_id__gt="") | Q(sent_to_crm_at__isnull=False)
    failed_lookup = Q(status=LeadDraft.Status.FAILED) | Q(crm_error__gt="")
    if data.get("tenant"):
        queryset = queryset.filter(tenant=data["tenant"])
    if data.get("status"):
        queryset = queryset.filter(status=data["status"])
    if data.get("crm_sent") == "yes":
        queryset = queryset.filter(sent_lookup)
    if data.get("crm_sent") == "no":
        queryset = queryset.exclude(sent_lookup)
    if data.get("dispatch_failed") == "yes":
        queryset = queryset.filter(failed_lookup)
    if data.get("dispatch_failed") == "no":
        queryset = queryset.exclude(failed_lookup)
    if data.get("start_date"):
        queryset = queryset.filter(updated_at__date__gte=data["start_date"])
    if data.get("end_date"):
        queryset = queryset.filter(updated_at__date__lte=data["end_date"])
    if data.get("q"):
        query = data["q"].strip()
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(company__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(conversation__session_id__icontains=query)
        )
    return queryset
