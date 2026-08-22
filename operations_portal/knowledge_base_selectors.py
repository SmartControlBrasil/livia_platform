from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q

from knowledge_base.models import (
    KnowledgeDocument,
    RagRetrievalEvent,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
)

from knowledge_base.services.manual_rag import manual_document_rag_state

from .knowledge_base_services import sanitize_excerpt, serialize_retrieval_event

PAGE_SIZE = 12


def get_tenant_rag_configuration(tenant) -> TenantRagConfiguration | None:
    if tenant is None:
        return None
    return TenantRagConfiguration.objects.filter(tenant=tenant).first()


def get_knowledge_document_list(*, tenant, form, page_number=1):
    queryset = KnowledgeDocument.objects.filter(tenant=tenant).order_by("-updated_at", "title", "id")
    if form.is_valid():
        status = form.cleaned_data.get("status")
        if status:
            queryset = queryset.filter(status=status)
        q = form.cleaned_data.get("q")
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q)
                | Q(slug__icontains=q)
                | Q(content__icontains=q)
                | Q(tags__icontains=q)
                | Q(source_type__icontains=q)
                | Q(source_url__icontains=q)
            )

    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    for item in page.object_list:
        item.tags_label = ", ".join(str(tag) for tag in (item.tags or [])) or "-"
        item.status_label = item.get_status_display()
        item.content_excerpt = sanitize_excerpt(item.content, max_len=140)
        item.rag_state = manual_document_rag_state(document=item)
    return page


def get_knowledge_document_detail(*, tenant, pk: int):
    document = KnowledgeDocument.objects.filter(tenant=tenant, pk=pk).first()
    if document is not None:
        document.rag_state = manual_document_rag_state(document=document)
    return document


def get_knowledge_document_counters(*, tenant) -> dict:
    documents = KnowledgeDocument.objects.filter(tenant=tenant)
    status_counts = {row["status"]: row["total"] for row in documents.values("status").annotate(total=Count("id"))}
    manifests = TenantRagDriveFileManifest.objects.filter(tenant=tenant)
    chunks = TenantRagDocumentChunk.objects.filter(tenant=tenant)
    embeddings = TenantRagChunkEmbedding.objects.filter(tenant=tenant, is_active=True, status=TenantRagChunkEmbedding.Status.ACTIVE)
    return {
        "documents": documents.count(),
        "active_documents": status_counts.get(KnowledgeDocument.Status.ACTIVE, 0),
        "draft_documents": status_counts.get(KnowledgeDocument.Status.DRAFT, 0),
        "archived_documents": status_counts.get(KnowledgeDocument.Status.ARCHIVED, 0),
        "rag_manifests": manifests.count(),
        "rag_active_manifests": manifests.filter(is_active=True).count(),
        "rag_chunks": chunks.count(),
        "rag_active_chunks": chunks.filter(is_active=True, status=TenantRagDocumentChunk.Status.ACTIVE).count(),
        "rag_embeddings": embeddings.count(),
    }


def get_knowledge_chunk_list(*, tenant, form, page_number=1):
    active_embedding = TenantRagChunkEmbedding.objects.filter(
        chunk_id=OuterRef("pk"),
        tenant=tenant,
        is_active=True,
        status=TenantRagChunkEmbedding.Status.ACTIVE,
    )
    queryset = (
        TenantRagDocumentChunk.objects.filter(tenant=tenant)
        .select_related("manifest")
        .annotate(has_embedding=Exists(active_embedding))
        .order_by("manifest__name", "ordinal", "id")
    )
    if form.is_valid():
        status = form.cleaned_data.get("status")
        if status:
            queryset = queryset.filter(status=status)
        active = form.cleaned_data.get("is_active")
        if active == "yes":
            queryset = queryset.filter(is_active=True)
        elif active == "no":
            queryset = queryset.filter(is_active=False)
        embedding = form.cleaned_data.get("has_embedding")
        if embedding == "yes":
            queryset = queryset.filter(has_embedding=True)
        elif embedding == "no":
            queryset = queryset.filter(has_embedding=False)
        manifest_id = form.cleaned_data.get("manifest")
        if manifest_id:
            queryset = queryset.filter(manifest_id=manifest_id.pk)

    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    for item in page.object_list:
        item.document_title = item.manifest.name if item.manifest_id else "-"
        item.excerpt = sanitize_excerpt(item.chunk_text)
        item.embedding_label = "Sim" if item.has_embedding else "Não"
        item.status_label = item.get_status_display()
    return page


def get_knowledge_retrieval_events(*, tenant, page_number=1, page_size=20):
    queryset = RagRetrievalEvent.objects.filter(tenant=tenant).order_by("-created_at", "-id")
    page = Paginator(queryset, page_size).get_page(page_number)
    page.object_list = [serialize_retrieval_event(event) for event in page.object_list]
    return page


def serialize_operation_request(request_obj) -> dict:
    duration_ms = 0
    if request_obj.started_at and request_obj.finished_at:
        duration_ms = int((request_obj.finished_at - request_obj.started_at).total_seconds() * 1000)
    lease_stale = False
    if (
        request_obj.status == request_obj.Status.RUNNING
        and request_obj.lease_expires_at is not None
    ):
        from django.utils import timezone

        lease_stale = request_obj.lease_expires_at < timezone.now()
    return {
        "id": request_obj.pk,
        "operation": request_obj.operation,
        "operation_label": request_obj.get_operation_display(),
        "status": request_obj.status,
        "status_label": request_obj.get_status_display(),
        "dry_run": request_obj.dry_run,
        "run_id": request_obj.run_id,
        "counters": request_obj.counters or {},
        "error_code": request_obj.error_code or "",
        "error_message": request_obj.error_message or "",
        "requested_by": getattr(request_obj.requested_by, "username", "") or "-",
        "started_at": request_obj.started_at,
        "finished_at": request_obj.finished_at,
        "created_at": request_obj.created_at,
        "duration_ms": duration_ms,
        "attempt_count": request_obj.attempt_count,
        "last_heartbeat_at": request_obj.last_heartbeat_at,
        "lease_expires_at": request_obj.lease_expires_at,
        "lease_stale": lease_stale,
    }


def get_operation_request_list(*, tenant, page_number=1):
    from knowledge_base.models import TenantRagOperationRequest

    queryset = (
        TenantRagOperationRequest.objects.filter(tenant=tenant)
        .select_related("requested_by")
        .order_by("-created_at", "-id")
    )
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    page.object_list = [serialize_operation_request(item) for item in page.object_list]
    return page


def get_operation_request_detail(*, tenant, pk: int):
    from knowledge_base.models import TenantRagOperationRequest

    request_obj = (
        TenantRagOperationRequest.objects.select_related("requested_by", "index_run")
        .filter(tenant=tenant, pk=pk)
        .first()
    )
    if request_obj is None:
        return None
    payload = serialize_operation_request(request_obj)
    payload["index_run_id"] = getattr(request_obj.index_run, "pk", None)
    return payload
