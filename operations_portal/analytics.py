from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from conversations.models import Conversation, HandoffRequest
from leads.models import LeadDraft
from tenants.models import Tenant


VALID_PERIODS = (7, 30, 90)

CONVERSATION_LEAD_STATE_LABELS = {
    Conversation.LeadState.DISCOVERY: "Descoberta",
    Conversation.LeadState.OFFER_HANDOFF: "Oferta de atendimento humano",
    Conversation.LeadState.COLLECT_NEED: "Coleta da necessidade",
    Conversation.LeadState.COLLECT_NAME_COMPANY: "Nome e empresa",
    Conversation.LeadState.COLLECT_CONTACT: "Contato",
    Conversation.LeadState.QUALIFIED: "Qualificada",
    Conversation.LeadState.CLOSED: "Encerrada",
}

FUNNEL_LABELS = {
    LeadDraft.Status.DRAFT: "Rascunhos de leads",
    LeadDraft.Status.QUALIFIED: "Qualificados",
    LeadDraft.Status.SENT_TO_CRM: "Enviados ao CRM",
    LeadDraft.Status.FAILED: "Falha de envio",
}


def resolve_period(value) -> int:
    try:
        period = int(value)
    except (TypeError, ValueError):
        return 30
    return period if period in VALID_PERIODS else 30


@dataclass(frozen=True)
class PeriodWindow:
    days: int
    start_date: object
    end_date: object
    start: object
    end: object
    label: str


def get_period_window(days: int) -> PeriodWindow:
    current_tz = timezone.get_current_timezone()
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days - 1)
    start = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), current_tz)
    return PeriodWindow(
        days=days,
        start_date=start_date,
        end_date=end_date,
        start=start,
        end=end,
        label=f"{start_date:%d/%m/%Y} a {end_date:%d/%m/%Y}",
    )


def get_dashboard_analytics(period_value=None, *, tenant=None):
    days = resolve_period(period_value)
    window = get_period_window(days)
    day_items = _period_days(window)
    conversation_qs = _scope_queryset(Conversation.objects.all(), tenant)
    lead_qs = _scope_queryset(LeadDraft.objects.all(), tenant)
    handoff_qs = _scope_queryset(HandoffRequest.objects.all(), tenant)
    tenant_qs = Tenant.objects.filter(pk=tenant.pk) if tenant is not None else Tenant.objects.filter(is_active=True)
    conversations_by_day = _counts_by_day(conversation_qs, "created_at", window, day_items)
    leads_created_by_day = _counts_by_day(lead_qs, "created_at", window, day_items)
    leads_sent_by_day = _counts_by_day(
        lead_qs.filter(sent_to_crm_at__isnull=False),
        "sent_to_crm_at",
        window,
        day_items,
    )
    funnel = _lead_funnel(window, lead_qs)
    conversation_states = _conversation_states(window, conversation_qs)
    tenant_volume = _tenant_volume(window, tenant_qs)
    kpis = _analytics_kpis(window, conversation_qs, lead_qs, handoff_qs, tenant_qs)

    return {
        "period": {
            "days": days,
            "options": VALID_PERIODS,
            "label": window.label,
            "start": window.start_date.isoformat(),
            "end": window.end_date.isoformat(),
        },
        "kpis": kpis,
        "charts": {
            "conversations_by_day": {
                "labels": [item["label"] for item in conversations_by_day],
                "dates": [item["date"] for item in conversations_by_day],
                "series": [{"name": "Conversas", "data": [item["count"] for item in conversations_by_day]}],
                "has_data": any(item["count"] for item in conversations_by_day),
                "summary": f"{kpis['period_conversations']} conversas no período.",
            },
            "leads_by_day": {
                "labels": [item["label"] for item in day_items],
                "dates": [item["date"] for item in day_items],
                "series": [
                    {"name": "Leads criados", "data": [item["count"] for item in leads_created_by_day]},
                    {"name": "Enviados ao CRM", "data": [item["count"] for item in leads_sent_by_day]},
                ],
                "has_data": any(item["count"] for item in leads_created_by_day + leads_sent_by_day),
                "summary": f"{kpis['period_leads_created']} leads criados e {kpis['period_leads_sent']} enviados ao CRM.",
            },
            "funnel": funnel,
            "conversation_states": conversation_states,
            "tenant_volume": tenant_volume,
        },
    }

def _analytics_kpis(window, conversation_qs, lead_qs, handoff_qs, tenant_qs):
    lead_stats = lead_qs.aggregate(
        total=Count("id"),
        created=Count("id", filter=Q(created_at__gte=window.start, created_at__lt=window.end)),
        qualified=Count(
            "id",
            filter=Q(created_at__gte=window.start, created_at__lt=window.end, status=LeadDraft.Status.QUALIFIED),
        ),
        sent=Count(
            "id",
            filter=Q(created_at__gte=window.start, created_at__lt=window.end, status=LeadDraft.Status.SENT_TO_CRM),
        ),
        failed=Count(
            "id",
            filter=Q(created_at__gte=window.start, created_at__lt=window.end, status=LeadDraft.Status.FAILED),
        ),
    )
    conversation_stats = conversation_qs.aggregate(
        total=Count("id"),
        period=Count("id", filter=Q(created_at__gte=window.start, created_at__lt=window.end)),
    )
    handoff_stats = handoff_qs.aggregate(
        pending=Count("id", filter=Q(status=HandoffRequest.Status.PENDING)),
        period_pending=Count(
            "id",
            filter=Q(status=HandoffRequest.Status.PENDING, created_at__gte=window.start, created_at__lt=window.end),
        ),
        high_priority=Count(
            "id",
            filter=Q(
                status=HandoffRequest.Status.PENDING,
                priority__in=[HandoffRequest.Priority.HIGH, HandoffRequest.Priority.URGENT],
            ),
        ),
    )
    created = lead_stats["created"] or 0
    qualified = lead_stats["qualified"] or 0
    sent = lead_stats["sent"] or 0
    return {
        "period_conversations": conversation_stats["period"] or 0,
        "period_leads_created": created,
        "period_leads_qualified": qualified,
        "period_leads_sent": sent,
        "period_leads_failed": lead_stats["failed"] or 0,
        "qualification_rate": _percentage(qualified, created),
        "crm_send_rate": _percentage(sent, created),
        "pending_handoffs": handoff_stats["pending"] or 0,
        "period_pending_handoffs": handoff_stats["period_pending"] or 0,
        "high_priority_handoffs": handoff_stats["high_priority"] or 0,
        "active_tenants": tenant_qs.filter(is_active=True).count(),
        "total_conversations": conversation_stats["total"] or 0,
        "total_leads": lead_stats["total"] or 0,
    }


def _period_days(window):
    return [
        {
            "date": (window.start_date + timedelta(days=offset)).isoformat(),
            "label": f"{window.start_date + timedelta(days=offset):%d/%m}",
            "count": 0,
        }
        for offset in range(window.days)
    ]


def _counts_by_day(queryset, field_name, window, day_items):
    current_tz = timezone.get_current_timezone()
    date_key = f"{field_name}__gte"
    end_key = f"{field_name}__lt"
    rows = (
        queryset.filter(**{date_key: window.start, end_key: window.end})
        .annotate(day=TruncDate(field_name, tzinfo=current_tz))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    counts = {row["day"].isoformat(): row["count"] for row in rows}
    return [{**item, "count": counts.get(item["date"], 0)} for item in day_items]


def _lead_funnel(window, lead_qs):
    rows = (
        lead_qs.filter(created_at__gte=window.start, created_at__lt=window.end)
        .values("status")
        .annotate(count=Count("id"))
    )
    counts = {row["status"]: row["count"] for row in rows}
    items = [
        {"label": label, "count": counts.get(status, 0)}
        for status, label in FUNNEL_LABELS.items()
    ]
    return {
        "labels": [item["label"] for item in items],
        "series": [item["count"] for item in items],
        "items": items,
        "has_data": any(item["count"] for item in items),
        "summary": "Leads criados no período agrupados por status atual; categorias mutuamente exclusivas.",
    }


def _conversation_states(window, conversation_qs):
    rows = (
        conversation_qs.filter(created_at__gte=window.start, created_at__lt=window.end)
        .values("lead_state")
        .annotate(count=Count("id"))
        .order_by("lead_state")
    )
    items = [
        {"label": _conversation_state_label(row["lead_state"]), "count": row["count"]}
        for row in rows
    ]
    return {
        "labels": [item["label"] for item in items],
        "series": [item["count"] for item in items],
        "items": items,
        "has_data": any(item["count"] for item in items),
        "summary": f"{sum(item['count'] for item in items)} conversas distribuídas por etapa.",
    }


def _tenant_volume(window, tenant_qs):
    rows = list(
        tenant_qs.annotate(
            conversations_count=Count(
                "conversations",
                filter=Q(conversations__created_at__gte=window.start, conversations__created_at__lt=window.end),
                distinct=True,
            ),
            leads_count=Count(
                "lead_drafts",
                filter=Q(lead_drafts__created_at__gte=window.start, lead_drafts__created_at__lt=window.end),
                distinct=True,
            ),
        )
        .annotate(total_volume=F("conversations_count") + F("leads_count"))
        .filter(total_volume__gt=0)
        .order_by("-total_volume", "name")[:11]
    )
    has_more = len(rows) > 10
    items = [
        {
            "label": tenant.name or tenant.slug,
            "slug": tenant.slug,
            "conversations": tenant.conversations_count,
            "leads": tenant.leads_count,
            "total": tenant.total_volume,
        }
        for tenant in rows[:10]
    ]
    return {
        "labels": [item["label"] for item in items],
        "series": [
            {"name": "Conversas", "data": [item["conversations"] for item in items]},
            {"name": "Leads", "data": [item["leads"] for item in items]},
        ],
        "items": items,
        "has_data": any(item["total"] for item in items),
        "has_more": has_more,
        "summary": "Top 10 tenants por volume combinado de conversas e leads no período.",
    }


def _conversation_state_label(value):
    if value in CONVERSATION_LEAD_STATE_LABELS:
        return CONVERSATION_LEAD_STATE_LABELS[value]
    return str(value or "Sem estado").replace("_", " ").strip().capitalize()


def _percentage(numerator, denominator):
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 1)


def _scope_queryset(queryset, tenant):
    if tenant is None:
        return queryset
    return queryset.filter(tenant=tenant)
