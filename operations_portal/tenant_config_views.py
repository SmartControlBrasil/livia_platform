"""Gestão operacional de tenants e RAG em /painel/configuracao/tenants/."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.models import (
    ACTION_RAG_DOCUMENT_DISABLED,
    ACTION_RAG_DOCUMENT_ENABLED,
    ACTION_TENANT_ORIGIN_ADDED,
    ACTION_TENANT_ORIGIN_REMOVED,
    ACTION_TENANT_SETTING_CHANGED,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from knowledge_base.models import (
    KnowledgeDocument,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
)
from knowledge_base.rag.content_classification import classify_rag_source
from operations_portal.access import portal_template_context, require_portal_capability, resolve_portal_access
from operations_portal.forms import (
    TenantOperationalSettingsForm,
    TenantOriginAddForm,
    TenantRagSafeSettingsForm,
)
from operations_portal.operational_readiness import TenantOperationalReadinessService
from operations_portal.selectors import clean_querystring
from tenants.access import CAPABILITY_TENANT_MANAGE, CAPABILITY_TENANT_VIEW
from tenants.models import AssistantProfile, Tenant, TenantAllowedOrigin
from tenants.services.human_handoff import build_human_handoff_payload


PROFILE_SAFE_FIELDS = [
    "name",
    "notification_email",
    "human_handoff_enabled",
    "is_widget_enabled",
    "tone",
    "primary_goal",
    "business_name",
    "business_domain",
    "short_description",
]
RAG_SAFE_FIELDS = ["retrieval_enabled", "sync_enabled", "min_similarity_score", "max_retrieved_chunks"]


def _tenant_qs(access):
    qs = Tenant.objects.select_related("assistant_profile").prefetch_related(
        Prefetch("allowed_origins", queryset=TenantAllowedOrigin.objects.filter(is_active=True).order_by("origin")),
    ).order_by("name")
    if access.is_global:
        return qs
    if access.tenant is None:
        return qs.none()
    return qs.filter(pk=access.tenant.pk)


def _get_tenant(access, slug: str) -> Tenant:
    return get_object_or_404(_tenant_qs(access), slug=slug)


def _mask_folder_id(folder_id: str) -> str:
    value = str(folder_id or "").strip()
    if len(value) <= 8:
        return "***" if value else "—"
    return f"{value[:4]}…{value[-4:]}"


def _document_classification(doc: KnowledgeDocument) -> dict:
    result = classify_rag_source(
        source_name=doc.title,
        source_reference=doc.source_url or doc.source_type or "",
        text=doc.content or "",
    )
    return {
        "content_type": result.content_type,
        "visibility": result.visibility,
        "domain": result.domain,
        "is_internal": result.visibility == "internal",
    }


def _rag_stats(tenant: Tenant) -> dict:
    docs = KnowledgeDocument.objects.filter(tenant=tenant)
    chunks = TenantRagDocumentChunk.objects.filter(tenant=tenant, is_active=True)
    embeddings = TenantRagChunkEmbedding.objects.filter(tenant=tenant)
    failed = embeddings.filter(status=TenantRagChunkEmbedding.Status.FAILED).count()
    ready = embeddings.filter(status=TenantRagChunkEmbedding.Status.ACTIVE, is_active=True).count()
    return {
        "documents": docs.count(),
        "active_documents": docs.filter(status=KnowledgeDocument.Status.ACTIVE).count(),
        "chunks": chunks.count(),
        "embeddings": ready,
        "embeddings_pending": max(chunks.count() - ready - failed, 0),
        "embeddings_failed": failed,
    }


@login_required(login_url="/admin/login/")
def tenant_config_list(request):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_VIEW, allow_global=True)
    qs = _tenant_qs(access).annotate(
        document_count=Count("knowledge_documents", distinct=True),
        chunk_count=Count("rag_document_chunks", distinct=True),
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page") or 1)
    rows = []
    readiness_service = TenantOperationalReadinessService()
    for tenant in page.object_list:
        profile = getattr(tenant, "assistant_profile", None)
        rag = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        operational = readiness_service.for_tenant(tenant)
        rows.append(
            {
                "tenant": tenant,
                "assistant_name": getattr(profile, "name", "") or "—",
                "notification_email": getattr(profile, "notification_email", "") or "—",
                "origins": [o.origin for o in tenant.allowed_origins.all()[:5]],
                "rag_enabled": bool(getattr(rag, "retrieval_enabled", False)),
                "source_mode": getattr(rag, "source_mode", "—"),
                "sync_enabled": bool(getattr(rag, "sync_enabled", False)),
                "handoff_enabled": bool(getattr(profile, "human_handoff_enabled", False)),
                "readiness": getattr(operational, "status", "—"),
                "last_sync": getattr(rag, "last_inventory_at", None) or getattr(rag, "last_index_at", None),
                "documents": tenant.document_count,
                "chunks": tenant.chunk_count,
            }
        )
    context = {
        "active_section": "configuracao_tenants",
        "page_obj": page,
        "rows": rows,
        "querystring": clean_querystring(request.GET),
        "q": q,
        "can_manage": CAPABILITY_TENANT_MANAGE in access.capabilities or access.is_global,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenant_config/list.html", context)


@login_required(login_url="/admin/login/")
def tenant_config_detail(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_VIEW, allow_global=True)
    tenant = _get_tenant(access, slug)
    profile, _ = AssistantProfile.objects.get_or_create(tenant=tenant)
    rag, _ = TenantRagConfiguration.objects.get_or_create(tenant=tenant)
    can_manage = CAPABILITY_TENANT_MANAGE in access.capabilities or access.is_global
    operational = TenantOperationalReadinessService().for_tenant(tenant)
    profile_form = TenantOperationalSettingsForm(instance=profile)
    rag_form = TenantRagSafeSettingsForm(instance=rag)
    origin_form = TenantOriginAddForm()
    if not can_manage:
        for form in (profile_form, rag_form, origin_form):
            for field in form.fields.values():
                field.disabled = True
    handoff_preview = build_human_handoff_payload(profile, handoff=None)
    context = {
        "active_section": "configuracao_tenants",
        "tenant": tenant,
        "profile": profile,
        "rag": rag,
        "masked_folder_id": _mask_folder_id(rag.approved_folder_id),
        "profile_form": profile_form,
        "rag_form": rag_form,
        "origin_form": origin_form,
        "origins": list(tenant.allowed_origins.filter(is_active=True).order_by("origin")),
        "can_manage": can_manage,
        "operational": operational,
        "rag_stats": _rag_stats(tenant),
        "handoff_preview": handoff_preview,
        "sync_status": {
            "inventory": rag.last_inventory_status,
            "index": rag.last_index_status,
            "last_started_at": rag.last_inventory_started_at or rag.last_index_started_at,
            "last_finished_at": rag.last_inventory_at or rag.last_index_at,
            "files_seen": rag.last_inventory_file_count,
            "error": rag.last_inventory_error or rag.last_index_error,
        },
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenant_config/detail.html", context)


@login_required(login_url="/admin/login/")
@require_POST
def tenant_config_save_profile(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_MANAGE, allow_global=True)
    require_portal_capability(access, CAPABILITY_TENANT_MANAGE)
    tenant = _get_tenant(access, slug)
    profile, _ = AssistantProfile.objects.get_or_create(tenant=tenant)
    form = TenantOperationalSettingsForm(request.POST, instance=profile)
    if not form.is_valid():
        messages.error(request, "Revise os campos do perfil antes de salvar.")
        return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)
    with transaction.atomic():
        before = audit_model_snapshot(AssistantProfile.objects.get(pk=profile.pk), fields=PROFILE_SAFE_FIELDS)
        saved = form.save()
        changes = changed_fields(before, audit_model_snapshot(saved, fields=PROFILE_SAFE_FIELDS))
        if changes["before"] or changes["after"]:
            record_audit_event(
                action=ACTION_TENANT_SETTING_CHANGED,
                actor=request.user,
                tenant=tenant,
                obj=saved,
                before_data=changes["before"],
                after_data=changes["after"],
                metadata={"source": "operations_portal.tenant_config.profile", "section": "chat_comercial"},
                request=request,
            )
    messages.success(request, "Configuração operacional salva.")
    return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)


@login_required(login_url="/admin/login/")
@require_POST
def tenant_config_save_rag(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_MANAGE, allow_global=True)
    require_portal_capability(access, CAPABILITY_TENANT_MANAGE)
    tenant = _get_tenant(access, slug)
    rag, _ = TenantRagConfiguration.objects.get_or_create(tenant=tenant)
    form = TenantRagSafeSettingsForm(request.POST, instance=rag)
    if not form.is_valid():
        messages.error(request, "Revise as configurações RAG antes de salvar.")
        return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)
    with transaction.atomic():
        before = audit_model_snapshot(TenantRagConfiguration.objects.get(pk=rag.pk), fields=RAG_SAFE_FIELDS)
        saved = form.save()
        changes = changed_fields(before, audit_model_snapshot(saved, fields=RAG_SAFE_FIELDS))
        if changes["before"] or changes["after"]:
            record_audit_event(
                action=ACTION_TENANT_SETTING_CHANGED,
                actor=request.user,
                tenant=tenant,
                obj=saved,
                before_data=changes["before"],
                after_data=changes["after"],
                metadata={"source": "operations_portal.tenant_config.rag", "section": "rag"},
                request=request,
            )
    messages.success(request, "Configuração RAG salva.")
    return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)


@login_required(login_url="/admin/login/")
@require_POST
def tenant_config_origin_add(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_MANAGE, allow_global=True)
    require_portal_capability(access, CAPABILITY_TENANT_MANAGE)
    tenant = _get_tenant(access, slug)
    form = TenantOriginAddForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Origin inválida.")
        return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)
    origin = form.cleaned_data["origin"]
    with transaction.atomic():
        obj, created = TenantAllowedOrigin.objects.get_or_create(
            tenant=tenant,
            origin=origin,
            defaults={"is_active": True, "created_by": request.user},
        )
        reactivated = False
        if not created and not obj.is_active:
            obj.is_active = True
            obj.save(update_fields=["is_active", "updated_at"])
            reactivated = True
        if created or reactivated:
            record_audit_event(
                action=ACTION_TENANT_ORIGIN_ADDED,
                actor=request.user,
                tenant=tenant,
                obj=obj,
                after_data={"origin": origin, "is_active": True},
                metadata={"source": "operations_portal.tenant_config.origins"},
                request=request,
            )
            messages.success(request, "Origin adicionada.")
        else:
            messages.info(request, "Origin já existia.")
    return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)


@login_required(login_url="/admin/login/")
@require_POST
def tenant_config_origin_remove(request, slug, origin_id):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_MANAGE, allow_global=True)
    require_portal_capability(access, CAPABILITY_TENANT_MANAGE)
    tenant = _get_tenant(access, slug)
    origin = get_object_or_404(TenantAllowedOrigin.objects.filter(tenant=tenant), pk=origin_id)
    with transaction.atomic():
        before = {"origin": origin.origin, "is_active": origin.is_active}
        origin.is_active = False
        origin.save(update_fields=["is_active", "updated_at"])
        record_audit_event(
            action=ACTION_TENANT_ORIGIN_REMOVED,
            actor=request.user,
            tenant=tenant,
            obj=origin,
            before_data=before,
            after_data={"origin": origin.origin, "is_active": False},
            metadata={"source": "operations_portal.tenant_config.origins"},
            request=request,
        )
    messages.success(request, "Origin desativada.")
    return redirect("operations_portal:tenant_config_detail", slug=tenant.slug)


@login_required(login_url="/admin/login/")
def tenant_config_rag(request, slug):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_VIEW, allow_global=True)
    tenant = _get_tenant(access, slug)
    rag = TenantRagConfiguration.objects.filter(tenant=tenant).first()
    docs = KnowledgeDocument.objects.filter(tenant=tenant).order_by("-updated_at")
    visibility = (request.GET.get("visibility") or "").strip()
    page = Paginator(docs, 25).get_page(request.GET.get("page") or 1)
    rows = []
    for doc in page.object_list:
        classification = _document_classification(doc)
        if visibility == "public" and classification["is_internal"]:
            continue
        if visibility == "internal" and not classification["is_internal"]:
            continue
        rows.append(
            {
                "doc": doc,
                "classification": classification,
                "chunks": "—",
                "embeddings": doc.lifecycle_status,
            }
        )
    context = {
        "active_section": "configuracao_tenants",
        "tenant": tenant,
        "rag": rag,
        "masked_folder_id": _mask_folder_id(getattr(rag, "approved_folder_id", "")),
        "page_obj": page,
        "rows": rows,
        "rag_stats": _rag_stats(tenant),
        "visibility": visibility,
        "can_manage": CAPABILITY_TENANT_MANAGE in access.capabilities or access.is_global,
        "querystring": clean_querystring(request.GET),
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenant_config/rag.html", context)


@login_required(login_url="/admin/login/")
def tenant_config_document_detail(request, slug, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_VIEW, allow_global=True)
    tenant = _get_tenant(access, slug)
    doc = get_object_or_404(KnowledgeDocument.objects.filter(tenant=tenant), pk=pk)
    classification = _document_classification(doc)
    # Chunks Drive/RAG não estão ligados por FK direta a KnowledgeDocument.
    # Mostra metadados de lifecycle e preview; nunca vetores.
    embedding_meta = []
    preview = (doc.content or "")[:1200]
    chunks = []
    context = {
        "active_section": "configuracao_tenants",
        "tenant": tenant,
        "doc": doc,
        "classification": classification,
        "preview": preview,
        "chunks": chunks,
        "embedding_meta": embedding_meta,
        "can_manage": CAPABILITY_TENANT_MANAGE in access.capabilities or access.is_global,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/tenant_config/document_detail.html", context)


@login_required(login_url="/admin/login/")
@require_POST
def tenant_config_document_toggle(request, slug, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_TENANT_MANAGE, allow_global=True)
    require_portal_capability(access, CAPABILITY_TENANT_MANAGE)
    tenant = _get_tenant(access, slug)
    doc = get_object_or_404(KnowledgeDocument.objects.filter(tenant=tenant), pk=pk)
    with transaction.atomic():
        before = {"status": doc.status}
        if doc.status == KnowledgeDocument.Status.ACTIVE:
            doc.status = KnowledgeDocument.Status.ARCHIVED
            action = ACTION_RAG_DOCUMENT_DISABLED
            msg = "Documento desativado (não deletado)."
        else:
            doc.status = KnowledgeDocument.Status.ACTIVE
            action = ACTION_RAG_DOCUMENT_ENABLED
            msg = "Documento ativado."
        doc.save(update_fields=["status", "updated_at"])
        record_audit_event(
            action=action,
            actor=request.user,
            tenant=tenant,
            obj=doc,
            before_data=before,
            after_data={"status": doc.status},
            metadata={"source": "operations_portal.tenant_config.document_toggle"},
            request=request,
        )
    messages.success(request, msg)
    return redirect("operations_portal:tenant_config_document_detail", slug=tenant.slug, pk=doc.pk)
