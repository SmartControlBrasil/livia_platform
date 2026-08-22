from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import redirect, render

from audit.models import (
    ACTION_ASSISTANT_PROFILE_UPDATED,
    ACTION_TENANT_CREATED,
    ACTION_TENANT_ORIGIN_CREATED,
    ACTION_TENANT_ORIGIN_DEACTIVATED,
    ACTION_TENANT_UPDATED,
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
    CAPABILITY_MEMBERSHIPS_MANAGE,
    CAPABILITY_PORTAL_VIEW_DASHBOARD,
)
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.onboarding import TenantOnboardingService

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .crm_retry import execute_portal_crm_retry
from .forms import (
    AssistantProfilePortalForm,
    AssistantSettingsProfileForm,
    ConversationFilterForm,
    HandoffFilterForm,
    HumanHandoffSettingsForm,
    LeadFilterForm,
    TenantAllowedOriginsPortalForm,
    TenantPortalFilterForm,
    TenantPortalForm,
)
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


TENANT_AUDIT_FIELDS = ["name", "slug", "domain", "is_active"]
ASSISTANT_PROFILE_PORTAL_AUDIT_FIELDS = [
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


def _tenant_management_access(request, *, write=False):
    capability = CAPABILITY_MEMBERSHIPS_MANAGE if write else CAPABILITY_PORTAL_VIEW_DASHBOARD
    return resolve_portal_access(request, capability=capability, allow_global=True)


def _tenant_queryset_for_access(access):
    if access.is_global:
        return Tenant.objects.all().order_by("name")
    return Tenant.objects.filter(pk__in=[tenant.pk for tenant in access.accessible_tenants]).order_by("name")


def _get_portal_tenant_or_404(access, pk):
    from django.shortcuts import get_object_or_404

    return get_object_or_404(_tenant_queryset_for_access(access), pk=pk)


@login_required(login_url="/admin/login/")
def tenant_list(request):
    access = _tenant_management_access(request)
    queryset = (
        _tenant_queryset_for_access(access)
        .select_related("assistant_profile")
        .annotate(knowledge_document_count=Count("knowledge_documents", distinct=True))
    )
    form = TenantPortalFilterForm(request.GET or None)
    if form.is_valid():
        status = form.cleaned_data.get("status")
        query = form.cleaned_data.get("q")
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(slug__icontains=query)
                | Q(domain__icontains=query)
                | Q(assistant_profile__business_domain__icontains=query)
                | Q(assistant_profile__business_name__icontains=query)
            )
    page = Paginator(queryset, 25).get_page(request.GET.get("page", 1))
    context = {
        "active_section": "tenants",
        "form": form,
        "page_obj": page,
        "querystring": clean_querystring(request.GET),
        "can_manage_tenants": CAPABILITY_MEMBERSHIPS_MANAGE in access.capabilities,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenants/list.html", context)


@login_required(login_url="/admin/login/")
def tenant_create(request):
    access = _tenant_management_access(request, write=True)
    require_portal_capability(access, CAPABILITY_MEMBERSHIPS_MANAGE)
    if request.method == "POST":
        tenant_form = TenantPortalForm(request.POST, prefix="tenant")
        profile_form = AssistantProfilePortalForm(request.POST, prefix="profile")
        origins_form = TenantAllowedOriginsPortalForm(request.POST)
        if tenant_form.is_valid() and profile_form.is_valid() and origins_form.is_valid():
            cleaned_profile = profile_form.cleaned_data
            with transaction.atomic():
                result = TenantOnboardingService().onboard(
                    slug=tenant_form.cleaned_data["slug"],
                    name=tenant_form.cleaned_data["name"],
                    domain=tenant_form.cleaned_data["domain"],
                    assistant_name=cleaned_profile.get("name") or "Lívia",
                    initial_message=cleaned_profile.get("initial_message") or "Olá! Sou a Lívia. Como posso te ajudar?",
                    primary_goal=cleaned_profile.get("primary_goal") or "qualificar leads",
                    tone=cleaned_profile.get("tone") or "consultivo, claro e profissional",
                    business_domain=cleaned_profile.get("business_domain") or "",
                    short_description=cleaned_profile.get("short_description") or "",
                    use_ai=bool(cleaned_profile.get("use_ai")),
                    widget_title=cleaned_profile.get("widget_title") or "",
                    launcher_label=cleaned_profile.get("launcher_label") or "Fale com a Lívia",
                    primary_color=cleaned_profile.get("primary_color") or "#2563eb",
                    position=cleaned_profile.get("position") or "bottom_right",
                    placeholder_text=cleaned_profile.get("placeholder_text") or "Digite sua mensagem...",
                    widget_enabled=bool(cleaned_profile.get("is_widget_enabled")),
                    allowed_origins=origins_form.cleaned_data.get("origins") or [],
                )
                tenant = result.tenant
                tenant.is_active = bool(tenant_form.cleaned_data.get("is_active"))
                tenant.save(update_fields=["is_active", "updated_at"])
                profile = result.assistant_profile
                profile.business_name = cleaned_profile.get("business_name") or ""
                profile.show_branding = bool(cleaned_profile.get("show_branding"))
                profile.full_clean()
                profile.save(update_fields=["business_name", "show_branding", "updated_at"])
                record_audit_event(
                    action=ACTION_TENANT_CREATED,
                    actor=request.user,
                    tenant=tenant,
                    obj=tenant,
                    before_data={},
                    after_data=audit_model_snapshot(tenant, fields=TENANT_AUDIT_FIELDS),
                    metadata={"source": "operations_portal.tenants.create"},
                    request=request,
                )
            messages.success(request, "Tenant criado com sucesso.")
            return redirect("operations_portal:tenant_detail", pk=tenant.pk)
        messages.error(request, "Revise os campos destacados antes de criar o tenant.")
    else:
        tenant_form = TenantPortalForm(initial={"is_active": True}, prefix="tenant")
        profile_form = AssistantProfilePortalForm(prefix="profile")
        origins_form = TenantAllowedOriginsPortalForm()
    context = {
        "active_section": "tenants",
        "mode": "create",
        "tenant_form": tenant_form,
        "profile_form": profile_form,
        "origins_form": origins_form,
        "can_manage_tenants": True,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenants/form.html", context)


@login_required(login_url="/admin/login/")
def tenant_detail(request, pk):
    write = request.method == "POST"
    access = _tenant_management_access(request, write=write)
    tenant = _get_portal_tenant_or_404(access, pk)
    profile, _ = AssistantProfile.objects.get_or_create(tenant=tenant)
    can_manage = CAPABILITY_MEMBERSHIPS_MANAGE in access.capabilities
    if request.method == "POST":
        require_portal_capability(access, CAPABILITY_MEMBERSHIPS_MANAGE)
        action = request.POST.get("action")
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(tenant.pk):
            raise PermissionDenied
        if action == "save_general":
            form = TenantPortalForm(request.POST, instance=tenant, editing=True, prefix="tenant")
            if form.is_valid():
                before = audit_model_snapshot(Tenant.objects.get(pk=tenant.pk), fields=TENANT_AUDIT_FIELDS)
                saved = form.save()
                changes = changed_fields(before, audit_model_snapshot(saved, fields=TENANT_AUDIT_FIELDS))
                if changes["before"] or changes["after"]:
                    record_audit_event(
                        action=ACTION_TENANT_UPDATED,
                        actor=request.user,
                        tenant=saved,
                        obj=saved,
                        before_data=changes["before"],
                        after_data=changes["after"],
                        metadata={"source": "operations_portal.tenants.general"},
                        request=request,
                    )
                messages.success(request, "Dados gerais do tenant atualizados.")
                return redirect("operations_portal:tenant_detail", pk=tenant.pk)
            messages.error(request, "Revise os dados gerais antes de salvar.")
        elif action == "save_assistant":
            form = AssistantProfilePortalForm(
                _profile_portal_post_data(profile, request.POST, scope=request.POST.get("profile_scope")),
                instance=profile,
                prefix="profile",
            )
            if form.is_valid():
                before = audit_model_snapshot(AssistantProfile.objects.get(pk=profile.pk), fields=ASSISTANT_PROFILE_PORTAL_AUDIT_FIELDS)
                saved = form.save()
                changes = changed_fields(before, audit_model_snapshot(saved, fields=ASSISTANT_PROFILE_PORTAL_AUDIT_FIELDS))
                if changes["before"] or changes["after"]:
                    record_audit_event(
                        action=ACTION_ASSISTANT_PROFILE_UPDATED,
                        actor=request.user,
                        tenant=tenant,
                        obj=saved,
                        before_data=changes["before"],
                        after_data=changes["after"],
                        metadata={"source": "operations_portal.tenants.assistant"},
                        request=request,
                    )
                messages.success(request, "Perfil da assistente atualizado.")
                return redirect("operations_portal:tenant_detail", pk=tenant.pk)
            messages.error(request, "Revise o perfil da assistente antes de salvar.")
        elif action == "save_origins":
            form = TenantAllowedOriginsPortalForm(request.POST, tenant=tenant)
            if form.is_valid():
                _sync_allowed_origins(request=request, tenant=tenant, origins=form.cleaned_data["origins"])
                messages.success(request, "Allowed origins atualizadas.")
                return redirect("operations_portal:tenant_detail", pk=tenant.pk)
            messages.error(request, "Revise as allowed origins antes de salvar.")
        else:
            raise PermissionDenied

    tenant_form = TenantPortalForm(instance=tenant, editing=True, prefix="tenant")
    profile_form = AssistantProfilePortalForm(instance=profile, prefix="profile")
    origins_form = TenantAllowedOriginsPortalForm(tenant=tenant)
    if not can_manage:
        for form in (tenant_form, profile_form, origins_form):
            for field in form.fields.values():
                field.disabled = True
    install_package = TenantInstallPackageService().build_for_tenant(tenant)
    from knowledge_base.models import KnowledgeDocument, TenantRagChunkEmbedding, TenantRagDocumentChunk, TenantRagDriveTextStaging

    knowledge_counts = {
        "documents": KnowledgeDocument.objects.filter(tenant=tenant).count(),
        "active_documents": KnowledgeDocument.objects.filter(tenant=tenant, status=KnowledgeDocument.Status.ACTIVE).count(),
        "staging": TenantRagDriveTextStaging.objects.filter(tenant=tenant).count(),
        "chunks": TenantRagDocumentChunk.objects.filter(tenant=tenant).count(),
        "embeddings": TenantRagChunkEmbedding.objects.filter(tenant=tenant, is_active=True).count(),
    }
    context = {
        "active_section": "tenants",
        "tenant_obj": tenant,
        "profile": profile,
        "tenant_form": tenant_form,
        "profile_form": profile_form,
        "origins_form": origins_form,
        "install_package": install_package,
        "knowledge_counts": knowledge_counts,
        "config_endpoint": f"/api/widget/config/?tenant={tenant.slug}",
        "can_manage_tenants": can_manage,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenants/detail.html", context)


def _sync_allowed_origins(*, request, tenant, origins):
    desired = set(origins)
    existing = {item.origin: item for item in tenant.allowed_origins.all()}
    for origin in sorted(desired):
        item = existing.get(origin)
        if item is None:
            item = TenantAllowedOrigin.objects.create(
                tenant=tenant,
                origin=origin,
                is_active=True,
                created_by=request.user,
            )
            record_audit_event(
                action=ACTION_TENANT_ORIGIN_CREATED,
                actor=request.user,
                tenant=tenant,
                obj=item,
                before_data={},
                after_data=audit_model_snapshot(item, fields=["origin", "is_active"]),
                metadata={"source": "operations_portal.tenants.origins"},
                request=request,
            )
            continue
        if not item.is_active:
            before = audit_model_snapshot(item, fields=["origin", "is_active"])
            item.is_active = True
            item.save(update_fields=["is_active", "updated_at"])
            record_audit_event(
                action=ACTION_TENANT_ORIGIN_CREATED,
                actor=request.user,
                tenant=tenant,
                obj=item,
                before_data=before,
                after_data=audit_model_snapshot(item, fields=["origin", "is_active"]),
                metadata={"source": "operations_portal.tenants.origins.reactivate"},
                request=request,
            )
    for origin, item in existing.items():
        if origin in desired or not item.is_active:
            continue
        before = audit_model_snapshot(item, fields=["origin", "is_active"])
        item.is_active = False
        item.save(update_fields=["is_active", "updated_at"])
        record_audit_event(
            action=ACTION_TENANT_ORIGIN_DEACTIVATED,
            actor=request.user,
            tenant=tenant,
            obj=item,
            before_data=before,
            after_data=audit_model_snapshot(item, fields=["origin", "is_active"]),
            metadata={"source": "operations_portal.tenants.origins"},
            request=request,
        )


def _profile_portal_post_data(profile, data, *, scope=None):
    payload = data.copy()
    form = AssistantProfilePortalForm(instance=profile, prefix="profile")
    widget_checkbox_fields = {"show_branding", "is_widget_enabled", "use_ai"}
    for name, field in form.fields.items():
        key = form.add_prefix(name)
        if key in payload:
            continue
        if scope == "widget" and name in widget_checkbox_fields:
            continue
        value = getattr(profile, name, "")
        if isinstance(field.widget, forms.CheckboxInput):
            if value:
                payload[key] = "on"
            continue
        payload[key] = "" if value is None else str(value)
    return payload


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
        action = request.POST.get("action")
        if action == "save_profile":
            profile_form = AssistantSettingsProfileForm(request.POST, instance=profile, prefix="profile")
            handoff_form = HumanHandoffSettingsForm(instance=profile, prefix="handoff")
            if profile_form.is_valid():
                before_data = audit_model_snapshot(AssistantProfile.objects.get(pk=profile.pk), fields=ASSISTANT_PROFILE_PORTAL_AUDIT_FIELDS)
                saved = profile_form.save()
                changes = changed_fields(before_data, audit_model_snapshot(saved, fields=ASSISTANT_PROFILE_PORTAL_AUDIT_FIELDS))
                if changes["before"] or changes["after"]:
                    record_audit_event(
                        action=ACTION_ASSISTANT_PROFILE_UPDATED,
                        actor=request.user,
                        tenant=selected_tenant,
                        obj=saved,
                        before_data=changes["before"],
                        after_data=changes["after"],
                        metadata={"source": "operations_portal.settings.profile"},
                        request=request,
                    )
                messages.success(request, "Configurações da assistente atualizadas.")
                return redirect(f"{request.path}?tenant={selected_tenant.pk}")
            messages.error(request, "Revise as configurações da assistente antes de salvar.")
        elif action == "save_handoff":
            profile_form = AssistantSettingsProfileForm(instance=profile, prefix="profile")
            handoff_form = HumanHandoffSettingsForm(request.POST, instance=profile, prefix="handoff")
            if handoff_form.is_valid():
                audit_fields = list(handoff_form.fields.keys())
                before_data = audit_model_snapshot(AssistantProfile.objects.get(pk=profile.pk), fields=audit_fields)
                handoff_form.save()
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
                        metadata={"source": "operations_portal.settings.handoff"},
                        request=request,
                    )
                messages.success(request, "Configuração de atendimento humano atualizada.")
                return redirect(f"{request.path}?tenant={selected_tenant.pk}")
            messages.error(request, "Revise o atendimento humano antes de salvar.")
        else:
            raise PermissionDenied
    else:
        profile_form = AssistantSettingsProfileForm(instance=profile, prefix="profile")
        handoff_form = HumanHandoffSettingsForm(instance=profile, prefix="handoff")

    if not can_change:
        for form in (profile_form, handoff_form):
            for field in form.fields.values():
                field.disabled = True

    context = {
        "active_section": "configuracoes",
        "tenants": access.accessible_tenants,
        "selected_tenant": selected_tenant,
        "profile": profile,
        "profile_form": profile_form,
        "handoff_form": handoff_form,
        "can_change_settings": can_change,
        **_assistant_settings_operational_state(selected_tenant),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/settings.html", context)


def _assistant_settings_operational_state(tenant):
    from knowledge_base.models import (
        KnowledgeDocument,
        TenantRagChunkEmbedding,
        TenantRagConfiguration,
        TenantRagDocumentChunk,
        TenantRagDriveTextStaging,
        TenantRagIndexRun,
    )

    rag_config = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    install_package = TenantInstallPackageService().build_for_tenant(tenant)
    latest_index_run = TenantRagIndexRun.objects.filter(tenant=tenant).order_by("-started_at").first()
    knowledge_counts = {
        "documents": KnowledgeDocument.objects.filter(tenant=tenant).count(),
        "active_documents": KnowledgeDocument.objects.filter(tenant=tenant, status=KnowledgeDocument.Status.ACTIVE).count(),
        "staging": TenantRagDriveTextStaging.objects.filter(tenant=tenant).count(),
        "chunks": TenantRagDocumentChunk.objects.filter(tenant=tenant).count(),
        "active_chunks": TenantRagDocumentChunk.objects.filter(tenant=tenant, is_active=True).count(),
        "embeddings": TenantRagChunkEmbedding.objects.filter(tenant=tenant, is_active=True).count(),
    }
    return {
        "allowed_origins": list(tenant.allowed_origins.filter(is_active=True).order_by("origin")),
        "config_endpoint": f"/api/widget/config/?tenant={tenant.slug}",
        "install_package": install_package,
        "knowledge_counts": knowledge_counts,
        "latest_index_run": latest_index_run,
        "rag_config": rag_config,
    }


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
