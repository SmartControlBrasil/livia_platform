from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from audit.services import record_audit_event
from knowledge_base.models import (
    KnowledgeDocument,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
)
from knowledge_base.rag.google_drive_inventory import compute_text_sha256, normalize_text_for_rag
from knowledge_base.rag.indexing import TenantRagIndexingError, run_index_for_tenant
from knowledge_base.rag.sync import TenantRagSyncError, run_chunk_build_for_tenant
from knowledge_base.services.manual_rag import (
    build_manual_document_text,
    deactivate_manual_document_from_rag,
    manual_document_rag_state,
    manual_drive_file_id,
    ensure_manual_rag_configuration,
    sync_manual_knowledge_document_to_rag,
)

logger = logging.getLogger(__name__)

IMPORT_CREATED = "CREATED"
IMPORT_UPDATED = "UPDATED"
IMPORT_UNCHANGED = "UNCHANGED"
DOCUMENT_DISABLED = "DISABLED"
DOCUMENT_ENABLED = "ENABLED"
REINDEX_REQUIRED = "REINDEX_REQUIRED"
INDEX_UNCHANGED = "UNCHANGED"
INDEX_COMPLETED = "INDEXED"
INDEX_FAILED = "FAILED"

READINESS_EMPTY = "EMPTY"
READINESS_AVAILABLE = "AVAILABLE"
READINESS_INDEXING = "INDEXING"
READINESS_READY = "READY"
READINESS_STALE = "STALE"
READINESS_DEGRADED = "DEGRADED"

ACTION_KNOWLEDGE_DOCUMENT_UNCHANGED = "knowledge_document.unchanged"
ACTION_KNOWLEDGE_DOCUMENT_DISABLED = "knowledge_document.disabled"
ACTION_KNOWLEDGE_DOCUMENT_ENABLED = "knowledge_document.enabled"
ACTION_KNOWLEDGE_INDEX_STARTED = "knowledge_index.started"
ACTION_KNOWLEDGE_INDEX_COMPLETED = "knowledge_index.completed"
ACTION_KNOWLEDGE_INDEX_FAILED = "knowledge_index.failed"

SAFE_AUDIT_FIELDS = [
    "tenant",
    "title",
    "slug",
    "source_type",
    "source_url",
    "tags",
    "status",
    "content_sha256",
    "indexed_content_sha256",
    "lifecycle_status",
    "last_indexed_at",
    "last_index_error",
]


@dataclass(frozen=True)
class KnowledgeDocumentUpsertResult:
    tenant: object
    document: KnowledgeDocument
    status: str
    index_status: str
    dry_run: bool = False
    created: bool = False
    changed: bool = False
    content_sha256: str = ""
    previous_content_sha256: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeReindexResult:
    tenant: object
    document: KnowledgeDocument | None
    status: str
    dry_run: bool
    chunks_created: int = 0
    chunks_rebuilt: int = 0
    chunks_unchanged: int = 0
    embeddings_indexed: int = 0
    embeddings_reindexed: int = 0
    embeddings_unchanged: int = 0
    failed: int = 0
    error: str = ""


@dataclass(frozen=True)
class KnowledgeDocumentState:
    document: KnowledgeDocument
    lifecycle_status: str
    usable_for_retrieval: bool
    content_sha256: str
    indexed_content_sha256: str
    manifest_status: str = ""
    manifest_active: bool = False
    active_chunks: int = 0
    active_embeddings: int = 0
    last_indexed_at: object | None = None
    last_error: str = ""


@dataclass(frozen=True)
class KnowledgeReadiness:
    tenant: object
    status: str
    documents_total: int = 0
    documents_enabled: int = 0
    documents_indexed: int = 0
    documents_stale: int = 0
    documents_failed: int = 0
    documents_disabled: int = 0
    documents_indexing: int = 0
    chunks_active: int = 0
    embeddings_active: int = 0
    detail: str = ""

    def as_dict(self):
        return {
            "status": self.status,
            "documents_total": self.documents_total,
            "documents_enabled": self.documents_enabled,
            "documents_indexed": self.documents_indexed,
            "documents_stale": self.documents_stale,
            "documents_failed": self.documents_failed,
            "documents_disabled": self.documents_disabled,
            "documents_indexing": self.documents_indexing,
            "chunks_active": self.chunks_active,
            "embeddings_active": self.embeddings_active,
            "detail": self.detail,
        }


def compute_document_fingerprint(*, tenant, title: str, content: str, source_url: str = "", tags=None) -> str:
    normalized = normalize_text_for_rag(
        "\n".join(
            [
                str(getattr(tenant, "slug", "") or ""),
                str(title or "").strip(),
                str(source_url or "").strip(),
                ",".join(str(tag).strip() for tag in (tags or []) if str(tag).strip()),
                str(content or ""),
            ]
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def document_rag_text_fingerprint(document: KnowledgeDocument) -> str:
    return compute_text_sha256(normalize_text_for_rag(build_manual_document_text(document)))


def build_document_rag_fingerprint(*, tenant, title: str, slug: str, content: str, source_url: str = "", tags=None) -> str:
    document = KnowledgeDocument(
        tenant=tenant,
        title=str(title or "").strip(),
        slug=str(slug or "").strip() or "document",
        content=str(content or ""),
        source_url=str(source_url or "").strip(),
        tags=list(tags or []),
    )
    return document_rag_text_fingerprint(document)


def _safe_error(error: Exception | str) -> str:
    return " ".join(str(error or "operation_failed").split())[:500]


class KnowledgeLifecycleService:
    def upsert_document(
        self,
        *,
        tenant,
        title: str,
        slug: str,
        content: str,
        source_type: str = "manual",
        source_url: str = "",
        tags=None,
        status: str = KnowledgeDocument.Status.ACTIVE,
        replace: bool = True,
        dry_run: bool = False,
        actor=None,
        request=None,
        source: str = "knowledge_lifecycle",
    ) -> KnowledgeDocumentUpsertResult:
        cleaned_title = str(title or "").strip()
        cleaned_slug = str(slug or "").strip() or slugify(cleaned_title)[:110].strip("-")
        cleaned_content = str(content or "").strip()
        cleaned_source_type = str(source_type or "manual").strip() or "manual"
        cleaned_source_url = str(source_url or "").strip()
        cleaned_tags = self._normalize_tags(tags)
        if not cleaned_title:
            raise ValueError("Document title is required.")
        if not cleaned_slug:
            raise ValueError("Document slug is required.")
        if not cleaned_content:
            raise ValueError("Document content is required.")
        content_sha = build_document_rag_fingerprint(
            tenant=tenant,
            title=cleaned_title,
            slug=cleaned_slug,
            content=cleaned_content,
            source_url=cleaned_source_url,
            tags=cleaned_tags,
        )

        existing = KnowledgeDocument.objects.filter(tenant=tenant, slug=cleaned_slug).first()
        if existing is not None and not replace:
            return KnowledgeDocumentUpsertResult(
                tenant=tenant,
                document=existing,
                status=IMPORT_UNCHANGED,
                index_status=INDEX_UNCHANGED,
                dry_run=dry_run,
                content_sha256=existing.content_sha256 or "",
                previous_content_sha256=existing.content_sha256 or "",
            )

        created = existing is None
        previous_sha = getattr(existing, "content_sha256", "") or ""
        unchanged = (
            existing is not None
            and existing.title == cleaned_title
            and existing.content == cleaned_content
            and existing.source_type == cleaned_source_type
            and existing.source_url == cleaned_source_url
            and (existing.tags or []) == cleaned_tags
            and existing.status == status
            and previous_sha == content_sha
        )
        if dry_run:
            fake = existing or KnowledgeDocument(tenant=tenant, slug=cleaned_slug)
            fake.title = cleaned_title
            fake.content = cleaned_content
            fake.source_type = cleaned_source_type
            fake.source_url = cleaned_source_url
            fake.tags = cleaned_tags
            fake.status = status
            fake.content_sha256 = content_sha
            fake.lifecycle_status = self._lifecycle_for_import(status=status, changed=not unchanged)
            return KnowledgeDocumentUpsertResult(
                tenant=tenant,
                document=fake,
                status=IMPORT_CREATED if created else (IMPORT_UNCHANGED if unchanged else IMPORT_UPDATED),
                index_status=INDEX_UNCHANGED if unchanged else REINDEX_REQUIRED,
                dry_run=True,
                created=created,
                changed=not unchanged,
                content_sha256=content_sha,
                previous_content_sha256=previous_sha,
            )

        with transaction.atomic():
            document = (
                KnowledgeDocument.objects.select_for_update().filter(tenant=tenant, slug=cleaned_slug).first()
            )
            created = document is None
            if document is None:
                document = KnowledgeDocument(tenant=tenant, slug=cleaned_slug)
            previous_sha = document.content_sha256 or ""
            unchanged = (
                not created
                and document.title == cleaned_title
                and document.content == cleaned_content
                and document.source_type == cleaned_source_type
                and document.source_url == cleaned_source_url
                and (document.tags or []) == cleaned_tags
                and document.status == status
                and previous_sha == content_sha
            )
            if unchanged:
                self._record_document_event(
                    action=ACTION_KNOWLEDGE_DOCUMENT_UNCHANGED,
                    document=document,
                    actor=actor,
                    request=request,
                    source=source,
                    status=IMPORT_UNCHANGED,
                )
                return KnowledgeDocumentUpsertResult(
                    tenant=tenant,
                    document=document,
                    status=IMPORT_UNCHANGED,
                    index_status=INDEX_UNCHANGED,
                    dry_run=False,
                    content_sha256=content_sha,
                    previous_content_sha256=previous_sha,
                )
            document.title = cleaned_title
            document.content = cleaned_content
            document.source_type = cleaned_source_type
            document.source_url = cleaned_source_url
            document.tags = cleaned_tags
            document.status = status
            document.content_sha256 = content_sha
            document.last_index_error = ""
            document.lifecycle_status = self._lifecycle_for_import(status=status, changed=True)
            if document.indexed_content_sha256 and document.indexed_content_sha256 != content_sha:
                document.lifecycle_status = KnowledgeDocument.LifecycleStatus.STALE
            document.full_clean()
            document.save()

        sync_result = sync_manual_knowledge_document_to_rag(document=document)
        document.refresh_from_db()
        if sync_result.missing_configuration and document.status == KnowledgeDocument.Status.ACTIVE:
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.IMPORTED
            document.save(update_fields=["lifecycle_status", "updated_at"])
        elif document.status != KnowledgeDocument.Status.ACTIVE:
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.DISABLED
            document.save(update_fields=["lifecycle_status", "updated_at"])
        else:
            rag_sha = document_rag_text_fingerprint(document)
            if document.content_sha256 != rag_sha:
                document.content_sha256 = rag_sha
            if document.indexed_content_sha256 == rag_sha and self.document_state(document=document).usable_for_retrieval:
                document.lifecycle_status = KnowledgeDocument.LifecycleStatus.INDEXED
            else:
                document.lifecycle_status = KnowledgeDocument.LifecycleStatus.STALE
            document.save(update_fields=["content_sha256", "lifecycle_status", "updated_at"])

        self._record_document_event(
            action="knowledge_document.created" if created else "knowledge_document.updated",
            document=document,
            actor=actor,
            request=request,
            source=source,
            status=IMPORT_CREATED if created else IMPORT_UPDATED,
        )
        return KnowledgeDocumentUpsertResult(
            tenant=tenant,
            document=document,
            status=IMPORT_CREATED if created else IMPORT_UPDATED,
            index_status=REINDEX_REQUIRED,
            dry_run=False,
            created=created,
            changed=True,
            content_sha256=document.content_sha256,
            previous_content_sha256=previous_sha,
        )

    def disable_document(self, *, tenant, document_id: int, actor=None, request=None, source="knowledge_lifecycle"):
        with transaction.atomic():
            document = KnowledgeDocument.objects.select_for_update().get(tenant=tenant, pk=document_id)
            document.status = KnowledgeDocument.Status.ARCHIVED
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.DISABLED
            document.last_index_error = ""
            document.save(update_fields=["status", "lifecycle_status", "last_index_error", "updated_at"])
        deactivate_manual_document_from_rag(document=document)
        self._record_document_event(
            action=ACTION_KNOWLEDGE_DOCUMENT_DISABLED,
            document=document,
            actor=actor,
            request=request,
            source=source,
            status=DOCUMENT_DISABLED,
        )
        return document

    def enable_document(self, *, tenant, document_id: int, actor=None, request=None, source="knowledge_lifecycle"):
        with transaction.atomic():
            document = KnowledgeDocument.objects.select_for_update().get(tenant=tenant, pk=document_id)
            document.status = KnowledgeDocument.Status.ACTIVE
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.STALE
            document.last_index_error = ""
            document.save(update_fields=["status", "lifecycle_status", "last_index_error", "updated_at"])
        sync_manual_knowledge_document_to_rag(document=document)
        self._record_document_event(
            action=ACTION_KNOWLEDGE_DOCUMENT_ENABLED,
            document=document,
            actor=actor,
            request=request,
            source=source,
            status=DOCUMENT_ENABLED,
        )
        return document

    def reindex_document(
        self,
        *,
        tenant,
        document_id: int,
        dry_run: bool = False,
        provider=None,
        config=None,
        actor=None,
        request=None,
        source="knowledge_lifecycle",
    ) -> KnowledgeReindexResult:
        document = KnowledgeDocument.objects.filter(tenant=tenant, pk=document_id).first()
        if document is None:
            raise KnowledgeDocument.DoesNotExist("Knowledge document not found for tenant.")
        if document.status != KnowledgeDocument.Status.ACTIVE:
            return KnowledgeReindexResult(tenant=tenant, document=document, status=DOCUMENT_DISABLED, dry_run=dry_run)
        configuration = ensure_manual_rag_configuration(tenant=tenant)
        drive_file_id = manual_drive_file_id(document)
        if dry_run:
            state = self.document_state(document=document)
            status = INDEX_UNCHANGED if state.usable_for_retrieval else REINDEX_REQUIRED
            return KnowledgeReindexResult(tenant=tenant, document=document, status=status, dry_run=True)

        self._record_document_event(
            action=ACTION_KNOWLEDGE_INDEX_STARTED,
            document=document,
            actor=actor,
            request=request,
            source=source,
            status="INDEXING",
        )
        try:
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.INDEXING
            document.last_index_error = ""
            document.save(update_fields=["lifecycle_status", "last_index_error", "updated_at"])
            sync_manual_knowledge_document_to_rag(document=document)
            chunk_outcome = run_chunk_build_for_tenant(configuration=configuration)
            index_outcome = run_index_for_tenant(
                configuration=configuration,
                dry_run=False,
                drive_file_id=drive_file_id,
                provider=provider,
                config=config,
            )
            if chunk_outcome.counters.failed or index_outcome.counters.failed or not self._has_complete_active_index(document=document):
                raise RuntimeError("document_index_incomplete")
            document.content_sha256 = document_rag_text_fingerprint(document)
            document.indexed_content_sha256 = document.content_sha256
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.INDEXED
            document.last_indexed_at = timezone.now()
            document.last_index_error = ""
            document.save(update_fields=["content_sha256", "indexed_content_sha256", "lifecycle_status", "last_indexed_at", "last_index_error", "updated_at"])
            self._record_document_event(
                action=ACTION_KNOWLEDGE_INDEX_COMPLETED,
                document=document,
                actor=actor,
                request=request,
                source=source,
                status=INDEX_COMPLETED,
            )
            return KnowledgeReindexResult(
                tenant=tenant,
                document=document,
                status=INDEX_COMPLETED,
                dry_run=False,
                chunks_created=chunk_outcome.counters.updated,
                chunks_rebuilt=chunk_outcome.counters.exported,
                chunks_unchanged=chunk_outcome.counters.unchanged,
                embeddings_indexed=index_outcome.counters.indexed,
                embeddings_reindexed=index_outcome.counters.reindexed,
                embeddings_unchanged=index_outcome.counters.unchanged,
            )
        except Exception as exc:  # noqa: BLE001
            error = _safe_error(exc)
            document.lifecycle_status = KnowledgeDocument.LifecycleStatus.FAILED
            document.last_index_error = error
            document.save(update_fields=["lifecycle_status", "last_index_error", "updated_at"])
            manifest = TenantRagDriveFileManifest.objects.filter(tenant=tenant, drive_file_id=drive_file_id).first()
            if manifest is not None:
                chunk_ids = list(TenantRagDocumentChunk.objects.filter(tenant=tenant, manifest=manifest, is_active=True).values_list("pk", flat=True))
                if chunk_ids:
                    TenantRagDocumentChunk.objects.filter(tenant=tenant, pk__in=chunk_ids).update(
                        is_active=False,
                        status=TenantRagDocumentChunk.Status.FAILED,
                        updated_at=timezone.now(),
                    )
                    TenantRagChunkEmbedding.objects.filter(tenant=tenant, chunk_id__in=chunk_ids).update(
                        is_active=False,
                        status=TenantRagChunkEmbedding.Status.FAILED,
                        last_error=error,
                        updated_at=timezone.now(),
                    )
            self._record_document_event(
                action=ACTION_KNOWLEDGE_INDEX_FAILED,
                document=document,
                actor=actor,
                request=request,
                source=source,
                status=INDEX_FAILED,
                error=error,
            )
            logger.warning("knowledge_lifecycle.reindex_failed tenant=%s document=%s error=%s", tenant.slug, document.pk, error)
            return KnowledgeReindexResult(tenant=tenant, document=document, status=INDEX_FAILED, dry_run=False, failed=1, error=error)

    def reindex_tenant(self, *, tenant, dry_run=False, provider=None, config=None, actor=None, request=None, source="knowledge_lifecycle"):
        documents = KnowledgeDocument.objects.filter(tenant=tenant, status=KnowledgeDocument.Status.ACTIVE).order_by("id")
        return [
            self.reindex_document(
                tenant=tenant,
                document_id=document.pk,
                dry_run=dry_run,
                provider=provider,
                config=config,
                actor=actor,
                request=request,
                source=source,
            )
            for document in documents
        ]


    def _has_complete_active_index(self, *, document: KnowledgeDocument) -> bool:
        manifest = TenantRagDriveFileManifest.objects.filter(
            tenant=document.tenant,
            drive_file_id=manual_drive_file_id(document),
            is_active=True,
        ).exclude(
            status__in=[
                TenantRagDriveFileManifest.Status.FAILED,
                TenantRagDriveFileManifest.Status.REMOVED,
                TenantRagDriveFileManifest.Status.UNAVAILABLE,
                TenantRagDriveFileManifest.Status.SKIPPED_UNSUPPORTED,
            ]
        ).first()
        if manifest is None:
            return False
        chunks = TenantRagDocumentChunk.objects.filter(
            tenant=document.tenant,
            manifest=manifest,
            is_active=True,
            status=TenantRagDocumentChunk.Status.ACTIVE,
        )
        chunk_count = chunks.count()
        if chunk_count <= 0:
            return False
        embedding_count = TenantRagChunkEmbedding.objects.filter(
            tenant=document.tenant,
            manifest=manifest,
            chunk_id__in=chunks.values("id"),
            is_active=True,
            status=TenantRagChunkEmbedding.Status.ACTIVE,
        ).count()
        return embedding_count >= chunk_count

    def document_state(self, *, document: KnowledgeDocument) -> KnowledgeDocumentState:
        rag_state = manual_document_rag_state(document=document)
        manifest = rag_state.get("manifest")
        lifecycle = document.lifecycle_status or KnowledgeDocument.LifecycleStatus.NEW
        content_sha = document.content_sha256 or ""
        indexed_sha = document.indexed_content_sha256 or ""
        chunks = int(rag_state.get("chunks") or 0)
        embeddings = int(rag_state.get("embeddings") or 0)
        if lifecycle == KnowledgeDocument.LifecycleStatus.NEW and not content_sha and manifest is not None:
            if document.status != KnowledgeDocument.Status.ACTIVE or not getattr(manifest, "is_active", False):
                lifecycle = KnowledgeDocument.LifecycleStatus.DISABLED
            elif getattr(manifest, "status", "") == TenantRagDriveFileManifest.Status.FAILED:
                lifecycle = KnowledgeDocument.LifecycleStatus.FAILED
            elif chunks <= 0 or embeddings < chunks:
                lifecycle = KnowledgeDocument.LifecycleStatus.STALE
            else:
                lifecycle = KnowledgeDocument.LifecycleStatus.INDEXED
        usable = (
            document.status == KnowledgeDocument.Status.ACTIVE
            and lifecycle == KnowledgeDocument.LifecycleStatus.INDEXED
            and bool(content_sha)
            and content_sha == indexed_sha
            and chunks > 0
            and embeddings >= chunks
            and bool(getattr(manifest, "is_active", False))
        )
        legacy_usable = (
            document.status == KnowledgeDocument.Status.ACTIVE
            and not content_sha
            and lifecycle in {"", KnowledgeDocument.LifecycleStatus.NEW}
            and manifest is None
        )
        return KnowledgeDocumentState(
            document=document,
            lifecycle_status=lifecycle,
            usable_for_retrieval=usable or legacy_usable,
            content_sha256=content_sha,
            indexed_content_sha256=indexed_sha,
            manifest_status=str(getattr(manifest, "status", "") or ""),
            manifest_active=bool(getattr(manifest, "is_active", False)),
            active_chunks=chunks,
            active_embeddings=embeddings,
            last_indexed_at=document.last_indexed_at,
            last_error=document.last_index_error or str(getattr(manifest, "last_error", "") or ""),
        )

    def usable_keyword_documents(self, *, tenant):
        docs = list(KnowledgeDocument.objects.filter(tenant=tenant, status=KnowledgeDocument.Status.ACTIVE).order_by("title", "id"))
        usable_ids = [doc.pk for doc in docs if self.document_state(document=doc).usable_for_retrieval]
        return KnowledgeDocument.objects.filter(tenant=tenant, pk__in=usable_ids, status=KnowledgeDocument.Status.ACTIVE)

    def readiness(self, *, tenant) -> KnowledgeReadiness:
        documents = list(KnowledgeDocument.objects.filter(tenant=tenant).order_by("id"))
        if not documents:
            return KnowledgeReadiness(tenant=tenant, status=READINESS_EMPTY, detail="Nenhum documento cadastrado.")
        states = [self.document_state(document=document) for document in documents]
        disabled = sum(1 for item in states if item.document.status != KnowledgeDocument.Status.ACTIVE or item.lifecycle_status == KnowledgeDocument.LifecycleStatus.DISABLED)
        failed = sum(1 for item in states if item.lifecycle_status == KnowledgeDocument.LifecycleStatus.FAILED)
        indexing = sum(1 for item in states if item.lifecycle_status == KnowledgeDocument.LifecycleStatus.INDEXING)
        indexed = sum(1 for item in states if item.usable_for_retrieval)
        enabled = sum(1 for item in states if item.document.status == KnowledgeDocument.Status.ACTIVE)
        stale = sum(
            1
            for item in states
            if item.document.status == KnowledgeDocument.Status.ACTIVE
            and item.lifecycle_status in {KnowledgeDocument.LifecycleStatus.STALE, KnowledgeDocument.LifecycleStatus.IMPORTED, KnowledgeDocument.LifecycleStatus.NEW}
            and not item.usable_for_retrieval
        )
        chunks = TenantRagDocumentChunk.objects.filter(tenant=tenant, is_active=True, status=TenantRagDocumentChunk.Status.ACTIVE).count()
        embeddings = TenantRagChunkEmbedding.objects.filter(tenant=tenant, is_active=True, status=TenantRagChunkEmbedding.Status.ACTIVE).count()
        if indexing:
            status = READINESS_INDEXING
            detail = "Há documento em indexação."
        elif failed:
            status = READINESS_DEGRADED
            detail = "Há documento com falha de indexação."
        elif enabled == 0:
            status = READINESS_EMPTY
            detail = "Não há documento ativo."
        elif stale:
            status = READINESS_STALE if indexed == 0 else READINESS_DEGRADED
            detail = "Há documento ativo aguardando reindexação."
        elif indexed == enabled:
            status = READINESS_READY
            detail = "Documentos ativos estão indexados e utilizáveis."
        else:
            status = READINESS_AVAILABLE
            detail = "Há conhecimento cadastrado, mas sem índice completo."
        return KnowledgeReadiness(
            tenant=tenant,
            status=status,
            documents_total=len(documents),
            documents_enabled=enabled,
            documents_indexed=indexed,
            documents_stale=stale,
            documents_failed=failed,
            documents_disabled=disabled,
            documents_indexing=indexing,
            chunks_active=chunks,
            embeddings_active=embeddings,
            detail=detail,
        )

    def _lifecycle_for_import(self, *, status: str, changed: bool) -> str:
        if status != KnowledgeDocument.Status.ACTIVE:
            return KnowledgeDocument.LifecycleStatus.DISABLED
        return KnowledgeDocument.LifecycleStatus.STALE if changed else KnowledgeDocument.LifecycleStatus.INDEXED

    def _normalize_tags(self, raw_tags) -> list[str]:
        if isinstance(raw_tags, str):
            candidates = raw_tags.replace("\r", "\n").replace(",", "\n").splitlines()
        else:
            candidates = raw_tags or []
        tags = []
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value and value not in tags:
                tags.append(value)
        return tags

    def _record_document_event(self, *, action, document, actor, request, source, status, error=""):
        record_audit_event(
            action=action,
            actor=actor,
            tenant=document.tenant,
            obj=document,
            before_data={},
            after_data={
                "status": status,
                "document_id": document.pk,
                "slug": document.slug,
                "content_sha256": document.content_sha256,
                "indexed_content_sha256": document.indexed_content_sha256,
                "lifecycle_status": document.lifecycle_status,
            },
            metadata={"source": source, "error": error} if error else {"source": source},
            request=request,
        )
