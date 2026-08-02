from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import redirect, render

from audit.models import (
    ACTION_ASSISTANT_PROFILE_UPDATED,
    ACTION_HANDOFF_STATUS_CHANGED,
    ACTION_LEAD_CRM_DISPATCH_RETRIED,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from conversations.models import HandoffRequest
from leads.services.handoff import HandoffService
from tenants.access import (
    CAPABILITY_ASSISTANT_PROFILE_CHANGE,
    CAPABILITY_ASSISTANT_PROFILE_VIEW,
    CAPABILITY_CONVERSATIONS_VIEW,
    CAPABILITY_HANDOFFS_CHANGE_STATUS,
    CAPABILITY_HANDOFFS_VIEW,
    CAPABILITY_LEADS_RETRY_CRM,
    CAPABILITY_LEADS_VIEW,
    CAPABILITY_PORTAL_VIEW_DASHBOARD,
)
from tenants.models import AssistantProfile, Tenant

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .crm_retry import execute_portal_crm_retry
from .forms import ConversationFilterForm, HandoffFilterForm, HumanHandoffSettingsForm, LeadFilterForm
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
)

PLACEHOLDERS = {
    "tenants": "Tenants",
    "base-de-conhecimento": "Base de conhecimento",
    "integracoes": "Integrações",
    "configuracoes": "Configurações",
}


@login_required(login_url="/admin/login/")
def dashboard(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    context = get_dashboard_context(request.GET.get("period"), tenant=access.tenant, user=request.user)
    context.update({"active_section": "overview"})
    context.update(portal_template_context(access))
    return render(request, "operations_portal/dashboard.html", context)


@login_required(login_url="/admin/login/")
def conversation_list(request):
    access = resolve_portal_access(request, capability=CAPABILITY_CONVERSATIONS_VIEW, allow_global=True)
    form = ConversationFilterForm(request.GET or None, tenant_queryset=Tenant.objects.filter(pk__in=[t.pk for t in access.accessible_tenants]))
    page = get_conversation_list(form, page_number=request.GET.get("page", 1), tenant=access.tenant)
    context = {
        "active_section": "conversas",
        "form": form,
        "page_obj": page,
        "querystring": clean_querystring(request.GET),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/conversation_list.html", context)


@login_required(login_url="/admin/login/")
def conversation_detail(request, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_CONVERSATIONS_VIEW, allow_global=True)
    conversation = get_conversation_detail(pk, tenant=access.tenant)
    context = {"active_section": "conversas", "conversation": conversation}
    context.update(portal_template_context(access))
    return render(request, "operations_portal/conversation_detail.html", context)


@login_required(login_url="/admin/login/")
def lead_list(request):
    access = resolve_portal_access(request, capability=CAPABILITY_LEADS_VIEW, allow_global=True)
    form = LeadFilterForm(request.GET or None, tenant_queryset=Tenant.objects.filter(pk__in=[t.pk for t in access.accessible_tenants]))
    page = get_lead_list(form, page_number=request.GET.get("page", 1), tenant=access.tenant)
    context = {
        "active_section": "leads",
        "form": form,
        "page_obj": page,
        "querystring": clean_querystring(request.GET),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/lead_list.html", context)


@login_required(login_url="/admin/login/")
def lead_detail(request, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_LEADS_VIEW, allow_global=True)
    lead = get_lead_detail(pk, tenant=access.tenant)
    lead.can_retry_crm_dispatch = lead.can_retry_crm_dispatch and (CAPABILITY_LEADS_RETRY_CRM in access.capabilities)
    context = {"active_section": "leads", "lead": lead}
    context.update(portal_template_context(access))
    return render(request, "operations_portal/lead_detail.html", context)


@login_required(login_url="/admin/login/")
def retry_lead_crm_dispatch(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    access = resolve_portal_access(request, capability=CAPABILITY_LEADS_RETRY_CRM, allow_global=True)
    lead = get_lead_detail(pk, tenant=access.tenant)
    require_portal_capability(access, CAPABILITY_LEADS_RETRY_CRM)
    if not can_retry_crm_dispatch(lead):
        messages.warning(request, "Este lead não pode ser reprocessado porque já foi enviado ou não está em falha.")
        return redirect("operations_portal:lead_detail", pk=lead.pk)
    before_data = audit_model_snapshot(lead, fields=["status", "crm_error", "crm_external_id", "sent_to_crm_at"])
    try:
        outcome = execute_portal_crm_retry(lead=lead)
        locked = outcome.lead
        event = outcome.event
        if outcome.code == "blocked_active":
            messages.info(request, "Já existe processamento em andamento para este lead.")
            _audit_lead_retry(
                request=request,
                lead=locked,
                before_data=before_data,
                metadata={
                    "outcome": "blocked_active",
                    "outbox_event_id": str(event.event_id),
                    "outbox_status": event.status,
                },
            )
            return redirect("operations_portal:lead_detail", pk=locked.pk)
        if outcome.code == "already_retry_scheduled":
            messages.info(request, "Este lead já possui retry agendado na outbox.")
            _audit_lead_retry(
                request=request,
                lead=locked,
                before_data=before_data,
                metadata={
                    "outcome": "already_retry_scheduled",
                    "outbox_event_id": str(event.event_id),
                    "outbox_status": event.status,
                },
            )
            return redirect("operations_portal:lead_detail", pk=locked.pk)
        if outcome.code == "blocked_succeeded":
            messages.warning(
                request,
                "Este lead já foi concluído na outbox e não pode ser reenfileirado por aqui.",
            )
            _audit_lead_retry(
                request=request,
                lead=locked,
                before_data=before_data,
                metadata={
                    "outcome": "blocked_succeeded",
                    "outbox_event_id": str(event.event_id),
                    "outbox_status": event.status,
                },
            )
            return redirect("operations_portal:lead_detail", pk=locked.pk)
        if outcome.code == "requeued":
            messages.success(request, "Lead reenfileirado com sucesso na outbox.")
        elif outcome.code == "enqueued":
            messages.success(request, "Reprocessamento enfileirado com sucesso.")
        else:
            messages.success(request, "Reprocessamento já estava enfileirado.")
        _audit_lead_retry(
            request=request,
            lead=locked,
            before_data=before_data,
            metadata={
                "outcome": outcome.code,
                "outbox_event_id": str(event.event_id) if event is not None else "",
                "outbox_created": outcome.created,
                "outbox_status": event.status if event is not None else "",
                "conversation_id": locked.conversation_id,
            },
        )
        return redirect("operations_portal:lead_detail", pk=locked.pk)
    except (OperationalError, ProgrammingError):
        messages.error(
            request,
            "Não foi possível reenfileirar: outbox indisponível neste banco local. Verifique migrations antes de tentar novamente.",
        )
        return redirect("operations_portal:lead_detail", pk=lead.pk)


@login_required(login_url="/admin/login/")
def handoff_list(request):
    access = resolve_portal_access(request, capability=CAPABILITY_HANDOFFS_VIEW, allow_global=True)
    form = HandoffFilterForm(request.GET or None, tenant_queryset=Tenant.objects.filter(pk__in=[t.pk for t in access.accessible_tenants]))
    page = get_handoff_list(form, page_number=request.GET.get("page", 1), tenant=access.tenant)
    context = {
        "active_section": "handoffs",
        "form": form,
        "page_obj": page,
        "querystring": clean_querystring(request.GET),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/handoff_list.html", context)


@login_required(login_url="/admin/login/")
def handoff_detail(request, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_HANDOFFS_VIEW, allow_global=True)
    handoff = get_handoff_detail(pk, tenant=access.tenant)
    if CAPABILITY_HANDOFFS_CHANGE_STATUS not in access.capabilities:
        handoff.transition_options = []
    context = {"active_section": "handoffs", "handoff": handoff}
    context.update(portal_template_context(access))
    return render(request, "operations_portal/handoff_detail.html", context)


@login_required(login_url="/admin/login/")
def update_handoff_status(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    access = resolve_portal_access(request, capability=CAPABILITY_HANDOFFS_CHANGE_STATUS, allow_global=True)
    handoff = get_handoff_detail(pk, tenant=access.tenant)
    require_portal_capability(access, CAPABILITY_HANDOFFS_CHANGE_STATUS)
    target_status = request.POST.get("status")
    if not is_valid_handoff_transition(handoff, target_status):
        messages.error(request, "Transição de handoff inválida.")
        return redirect("operations_portal:handoff_detail", pk=handoff.pk)

    before_data = audit_model_snapshot(handoff, fields=["status", "resolved_at"])
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
    handoff.refresh_from_db()
    record_audit_event(
        action=ACTION_HANDOFF_STATUS_CHANGED,
        actor=request.user,
        tenant=handoff.tenant,
        obj=handoff,
        before_data=before_data,
        after_data=audit_model_snapshot(handoff, fields=["status", "resolved_at"]),
        metadata={"target_status": target_status},
        request=request,
    )
    return redirect("operations_portal:handoff_detail", pk=handoff.pk)


@login_required(login_url="/admin/login/")
def settings_view(request):
    access = resolve_portal_access(request, capability=CAPABILITY_ASSISTANT_PROFILE_VIEW, allow_global=False, require_tenant=True)
    selected_tenant = access.tenant
    profile, _ = AssistantProfile.objects.get_or_create(tenant=selected_tenant)
    can_change = CAPABILITY_ASSISTANT_PROFILE_CHANGE in access.capabilities

    if request.method == "POST":
        require_portal_capability(access, CAPABILITY_ASSISTANT_PROFILE_CHANGE)
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(selected_tenant.pk):
            raise PermissionDenied
        form = HumanHandoffSettingsForm(request.POST, instance=profile)
        if form.is_valid():
            audit_fields = list(form.fields.keys())
            before_data = audit_model_snapshot(AssistantProfile.objects.get(pk=profile.pk), fields=audit_fields)
            form.save()
            profile.refresh_from_db()
            changes = changed_fields(before_data, audit_model_snapshot(profile, fields=audit_fields))
            if changes["before"] or changes["after"]:
                record_audit_event(
                    action=ACTION_ASSISTANT_PROFILE_UPDATED,
                    actor=request.user,
                    tenant=selected_tenant,
                    obj=profile,
                    before_data=changes["before"],
                    after_data=changes["after"],
                    metadata={"source": "operations_portal.settings"},
                    request=request,
                )
            messages.success(request, "Configuração de atendimento humano atualizada.")
            return redirect(f"{request.path}?tenant={selected_tenant.pk}")
        messages.error(request, "Revise os campos destacados antes de salvar.")
    else:
        form = HumanHandoffSettingsForm(instance=profile)

    if not can_change:
        for field in form.fields.values():
            field.disabled = True

    context = {
        "active_section": "configuracoes",
        "tenants": access.accessible_tenants,
        "selected_tenant": selected_tenant,
        "form": form,
        "can_change_settings": can_change,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/settings.html", context)


@login_required(login_url="/admin/login/")
def placeholder(request, section):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=True)
    title = PLACEHOLDERS.get(section)
    if title is None:
        raise PermissionDenied
    context = {"title": title, "active_section": section}
    context.update(portal_template_context(access))
    return render(request, "operations_portal/placeholder.html", context)


def _audit_lead_retry(*, request, lead, before_data, metadata):
    record_audit_event(
        action=ACTION_LEAD_CRM_DISPATCH_RETRIED,
        actor=request.user,
        tenant=lead.tenant,
        obj=lead,
        before_data=before_data,
        after_data=audit_model_snapshot(lead, fields=["status", "crm_error", "crm_external_id", "sent_to_crm_at"]),
        metadata=metadata,
        request=request,
    )
