"""Views do painel Qualidade da Lívia."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from conversations.models import Message
from tenants.access import CAPABILITY_CONVERSATIONS_VIEW, CAPABILITY_PORTAL_VIEW_DASHBOARD
from tenants.models import Tenant

from .access import portal_template_context, resolve_portal_access
from .quality_metrics import (
    build_conversation_quality_list,
    build_conversation_rag_debug,
    build_documents_without_embedding,
    build_knowledge_gaps,
    build_outbox_metrics,
    build_quality_dashboard,
    build_tenant_quality_detail,
    resolve_quality_period,
)
from .selectors import decorate_conversation, get_conversation_detail


def _period_from_request(request):
    return resolve_quality_period(
        period=request.GET.get("period"),
        start=request.GET.get("start"),
        end=request.GET.get("end"),
    )


def _filter_query(request) -> dict:
    return {
        "period": request.GET.get("period") or "7d",
        "start": request.GET.get("start") or "",
        "end": request.GET.get("end") or "",
        "intent": request.GET.get("intent") or "",
        "lead_status": request.GET.get("lead_status") or "",
        "handoff_status": request.GET.get("handoff_status") or "",
        "source_page": request.GET.get("source_page") or "",
        "event_type": request.GET.get("event_type") or "",
        "status": request.GET.get("status") or "",
    }


def _clean_querystring(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()



@login_required(login_url="/admin/login/")
def quality_dashboard(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    period = _period_from_request(request)
    accessible_ids = [t.pk for t in access.accessible_tenants]
    payload = build_quality_dashboard(
        tenant=access.tenant,
        period=period,
        accessible_tenant_ids=accessible_ids if not access.is_global else None,
    )
    context = {
        "active_section": "qualidade",
        "filters": _filter_query(request),
        **payload,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/dashboard.html", context)


@login_required(login_url="/admin/login/")
def quality_tenant_detail(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    tenant = get_object_or_404(Tenant, slug=slug, is_active=True)
    if not access.is_global and tenant.pk not in {t.pk for t in access.accessible_tenants}:
        raise PermissionDenied
    period = _period_from_request(request)
    payload = build_tenant_quality_detail(tenant=tenant, period=period)
    context = {
        "active_section": "qualidade",
        "filters": _filter_query(request),
        **payload,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/tenant_detail.html", context)


@login_required(login_url="/admin/login/")
def quality_knowledge_gaps(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    period = _period_from_request(request)
    gaps = build_knowledge_gaps(
        tenant=access.tenant,
        period=period,
        page=int(request.GET.get("page") or 1),
    )
    context = {
        "active_section": "qualidade",
        "filters": _filter_query(request),
        "period": {"key": period.key, "label": period.label, "options": ("today", "7d", "30d", "custom")},
        "querystring": _clean_querystring(request),
        **gaps,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/knowledge_gaps.html", context)


@login_required(login_url="/admin/login/")
def quality_conversations(request):
    access = resolve_portal_access(request, capability=CAPABILITY_CONVERSATIONS_VIEW, allow_global=True)
    period = _period_from_request(request)
    filters = _filter_query(request)
    payload = build_conversation_quality_list(
        tenant=access.tenant,
        period=period,
        intent=filters["intent"] or None,
        lead_status=filters["lead_status"] or None,
        handoff_status=filters["handoff_status"] or None,
        source_page=filters["source_page"] or None,
        page=int(request.GET.get("page") or 1),
    )
    context = {
        "active_section": "qualidade",
        "filters": filters,
        "period": {"key": period.key, "label": period.label, "options": ("today", "7d", "30d", "custom")},
        "querystring": _clean_querystring(request),
        **payload,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/conversations.html", context)


@login_required(login_url="/admin/login/")
def quality_conversation_transcript(request, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_CONVERSATIONS_VIEW, allow_global=True)
    conversation = get_conversation_detail(pk, tenant=access.tenant)
    decorate_conversation(conversation)
    messages = [
        m
        for m in conversation.messages.all()
        if m.role in {Message.Role.USER, Message.Role.ASSISTANT}
    ]
    rag_debug = build_conversation_rag_debug(conversation=conversation)
    context = {
        "active_section": "qualidade",
        "conversation": conversation,
        "transcript_messages": messages,
        "rag_debug": rag_debug,
        "show_rag_debug": access.user.is_staff or access.user.is_superuser,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/transcript.html", context)


@login_required(login_url="/admin/login/")
def quality_documents(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    missing = build_documents_without_embedding(
        tenant=access.tenant,
        page=int(request.GET.get("page") or 1),
    )
    from knowledge_base.models import KnowledgeDocument
    from django.core.paginator import Paginator
    from .quality_metrics import _scope

    docs = (
        _scope(KnowledgeDocument.objects.select_related("tenant"), access.tenant)
        .order_by("tenant__slug", "title")
    )
    paginator = Paginator(docs, 25)
    page_obj = paginator.get_page(int(request.GET.get("docs_page") or 1))
    context = {
        "active_section": "qualidade",
        "filters": _filter_query(request),
        "documents_page": page_obj,
        "missing": missing,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/documents.html", context)


@login_required(login_url="/admin/login/")
def quality_outbox(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    period = _period_from_request(request)
    event_type = request.GET.get("event_type") or None
    outbox = build_outbox_metrics(tenant=access.tenant, period=period, event_type=event_type)
    from integrations.models import OutboxEvent
    from django.core.paginator import Paginator
    from .quality_metrics import _scope

    events = _scope(OutboxEvent.objects.select_related("tenant"), access.tenant).filter(
        created_at__gte=period.start,
        created_at__lt=period.end,
    )
    if event_type:
        events = events.filter(event_type=event_type)
    status = request.GET.get("status")
    if status:
        events = events.filter(status=status)
    paginator = Paginator(events.order_by("-created_at"), 25)
    page_obj = paginator.get_page(int(request.GET.get("page") or 1))
    context = {
        "active_section": "qualidade",
        "filters": _filter_query(request),
        "period": {"key": period.key, "label": period.label, "options": ("today", "7d", "30d", "custom")},
        "outbox": outbox,
        "page_obj": page_obj,
        "querystring": _clean_querystring(request),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/quality/outbox.html", context)
