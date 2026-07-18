from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from conversations.models import HandoffRequest
from leads.models import LeadDraft
from leads.services.crm_dispatch import CRMDispatchService
from leads.services.handoff import HandoffService

from .forms import ConversationFilterForm, HandoffFilterForm, LeadFilterForm
from .formatters import can_retry_crm_dispatch
from .selectors import (
    clean_querystring,
    get_conversation_detail,
    get_conversation_list,
    get_dashboard_context,
    get_handoff_detail,
    get_handoff_list,
    get_lead_detail,
    get_lead_list,
    is_valid_handoff_transition,
    has_secure_portal_scope,
    tenant_scope_note,
)

PLACEHOLDERS = {
    "tenants": "Tenants",
    "base-de-conhecimento": "Base de conhecimento",
    "integracoes": "Integrações",
    "configuracoes": "Configurações",
}


@login_required(login_url="/admin/login/")
def dashboard(request):
    _require_staff_scope(request.user)
    context = get_dashboard_context(request.GET.get("period"))
    context.update({"active_section": "overview", "tenant_scope_note": tenant_scope_note(request.user)})
    return render(request, "operations_portal/dashboard.html", context)


@login_required(login_url="/admin/login/")
def conversation_list(request):
    _require_staff_scope(request.user)
    form = ConversationFilterForm(request.GET or None)
    page = get_conversation_list(form, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/conversation_list.html",
        {
            "active_section": "conversas",
            "tenant_scope_note": tenant_scope_note(request.user),
            "form": form,
            "page_obj": page,
            "querystring": clean_querystring(request.GET),
        },
    )


@login_required(login_url="/admin/login/")
def conversation_detail(request, pk):
    _require_staff_scope(request.user)
    conversation = get_conversation_detail(pk)
    return render(
        request,
        "operations_portal/conversation_detail.html",
        {
            "active_section": "conversas",
            "tenant_scope_note": tenant_scope_note(request.user),
            "conversation": conversation,
        },
    )


@login_required(login_url="/admin/login/")
def lead_list(request):
    _require_staff_scope(request.user)
    form = LeadFilterForm(request.GET or None)
    page = get_lead_list(form, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/lead_list.html",
        {
            "active_section": "leads",
            "tenant_scope_note": tenant_scope_note(request.user),
            "form": form,
            "page_obj": page,
            "querystring": clean_querystring(request.GET),
        },
    )


@login_required(login_url="/admin/login/")
def lead_detail(request, pk):
    _require_staff_scope(request.user)
    lead = get_lead_detail(pk)
    return render(
        request,
        "operations_portal/lead_detail.html",
        {
            "active_section": "leads",
            "tenant_scope_note": tenant_scope_note(request.user),
            "lead": lead,
        },
    )


@login_required(login_url="/admin/login/")
def retry_lead_crm_dispatch(request, pk):
    _require_staff_scope(request.user)
    if request.method != "POST":
        raise PermissionDenied

    lead = get_lead_detail(pk)
    if not can_retry_crm_dispatch(lead):
        messages.warning(request, "Este lead não pode ser reprocessado porque já foi enviado ou não está em falha.")
        return redirect("operations_portal:lead_detail", pk=lead.pk)

    lead.status = LeadDraft.Status.QUALIFIED
    lead.crm_error = ""
    lead.save(update_fields=["status", "crm_error", "updated_at"])
    result = CRMDispatchService().dispatch_if_qualified(lead)
    if result.success:
        messages.success(request, "Reprocessamento concluído com sucesso.")
    else:
        if not result.attempted:
            lead.status = LeadDraft.Status.FAILED
            lead.crm_error = result.message
            lead.save(update_fields=["status", "crm_error", "updated_at"])
        messages.error(request, result.message or "Reprocessamento não concluído.")
    return redirect("operations_portal:lead_detail", pk=lead.pk)




@login_required(login_url="/admin/login/")
def handoff_list(request):
    _require_staff_scope(request.user)
    form = HandoffFilterForm(request.GET or None)
    page = get_handoff_list(form, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/handoff_list.html",
        {
            "active_section": "handoffs",
            "tenant_scope_note": tenant_scope_note(request.user),
            "form": form,
            "page_obj": page,
            "querystring": clean_querystring(request.GET),
        },
    )


@login_required(login_url="/admin/login/")
def handoff_detail(request, pk):
    _require_staff_scope(request.user)
    handoff = get_handoff_detail(pk)
    return render(
        request,
        "operations_portal/handoff_detail.html",
        {
            "active_section": "handoffs",
            "tenant_scope_note": tenant_scope_note(request.user),
            "handoff": handoff,
        },
    )


@login_required(login_url="/admin/login/")
def update_handoff_status(request, pk):
    _require_staff_scope(request.user)
    if request.method != "POST":
        raise PermissionDenied

    handoff = get_handoff_detail(pk)
    target_status = request.POST.get("status")
    if not is_valid_handoff_transition(handoff, target_status):
        messages.error(request, "Transição de handoff inválida.")
        return redirect("operations_portal:handoff_detail", pk=handoff.pk)

    service = HandoffService()
    if target_status == HandoffRequest.Status.SENT:
        service.mark_sent(handoff)
        messages.success(request, "Handoff marcado como notificado.")
    elif target_status == HandoffRequest.Status.RESOLVED:
        service.mark_resolved(handoff)
        messages.success(request, "Handoff marcado como resolvido.")
    elif target_status == HandoffRequest.Status.CANCELLED:
        handoff.status = HandoffRequest.Status.CANCELLED
        handoff.save(update_fields=["status", "updated_at"])
        messages.success(request, "Handoff cancelado.")
    return redirect("operations_portal:handoff_detail", pk=handoff.pk)

@login_required(login_url="/admin/login/")
def placeholder(request, section):
    _require_staff_scope(request.user)
    title = PLACEHOLDERS.get(section)
    if title is None:
        raise PermissionDenied
    return render(
        request,
        "operations_portal/placeholder.html",
        {
            "title": title,
            "active_section": section,
            "tenant_scope_note": tenant_scope_note(request.user),
        },
    )


def _require_staff_scope(user):
    if not user.is_staff:
        raise PermissionDenied
    if not has_secure_portal_scope(user):
        raise PermissionDenied
