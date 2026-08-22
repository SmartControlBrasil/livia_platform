from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from audit.models import (
    ACTION_KNOWLEDGE_DOCUMENT_CREATED,
    ACTION_KNOWLEDGE_DOCUMENT_UPDATED,
    ACTION_TENANT_RAG_CONFIGURED,
    ACTION_TENANT_RAG_DIAGNOSTIC_SEARCH,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration, TenantRagDriveFileManifest
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
    KnowledgeDocumentPortalForm,
    KnowledgeImportPortalForm,
    KnowledgeTextRetrievalForm,
    TenantRagConfigurationPortalForm,
)
from .knowledge_base_selectors import (
    get_knowledge_chunk_list,
    get_knowledge_document_counters,
    get_knowledge_document_detail,
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
from knowledge_base.rag.operational_alert_sync import (
    OperationalAlertError,
    acknowledge_operational_alert,
    count_open_operational_alerts,
    resolve_operational_alert,
    tenant_has_synced_alerts,
)
from knowledge_base.rag.operational_monitoring import process_operational_monitoring
from knowledge_base.models import OperationalMonitoringBatchRun
from .operational_alert_services import (
    get_maintenance_window_list,
    get_operational_alert_detail,
    get_operational_alert_list,
)
from knowledge_base.rag.alert_governance import build_tenant_governance_summary, SILENCE_PRESETS_HOURS
from knowledge_base.rag.alert_governance_services import (
    GovernanceError,
    assign_operational_alert,
    cancel_maintenance_window,
    cancel_operational_alert_silence,
    create_maintenance_window,
    silence_operational_alert,
)
from .rag_health_services import (
    build_rag_health_dashboard,
    get_health_ai_usage_page,
    get_health_operation_page,
    get_health_retrieval_page,
)
from .selectors import clean_querystring
from knowledge_base.rag.retriever import retrieve_relevant_knowledge
from knowledge_base.services.importing import (
    TenantKnowledgeImportError,
    build_document_item_from_upload,
    import_tenant_knowledge_items,
)
from knowledge_base.services.manual_rag import sync_manual_knowledge_document_to_rag, sync_manual_knowledge_documents_for_tenant

KNOWLEDGE_DOCUMENT_AUDIT_FIELDS = ["tenant", "title", "slug", "source_type", "source_url", "tags", "status"]


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
    from knowledge_base.rag.operational_work_queue import build_personal_work_count, build_work_queue_summary
    from tenants.access import get_active_membership

    membership = get_active_membership(access.user, access.tenant) if access.tenant else None
    work_queue_summary = build_work_queue_summary(tenant=access.tenant) if access.tenant else {}
    personal_work_count = (
        build_personal_work_count(tenant=access.tenant, membership=membership) if access.tenant and membership else 0
    )
    context = {
        "active_section": "base-de-conhecimento",
        "kb_sub_section": sub_section,
        "selected_tenant": access.tenant,
        "can_configure_kb": CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in access.capabilities,
        "can_operate_kb": CAPABILITY_KNOWLEDGE_BASE_OPERATE in access.capabilities,
        "open_alert_counts": count_open_operational_alerts(tenant=access.tenant) if access.tenant else {},
        "alerts_synced": tenant_has_synced_alerts(tenant=access.tenant) if access.tenant else False,
        "work_queue_summary": work_queue_summary,
        "personal_work_count": personal_work_count,
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
            counters=get_knowledge_document_counters(tenant=access.tenant),
            rag_manifests=TenantRagDriveFileManifest.objects.filter(tenant=access.tenant).order_by("-updated_at", "name")[:12],
            querystring=clean_querystring(request.GET),
            can_change_documents=CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in access.capabilities,
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_document_create(request):
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.method == "POST":
        form = KnowledgeDocumentPortalForm(request.POST, fixed_tenant=access.tenant)
        if form.is_valid():
            document = form.save()
            record_audit_event(
                action=ACTION_KNOWLEDGE_DOCUMENT_CREATED,
                actor=request.user,
                tenant=access.tenant,
                obj=document,
                before_data={},
                after_data=audit_model_snapshot(document, fields=KNOWLEDGE_DOCUMENT_AUDIT_FIELDS),
                metadata={"source": "operations_portal.knowledge_base_document_create"},
                request=request,
            )
            sync_manual_knowledge_document_to_rag(document=document)
            messages.success(request, "Documento criado com sucesso. Solicite processamento para atualizar chunks e embeddings.")
            return redirect(f"{reverse('operations_portal:knowledge_base_document_detail', kwargs={'pk': document.pk})}?tenant={access.tenant.pk}")
        messages.error(request, "Revise os campos destacados antes de criar o documento.")
    else:
        form = KnowledgeDocumentPortalForm(fixed_tenant=access.tenant, initial={"status": KnowledgeDocument.Status.ACTIVE, "source_type": "manual"})
    return render(
        request,
        "operations_portal/knowledge_base/document_form.html",
        _knowledge_base_context(access, sub_section="documents", form=form, mode="create"),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_document_detail(request, pk: int):
    access = _resolve_knowledge_base_access(request)
    document = get_knowledge_document_detail(tenant=access.tenant, pk=pk)
    if document is None:
        raise PermissionDenied
    retrieval_result = None
    if request.method == "POST":
        require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_VIEW)
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
            raise PermissionDenied
        form = KnowledgeTextRetrievalForm(request.POST)
        if form.is_valid():
            retrieval_result = retrieve_relevant_knowledge(access.tenant, form.cleaned_data["query"], limit=5)
    else:
        form = KnowledgeTextRetrievalForm()
    return render(
        request,
        "operations_portal/knowledge_base/document_detail.html",
        _knowledge_base_context(
            access,
            sub_section="documents",
            document=document,
            retrieval_form=form,
            retrieval_result=retrieval_result,
            counters=get_knowledge_document_counters(tenant=access.tenant),
            can_change_documents=CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in access.capabilities,
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_document_edit(request, pk: int):
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    document = get_knowledge_document_detail(tenant=access.tenant, pk=pk)
    if document is None:
        raise PermissionDenied
    if request.method == "POST":
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
            raise PermissionDenied
        form = KnowledgeDocumentPortalForm(request.POST, instance=document, fixed_tenant=access.tenant)
        if form.is_valid():
            before_data = audit_model_snapshot(KnowledgeDocument.objects.get(pk=document.pk), fields=KNOWLEDGE_DOCUMENT_AUDIT_FIELDS)
            saved = form.save()
            changes = changed_fields(before_data, audit_model_snapshot(saved, fields=KNOWLEDGE_DOCUMENT_AUDIT_FIELDS))
            if changes["before"] or changes["after"]:
                record_audit_event(
                    action=ACTION_KNOWLEDGE_DOCUMENT_UPDATED,
                    actor=request.user,
                    tenant=access.tenant,
                    obj=saved,
                    before_data=changes["before"],
                    after_data=changes["after"],
                    metadata={"source": "operations_portal.knowledge_base_document_edit"},
                    request=request,
                )
            sync_manual_knowledge_document_to_rag(document=saved)
            messages.success(request, "Documento atualizado. Solicite processamento para refletir a versão atual no índice RAG.")
            return redirect(f"{reverse('operations_portal:knowledge_base_document_detail', kwargs={'pk': saved.pk})}?tenant={access.tenant.pk}")
        messages.error(request, "Revise os campos destacados antes de salvar.")
    else:
        form = KnowledgeDocumentPortalForm(instance=document, fixed_tenant=access.tenant)
    return render(
        request,
        "operations_portal/knowledge_base/document_form.html",
        _knowledge_base_context(access, sub_section="documents", form=form, document=document, mode="edit"),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_document_import(request):
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    result = None
    if request.method == "POST":
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
            raise PermissionDenied
        form = KnowledgeImportPortalForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                item = build_document_item_from_upload(
                    form.cleaned_data["file"],
                    source_type=form.cleaned_data["source_type"],
                    tags=form.cleaned_data["tags_text"],
                    status=form.cleaned_data["status"],
                )
                result = import_tenant_knowledge_items(
                    tenant=access.tenant,
                    items=[item],
                    replace=form.cleaned_data.get("replace"),
                )
            except TenantKnowledgeImportError as exc:
                form.add_error("file", str(exc))
            else:
                sync_manual_knowledge_documents_for_tenant(tenant=access.tenant)
                messages.success(
                    request,
                    f"Importação concluída. Criados: {result.created}. Atualizados: {result.updated}. Ignorados: {result.skipped}. Solicite processamento para atualizar o índice RAG.",
                )
                return redirect(f"{request.path}?tenant={access.tenant.pk}")
        messages.error(request, "Revise a importação antes de continuar.")
    else:
        form = KnowledgeImportPortalForm()
    return render(
        request,
        "operations_portal/knowledge_base/import.html",
        _knowledge_base_context(access, sub_section="documents", form=form, result=result),
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
        "Solicitação registrada. Acompanhe o status nesta tela; o processamento será executado pelo worker operacional configurado.",
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


@login_required(login_url="/admin/login/")
def knowledge_base_health(request):
    access = _resolve_knowledge_base_access(request)
    period = request.GET.get("period", "7d")
    dashboard = build_rag_health_dashboard(tenant=access.tenant, period=period)
    operations_page = get_health_operation_page(
        tenant=access.tenant,
        page_number=request.GET.get("ops_page", 1),
    )
    retrieval_page = get_health_retrieval_page(
        tenant=access.tenant,
        period=dashboard["period"],
        page_number=request.GET.get("retrieval_page", 1),
    )
    ai_page = get_health_ai_usage_page(
        tenant=access.tenant,
        period=dashboard["period"],
        page_number=request.GET.get("ai_page", 1),
    )
    open_alerts = count_open_operational_alerts(tenant=access.tenant)
    from knowledge_base.rag.operational_monitoring import build_tenant_monitoring_summary

    monitoring = build_tenant_monitoring_summary(tenant=access.tenant)
    governance = build_tenant_governance_summary(tenant=access.tenant)
    return render(
        request,
        "operations_portal/knowledge_base/health.html",
        _knowledge_base_context(
            access,
            sub_section="health",
            dashboard=dashboard,
            period=dashboard["period"],
            operations_page=operations_page,
            retrieval_page=retrieval_page,
            ai_page=ai_page,
            open_alerts=open_alerts,
            monitoring=monitoring,
            governance=governance,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_health_sync(request):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    period = request.POST.get("period") or request.GET.get("period") or "7d"
    result = process_operational_monitoring(
        tenant_slug=access.tenant.slug,
        period=period,
        trigger=OperationalMonitoringBatchRun.Trigger.PORTAL,
        actor=request.user,
        request=request,
        dry_run=None,
    )
    messages.success(
        request,
        "Monitoramento executado. "
        f"Status: {result.status}. Processados: {result.tenants_processed}. "
        f"Criados: {result.alerts_created}. Atualizados: {result.alerts_updated}. "
        f"Resolvidos: {result.alerts_resolved}. Dry-run: {result.dry_run}.",
    )
    return redirect(f"{reverse('operations_portal:knowledge_base_health')}?tenant={access.tenant.pk}&period={period}")


@login_required(login_url="/admin/login/")
def knowledge_base_alerts(request):
    access = _resolve_knowledge_base_access(request)
    page = get_operational_alert_list(
        tenant=access.tenant,
        status=request.GET.get("status"),
        severity=request.GET.get("severity"),
        category=request.GET.get("category"),
        period=request.GET.get("period", "7d"),
        assigned_to=request.GET.get("assigned_to"),
        unassigned=request.GET.get("unassigned"),
        silenced=request.GET.get("silenced"),
        under_maintenance=request.GET.get("under_maintenance"),
        sla_breached=request.GET.get("sla_breached"),
        page_number=request.GET.get("page", 1),
    )
    return render(
        request,
        "operations_portal/knowledge_base/alerts.html",
        _knowledge_base_context(
            access,
            sub_section="alerts",
            page_obj=page,
            querystring=clean_querystring(request.GET),
            filters={
                "status": request.GET.get("status", ""),
                "severity": request.GET.get("severity", ""),
                "category": request.GET.get("category", ""),
                "period": request.GET.get("period", "7d"),
                "assigned_to": request.GET.get("assigned_to", ""),
                "unassigned": request.GET.get("unassigned", ""),
                "silenced": request.GET.get("silenced", ""),
                "under_maintenance": request.GET.get("under_maintenance", ""),
                "sla_breached": request.GET.get("sla_breached", ""),
            },
            silence_presets=SILENCE_PRESETS_HOURS.keys(),
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_alert_detail(request, pk: int):
    from knowledge_base.models import TenantOperationalAlert
    from operations_portal.work_queue_services import enrich_alert_detail_with_work_queue

    access = _resolve_knowledge_base_access(request)
    alert = (
        TenantOperationalAlert.objects.filter(tenant=access.tenant, pk=pk)
        .select_related("assigned_to__user", "assigned_by", "acknowledged_by", "resolved_by", "tenant")
        .first()
    )
    if alert is None:
        raise PermissionDenied
    detail = get_operational_alert_detail(tenant=access.tenant, alert_id=pk)
    detail = enrich_alert_detail_with_work_queue(detail=detail, alert=alert)
    return render(
        request,
        "operations_portal/knowledge_base/alert_detail.html",
        _knowledge_base_context(access, sub_section="alerts", alert=detail),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_alert_acknowledge(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        acknowledge_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            request=request,
        )
    except OperationalAlertError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Alerta reconhecido.")
    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_alert_resolve(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    note = request.POST.get("resolution_note", "")
    try:
        resolve_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            resolution_note=note,
            request=request,
        )
    except OperationalAlertError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Alerta resolvido manualmente.")
    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_alert_assign(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    membership_id = request.POST.get("membership_id")
    try:
        assign_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            membership_id=int(membership_id) if membership_id else None,
            request=request,
        )
    except GovernanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Responsável atribuído.")
    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_alert_silence(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        silence_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            reason=request.POST.get("reason", ""),
            duration_key=request.POST.get("duration_key"),
            request=request,
        )
    except GovernanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Alerta silenciado temporariamente.")
    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_alert_unsilence(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        cancel_operational_alert_silence(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            request=request,
        )
    except GovernanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Silenciamento cancelado.")
    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={access.tenant.pk}")


@login_required(login_url="/admin/login/")
def knowledge_base_maintenance(request):
    access = _resolve_knowledge_base_access(request)
    page = get_maintenance_window_list(
        tenant=access.tenant,
        status=request.GET.get("status"),
        category=request.GET.get("category"),
        period=request.GET.get("period", "30d"),
        page_number=request.GET.get("page", 1),
    )
    return render(
        request,
        "operations_portal/knowledge_base/maintenance.html",
        _knowledge_base_context(
            access,
            sub_section="maintenance",
            page_obj=page,
            querystring=clean_querystring(request.GET),
            filters={
                "status": request.GET.get("status", ""),
                "category": request.GET.get("category", ""),
                "period": request.GET.get("period", "30d"),
            },
        ),
    )


def _parse_portal_datetime(value: str):
    parsed = parse_datetime(str(value or "").strip())
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@login_required(login_url="/admin/login/")
def knowledge_base_maintenance_create(request):
    from django.urls import reverse

    from knowledge_base.models import TenantOperationalAlert, TenantOperationalMaintenanceWindow

    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.method == "POST":
        posted_tenant = request.POST.get("tenant")
        if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
            raise PermissionDenied
        starts_at = _parse_portal_datetime(request.POST.get("starts_at", ""))
        ends_at = _parse_portal_datetime(request.POST.get("ends_at", ""))
        if starts_at is None or ends_at is None:
            messages.error(request, "Informe início e fim válidos.")
        else:
            scope_categories = [
                item.strip()
                for item in str(request.POST.get("scope_categories", "")).split(",")
                if item.strip()
            ]
            scope_rule_ids = [
                item.strip()
                for item in str(request.POST.get("scope_rule_ids", "")).split(",")
                if item.strip()
            ]
            try:
                create_maintenance_window(
                    tenant=access.tenant,
                    actor=request.user,
                    title=request.POST.get("title", ""),
                    description=request.POST.get("description", ""),
                    starts_at=starts_at,
                    ends_at=ends_at,
                    scope=request.POST.get("scope") or "all",
                    scope_categories=scope_categories,
                    scope_rule_ids=scope_rule_ids,
                    scope_resource_reference=request.POST.get("scope_resource_reference", ""),
                    request=request,
                )
            except GovernanceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Janela de manutenção criada.")
                return redirect(
                    f"{reverse('operations_portal:knowledge_base_maintenance')}?tenant={access.tenant.pk}"
                )
    return render(
        request,
        "operations_portal/knowledge_base/maintenance_form.html",
        _knowledge_base_context(
            access,
            sub_section="maintenance",
            scope_choices=TenantOperationalMaintenanceWindow.Scope.choices,
            category_choices=TenantOperationalAlert.Category.choices,
        ),
    )


@login_required(login_url="/admin/login/")
def knowledge_base_maintenance_cancel(request, pk: int):
    from django.urls import reverse

    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    posted_tenant = request.POST.get("tenant")
    if posted_tenant and str(posted_tenant) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        cancel_maintenance_window(
            tenant=access.tenant,
            window_id=pk,
            actor=request.user,
            cancellation_note=request.POST.get("cancellation_note", ""),
            request=request,
        )
    except GovernanceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Janela de manutenção cancelada.")
    return redirect(f"{reverse('operations_portal:knowledge_base_maintenance')}?tenant={access.tenant.pk}")
