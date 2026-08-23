from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from knowledge_base.models import (
    KnowledgeDocument,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
)
from knowledge_base.rag.google_drive_inventory import compute_text_sha256, normalize_text_for_rag

MANUAL_SOURCE_PREFIX = "manual-knowledge-document"
MANUAL_MIME_TYPE = "text/markdown"


@dataclass(frozen=True)
class ManualRagSyncResult:
    synced: int = 0
    deactivated: int = 0
    missing_configuration: bool = False


def ensure_manual_rag_configuration(*, tenant, retrieval_enabled: bool = True) -> TenantRagConfiguration:
    configuration, created = TenantRagConfiguration.objects.get_or_create(
        tenant=tenant,
        defaults={
            "source_mode": TenantRagConfiguration.SOURCE_MANUAL,
            "approved_folder_id": "",
            "sync_enabled": False,
            "retrieval_enabled": bool(retrieval_enabled),
        },
    )
    if created:
        return configuration
    changed_fields = []
    if configuration.source_mode == TenantRagConfiguration.SOURCE_MANUAL:
        if configuration.approved_folder_id:
            configuration.approved_folder_id = ""
            changed_fields.append("approved_folder_id")
        if configuration.sync_enabled:
            configuration.sync_enabled = False
            changed_fields.append("sync_enabled")
    if changed_fields:
        configuration.save(update_fields=[*changed_fields, "updated_at"])
    return configuration


def manual_drive_file_id(document: KnowledgeDocument) -> str:
    return f"{MANUAL_SOURCE_PREFIX}-{document.pk}"


def manual_relative_path(document: KnowledgeDocument) -> str:
    slug = str(document.slug or document.pk).strip() or str(document.pk)
    return f"manual/{slug}.md"


def build_manual_document_text(document: KnowledgeDocument) -> str:
    parts = [f"# {document.title}".strip()]
    if document.source_url:
        parts.append(f"Fonte: {document.source_url}")
    if document.tags:
        parts.append("Tags: " + ", ".join(str(tag) for tag in document.tags if str(tag).strip()))
    parts.append("")
    parts.append(str(document.content or ""))
    return "\n".join(part for part in parts if part is not None).strip()


def sync_manual_knowledge_document_to_rag(*, document: KnowledgeDocument) -> ManualRagSyncResult:
    configuration = ensure_manual_rag_configuration(tenant=document.tenant)

    if document.status != KnowledgeDocument.Status.ACTIVE:
        deactivated = deactivate_manual_document_from_rag(document=document, configuration=configuration)
        return ManualRagSyncResult(deactivated=deactivated)

    raw_text = build_manual_document_text(document)
    normalized = normalize_text_for_rag(raw_text)
    text_hash = compute_text_sha256(normalized)
    now = timezone.now()
    drive_file_id = manual_drive_file_id(document)

    with transaction.atomic():
        manifest, created = TenantRagDriveFileManifest.objects.select_for_update().get_or_create(
            tenant=document.tenant,
            drive_file_id=drive_file_id,
            defaults={
                "configuration": configuration,
                "name": document.title,
                "mime_type": MANUAL_MIME_TYPE,
                "relative_path": manual_relative_path(document),
                "drive_modified_time": document.updated_at,
                "drive_size_bytes": len(normalized.encode("utf-8")),
                "normalized_text_sha256": "",
                "status": TenantRagDriveFileManifest.Status.DISCOVERED,
                "is_active": True,
                "last_seen_at": now,
            },
        )
        previous_hash = manifest.normalized_text_sha256 or ""
        manifest.configuration = configuration
        manifest.name = document.title
        manifest.mime_type = MANUAL_MIME_TYPE
        manifest.relative_path = manual_relative_path(document)
        manifest.drive_modified_time = document.updated_at
        manifest.drive_size_bytes = len(normalized.encode("utf-8"))
        manifest.is_active = True
        manifest.removed_at = None
        manifest.last_seen_at = now
        manifest.last_error = ""

        staging, _ = TenantRagDriveTextStaging.objects.select_for_update().get_or_create(
            manifest=manifest,
            defaults={
                "tenant": document.tenant,
                "normalized_text": "",
                "normalized_text_sha256": text_hash,
                "normalized_text_char_count": 0,
                "normalized_text_byte_count": 0,
                "exported_at": now,
            },
        )
        staging.tenant = document.tenant
        staging.normalized_text = normalized
        staging.normalized_text_sha256 = text_hash
        staging.normalized_text_char_count = len(normalized)
        staging.normalized_text_byte_count = len(normalized.encode("utf-8"))
        staging.exported_at = now
        staging.save()

        manifest.normalized_text_sha256 = text_hash
        if created or not previous_hash:
            manifest.status = TenantRagDriveFileManifest.Status.EXPORTED
        elif previous_hash == text_hash:
            manifest.status = TenantRagDriveFileManifest.Status.UNCHANGED
        else:
            manifest.status = TenantRagDriveFileManifest.Status.UPDATED
        manifest.last_exported_at = now
        manifest.save()

    return ManualRagSyncResult(synced=1)


def deactivate_manual_document_from_rag(*, document: KnowledgeDocument, configuration: TenantRagConfiguration | None = None) -> int:
    configuration = configuration or TenantRagConfiguration.objects.filter(tenant=document.tenant).first()
    if configuration is None:
        return 0
    now = timezone.now()
    with transaction.atomic():
        manifest = (
            TenantRagDriveFileManifest.objects.select_for_update()
            .filter(tenant=document.tenant, configuration=configuration, drive_file_id=manual_drive_file_id(document))
            .first()
        )
        if manifest is None:
            return 0
        manifest.is_active = False
        manifest.status = TenantRagDriveFileManifest.Status.REMOVED
        manifest.removed_at = now
        manifest.last_error = ""
        manifest.save(update_fields=["is_active", "status", "removed_at", "last_error", "updated_at"])
        chunk_ids = list(
            TenantRagDocumentChunk.objects.filter(
                tenant=document.tenant,
                manifest=manifest,
                is_active=True,
            ).values_list("pk", flat=True)
        )
        deactivated = 0
        if chunk_ids:
            deactivated = TenantRagDocumentChunk.objects.filter(pk__in=chunk_ids, tenant=document.tenant).update(
                is_active=False,
                status=TenantRagDocumentChunk.Status.REPLACED,
                updated_at=now,
            )
            TenantRagChunkEmbedding.objects.filter(
                tenant=document.tenant,
                chunk_id__in=chunk_ids,
                is_active=True,
            ).update(
                is_active=False,
                status=TenantRagChunkEmbedding.Status.REPLACED,
                updated_at=now,
            )
    return deactivated


def sync_manual_knowledge_documents_for_tenant(*, tenant) -> ManualRagSyncResult:
    configuration = ensure_manual_rag_configuration(tenant=tenant)
    synced = 0
    deactivated = 0
    documents = KnowledgeDocument.objects.filter(tenant=tenant).order_by("id")
    for document in documents:
        result = sync_manual_knowledge_document_to_rag(document=document)
        synced += result.synced
        deactivated += result.deactivated
    return ManualRagSyncResult(synced=synced, deactivated=deactivated)


def manual_document_rag_state(*, document: KnowledgeDocument) -> dict:
    manifest = (
        TenantRagDriveFileManifest.objects.filter(tenant=document.tenant, drive_file_id=manual_drive_file_id(document))
        .order_by("-updated_at")
        .first()
    )
    if manifest is None:
        return {"state": "AGUARDANDO", "manifest": None, "chunks": 0, "embeddings": 0, "stale": True}
    active_chunks = TenantRagDocumentChunk.objects.filter(
        tenant=document.tenant,
        manifest=manifest,
        is_active=True,
        status=TenantRagDocumentChunk.Status.ACTIVE,
    )
    chunks_count = active_chunks.count()
    embeddings_count = TenantRagChunkEmbedding.objects.filter(
        tenant=document.tenant,
        manifest=manifest,
        is_active=True,
        status=TenantRagChunkEmbedding.Status.ACTIVE,
    ).count()
    if document.status != KnowledgeDocument.Status.ACTIVE or not manifest.is_active:
        state = "BLOCKED"
    elif manifest.status == TenantRagDriveFileManifest.Status.FAILED:
        state = "FAILED"
    elif manifest.status == TenantRagDriveFileManifest.Status.UPDATED or chunks_count == 0:
        state = "STALE"
    elif embeddings_count < chunks_count:
        state = "AGUARDANDO"
    else:
        state = "READY"
    return {
        "state": state,
        "manifest": manifest,
        "chunks": chunks_count,
        "embeddings": embeddings_count,
        "stale": state in {"AGUARDANDO", "STALE"},
    }
