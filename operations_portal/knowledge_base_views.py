from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from audit.models import ACTION_TENANT_RAG_CONFIGURED, ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from knowledge_base.models import TenantRagConfiguration
from tenants.access import (
    CAPABILITY_KNOWLEDGE_BASE_CONFIGURE,
    CAPABILITY_KNOWLEDGE_BASE_OPERATE,
    CAPABILITY_KNOWLEDGE_BASE_REINDEX,
    CAPABILITY_KNOWLEDGE_BASE_VIEW,
)

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .forms import (
    KnowledgeBaseOperationRequestForm,
    KnowledgeChunkFilterForm,
    KnowledgeDiagnosticSearchForm,
    KnowledgeDocumentFilterForm,
    TenantRagConfigurationPortalForm,
)
from .knowledge_base_selectors import (
    get_knowledge_chunk_list,
    get_knowledge_document_list,
    get_knowledge_retrieval_events,
    get_operation_request_detail,
    get_operation_request_list,
    get_tenant_rag_configuration,
)
from .knowledge_base_services import (
    build_dashboard_metrics,
    build_operations_dashboard,
    compute_effective_rag_limits,
    run_diagnostic_search,
)
from knowledge_base.rag.operations import RagOperationsError, create_operation_request
from .selectors import clean_querystring

KNOWLEDGE_BASE_AUDIT_FIELDS = [
    "retrieval_enabled",
    "min_similarity_score",
    "max_retrieved_chunks",
    "max_context_chars",
    "retrieval_timeout_seconds",
]


def _resolve_knowledge_base_access(request, *, configure=False):
    capability = CAPABILITY_KNOWLEDGE_BASE_CONFIGURE if configure else CAPABILITY_KNOWLEDGE_BASE_VIEW
    return resolve_portal_access(
        request,
        capability=capability,
        allow_global=False,
        require_tenant=True,
    )


def _knowledge_base_context(access, *, sub_section: str, **extra):
    context = {
        "active_section": "base-de-conhecimento",
        "kb_sub_section": sub_section,
        "selected_tenant": access.tenant,
        "can_configure_kb": CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in access.capabilities,
    }
    context.update(extra)
    context.update(portal_template_context(access))
    return context


@login_required(login_url="/admin/login/")
def knowledge_base_dashboard(request):
    access = _resolve_knowledge_base_access(request)
    configuration = get_tenant_rag_configuration(access.tenant)
    metrics = build_dashboard_metrics(tenant=access.tenant, configuration=configuration)
    return render(
        request,
        "operations_portal/knowledge_base/dashboard.html",
        _knowledge_base_context(access, sub_section="dashboard", configuration=configuration, metrics=metrics),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_documents(request):
    access = _resolve_knowledge_base_access(request)
    form = KnowledgeDocumentFilterForm(request.GET or None)
    page = get_knowledge_document_list(tenant=access.tenant, form=form, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/knowledge_base/documents.html",
        _knowledge_base_context(
            access,
            sub_section="documents",
            form=form,
            page_obj=page,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_chunks(request):
    access = _resolve_knowledge_base_access(request)
    form = KnowledgeChunkFilterForm(request.GET or None, tenant=access.tenant)
    page = get_knowledge_chunk_list(tenant=access.tenant, form=form, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/knowledge_base/chunks.html",
        _knowledge_base_context(
            access,
            sub_section="chunks",
            form=form,
            page_obj=page,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_config(request):
    access = _resolve_knowledge_base_access(request)
    configuration = get_tenant_rag_configuration(access.tenant)
    limits = compute_effective_rag_limits(configuration=configuration)
    can_change = CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in access.capabilities

    if configuration is None:
        return render(
            request,
            "operations_portal/knowledge_base/config.html",
            _knowledge_base_context(
                access,
                sub_section="config",
                configuration=None,
                limits=limits,
                form=None,
                can_change_settings=False,
            ),
        )

    if request.method == "POST":
        require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
            raise PermissionDenied
        form = TenantRagConfigurationPortalForm(request.POST, instance=configuration, global_limits=limits)
        if form.is_valid():
            before_data = audit_model_snapshot(
                TenantRagConfiguration.objects.get(pk=configuration.pk),
                fields=KNOWLEDGE_BASE_AUDIT_FIELDS,
            )
            saved = form.save()
            changes = changed_fields(before_data, audit_model_snapshot(saved, fields=KNOWLEDGE_BASE_AUDIT_FIELDS))
            if changes["before"] or changes["after"]:
                record_audit_event(
                    action=ACTION_TENANT_RAG_CONFIGURED,
                    actor=request.user,
                    tenant=access.tenant,
                    obj=saved,
                    before_data=changes["before"],
                    after_data=changes["after"],
                    metadata={"source": "operations_portal.knowledge_base_config"},
                    request=request,
                )
            messages.success(request, "Configuração RAG atualizada.")
            return redirect(f"{request.path}?tenant={access.tenant.pk}")
        messages.error(request, "Revise os campos destacados antes de salvar.")
    else:
        form = TenantRagConfigurationPortalForm(instance=configuration, global_limits=limits)

    if not can_change:
        for field in form.fields.values():
            field.disabled = True

    return render(
        request,
        "operations_portal/knowledge_base/config.html",
        _knowledge_base_context(
            access,
            sub_section="config",
            configuration=configuration,
            limits=limits,
            form=form,
            can_change_settings=can_change,
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_diagnostic(request):
    access = _resolve_knowledge_base_access(request)
    result = None
    if request.method == "POST":
        form = KnowledgeDiagnosticSearchForm(request.POST)
        if form.is_valid():
            result = run_diagnostic_search(tenant=access.tenant, query=form.cleaned_data["query"])
            record_audit_event(
                action=ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH,
                actor=request.user,
                tenant=access.tenant,
                obj=get_tenant_rag_configuration(access.tenant),
                before_data={},
                after_data={
                    "query_length": len(result.query),
                    "status": result.retrieval.status,
                    "reason": result.retrieval.reason,
                    "result_count": len(result.evidence),
                },
                metadata={"source": "operations_portal.knowledge_base_diagnostic"},
                request=request,
            )
    else:
        form = KnowledgeDiagnosticSearchForm()

    return render(
        request,
        "operations_portal/knowledge_base/diagnostic.html",
        _knowledge_base_context(access, sub_section="diagnostic", form=form, result=result),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_events(request):
    access = _resolve_knowledge_base_access(request)
    page = get_knowledge_retrieval_events(tenant=access.tenant, page_number=request.GET.get("page", 1))
    return render(
        request,
        "operations_portal/knowledge_base/events.html",
        _knowledge_base_context(
            access,
            sub_section="events",
            page_obj=page,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_operations(request):
    access = _resolve_knowledge_base_access(request)
    configuration = get_tenant_rag_configuration(access.tenant)
    dashboard = build_operations_dashboard(tenant=access.tenant, configuration=configuration)
    history = get_operation_request_list(tenant=access.tenant, page_number=request.GET.get("page", 1))
    form = KnowledgeBaseOperationRequestForm()
    return render(
        request,
        "operations_portal/knowledge_base/operations.html",
        _knowledge_base_context(
            access,
            sub_section="operations",
            dashboard=dashboard,
            page_obj=history,
            querystring=clean_querystring(request.GET),
            form=form,
            can_operate=CAPABILITY_KNOWLEDGE_BASE_OPERATE in access.capabilities,
            can_reindex=CAPABILITY_KNOWLEDGE_BASE_REINDEX in access.capabilities,
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_operation_submit(request):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    form = KnowledgeBaseOperationRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Operação inválida.")
        return redirect(f"{reverse('operations_portal:knowledge_base_operations')}?tenant={access.tenant.pk}")

    operation = form.cleaned_data["operation"]
    if operation == "full_reindex":
        require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_REINDEX)
        if not form.cleaned_data.get("confirm_reindex"):
            messages.error(request, "Confirme explicitamente a reindexação completa antes de continuar.")
            return redirect(f"{reverse('operations_portal:knowledge_base_operations')}?tenant={access.tenant.pk}")
    else:
        require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)

    try:
        created = create_operation_request(
            tenant=access.tenant,
            operation=operation,
            requested_by=request.user,
            source="operations_portal.knowledge_base_operations",
        )
    except RagOperationsError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('operations_portal:knowledge_base_operations')}?tenant={access.tenant.pk}")

    messages.success(
        request,
        "Solicitação registrada. Execute o worker controlado para processar: "
        "python manage.py process_tenant_rag_operations",
    )
    return redirect(f"{reverse('operations_portal:knowledge_base_operation_detail', kwargs={'pk': created.pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_operation_detail(request, pk: int):
    access = _resolve_knowledge_base_access(request)
    detail = get_operation_request_detail(tenant=access.tenant, pk=pk)
    if detail is None:
        raise PermissionDenied
    return render(
        request,
        "operations_portal/knowledge_base/operation_detail.html",
        _knowledge_base_context(access, sub_section="operations", operation=detail),
    )
