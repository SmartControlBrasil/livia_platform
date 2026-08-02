from django.contrib import admin

from audit.models import (
    ACTION_KNOWLEDGE_DOCUMENT_CREATED,
    ACTION_KNOWLEDGE_DOCUMENT_UPDATED,
    ACTION_TENANT_RAG_CONFIGURED,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event

from .models import (
    KnowledgeDocument,
    RagRetrievalEvent,
    TenantRagChunkEmbedding,
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
    TenantRagIndexRun,
)


@admin.action(description="Ativar documentos selecionados")
def activate_knowledge_documents(modeladmin, request, queryset):
    queryset.update(status=KnowledgeDocument.Status.ACTIVE)


@admin.action(description="Arquivar documentos selecionados")
def deactivate_knowledge_documents(modeladmin, request, queryset):
    queryset.update(status=KnowledgeDocument.Status.ARCHIVED)


class KnowledgeIsActiveFilter(admin.SimpleListFilter):
    title = "is active"
    parameter_name = "is_active"

    def lookups(self, request, model_admin):
        return [("yes", "Yes"), ("no", "No")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(status=KnowledgeDocument.Status.ACTIVE)
        if self.value() == "no":
            return queryset.exclude(status=KnowledgeDocument.Status.ACTIVE)
        return queryset


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ["tenant", "title", "source_type", "is_active", "tags", "updated_at"]
    list_filter = ["tenant", KnowledgeIsActiveFilter, "source_type"]
    search_fields = ["title", "slug", "content", "tags", "source_url", "tenant__name", "tenant__slug"]
    prepopulated_fields = {"slug": ["title"]}
    readonly_fields = ["created_at", "updated_at"]
    actions = [activate_knowledge_documents, deactivate_knowledge_documents]

    audit_fields = ["tenant", "title", "slug", "source_type", "source_url", "tags", "status"]

    def save_model(self, request, obj, form, change):
        before_data = {}
        if change:
            before_obj = KnowledgeDocument.objects.get(pk=obj.pk)
            fields = [field for field in form.changed_data if field in self.audit_fields]
            before_data = audit_model_snapshot(before_obj, fields=fields)
        else:
            fields = self.audit_fields
        super().save_model(request, obj, form, change)
        if change:
            changes = changed_fields(before_data, audit_model_snapshot(obj, fields=fields))
            if not changes["before"] and not changes["after"]:
                return
            action = ACTION_KNOWLEDGE_DOCUMENT_UPDATED
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            action = ACTION_KNOWLEDGE_DOCUMENT_CREATED
            before_payload = {}
            after_payload = audit_model_snapshot(obj, fields=fields)
        record_audit_event(
            action=action,
            actor=request.user,
            tenant=obj.tenant,
            obj=obj,
            before_data=before_payload,
            after_data=after_payload,
            metadata={"source": "django_admin"},
            request=request,
        )


@admin.register(TenantRagConfiguration)
class TenantRagConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "sync_enabled",
        "retrieval_enabled",
        "min_similarity_score",
        "max_retrieved_chunks",
        "max_context_chars",
        "retrieval_timeout_seconds",
        "approved_folder_id",
        "last_inventory_status",
        "last_inventory_file_count",
        "last_inventory_at",
        "last_index_status",
        "last_index_at",
    ]
    list_filter = ["sync_enabled", "retrieval_enabled", "last_inventory_status", "last_index_status"]
    search_fields = ["tenant__slug", "tenant__name", "approved_folder_id"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "last_inventory_started_at",
        "last_inventory_mode",
        "last_inventory_at",
        "last_inventory_file_count",
        "last_inventory_error",
        "last_index_started_at",
        "last_index_mode",
        "last_index_at",
        "last_index_run_id",
        "last_index_error",
    ]
    audit_fields = [
        "approved_folder_id",
        "sync_enabled",
        "retrieval_enabled",
        "min_similarity_score",
        "max_retrieved_chunks",
        "max_context_chars",
        "retrieval_timeout_seconds",
    ]

    def save_model(self, request, obj, form, change):
        before_data = {}
        fields = self.audit_fields
        if change:
            before_obj = TenantRagConfiguration.objects.get(pk=obj.pk)
            fields = [field for field in form.changed_data if field in self.audit_fields]
            before_data = audit_model_snapshot(before_obj, fields=fields)
        super().save_model(request, obj, form, change)
        if not fields:
            return
        if change:
            changes = changed_fields(before_data, audit_model_snapshot(obj, fields=fields))
            if not changes["before"] and not changes["after"]:
                return
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            before_payload = {}
            after_payload = audit_model_snapshot(obj, fields=fields)
        record_audit_event(
            action=ACTION_TENANT_RAG_CONFIGURED,
            actor=request.user,
            tenant=obj.tenant,
            obj=obj,
            before_data=before_payload,
            after_data=after_payload,
            metadata={"source": "django_admin"},
            request=request,
        )


@admin.register(TenantRagDriveFileManifest)
class TenantRagDriveFileManifestAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "drive_file_id",
        "name",
        "mime_type",
        "status",
        "is_active",
        "last_seen_at",
        "last_exported_at",
    ]
    list_filter = ["tenant", "status", "is_active", "mime_type"]
    search_fields = ["tenant__slug", "name", "drive_file_id", "relative_path"]
    readonly_fields = [
        "tenant",
        "configuration",
        "drive_file_id",
        "name",
        "mime_type",
        "relative_path",
        "drive_modified_time",
        "drive_size_bytes",
        "normalized_text_sha256",
        "status",
        "is_active",
        "first_discovered_at",
        "last_seen_at",
        "last_exported_at",
        "removed_at",
        "last_error",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(TenantRagDriveTextStaging)
class TenantRagDriveTextStagingAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "manifest",
        "normalized_text_sha256",
        "normalized_text_char_count",
        "normalized_text_byte_count",
        "exported_at",
    ]
    list_filter = ["tenant"]
    search_fields = ["tenant__slug", "manifest__drive_file_id", "manifest__name"]
    readonly_fields = [
        "tenant",
        "manifest",
        "normalized_text_sha256",
        "normalized_text_char_count",
        "normalized_text_byte_count",
        "exported_at",
        "created_at",
        "updated_at",
    ]
    exclude = ["normalized_text"]

    def has_add_permission(self, request):
        return False


@admin.register(TenantRagDocumentChunk)
class TenantRagDocumentChunkAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "manifest",
        "ordinal",
        "chunk_sha256",
        "source_text_sha256",
        "chunk_config_signature",
        "char_count",
        "byte_count",
        "status",
        "is_active",
        "updated_at",
    ]
    list_filter = ["tenant", "status", "is_active"]
    search_fields = ["tenant__slug", "manifest__drive_file_id", "chunk_sha256", "source_text_sha256"]
    readonly_fields = [
        "tenant",
        "manifest",
        "staging",
        "ordinal",
        "chunk_sha256",
        "source_text_sha256",
        "chunk_config_signature",
        "char_count",
        "byte_count",
        "start_char",
        "end_char",
        "status",
        "is_active",
        "created_at",
        "updated_at",
    ]
    exclude = ["chunk_text"]

    def has_add_permission(self, request):
        return False


@admin.register(TenantRagChunkEmbedding)
class TenantRagChunkEmbeddingAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "chunk",
        "manifest",
        "chunk_sha256",
        "provider",
        "model",
        "dimension",
        "embedding_config_signature",
        "status",
        "is_active",
        "last_indexed_at",
    ]
    list_filter = ["tenant", "status", "is_active", "provider", "model"]
    search_fields = [
        "tenant__slug",
        "chunk_sha256",
        "embedding_config_signature",
        "chunk__id",
        "manifest__drive_file_id",
    ]
    readonly_fields = [
        "tenant",
        "chunk",
        "manifest",
        "chunk_sha256",
        "chunk_config_signature",
        "provider",
        "model",
        "dimension",
        "embedding_config_signature",
        "status",
        "is_active",
        "first_indexed_at",
        "last_indexed_at",
        "last_error",
        "created_at",
        "updated_at",
        "vector_dimension_display",
    ]
    exclude = ["vector"]

    @admin.display(description="vector dimension")
    def vector_dimension_display(self, obj):
        if obj is None:
            return ""
        if isinstance(obj.vector, list):
            return len(obj.vector)
        return 0

    def has_add_permission(self, request):
        return False


@admin.register(TenantRagIndexRun)
class TenantRagIndexRunAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "run_id",
        "mode",
        "status",
        "dry_run",
        "provider",
        "model",
        "dimension",
        "indexed",
        "reindexed",
        "unchanged",
        "failed",
        "started_at",
        "finished_at",
    ]
    list_filter = ["tenant", "status", "mode", "dry_run", "provider"]
    search_fields = ["tenant__slug", "run_id", "embedding_config_signature"]
    readonly_fields = [
        "tenant",
        "run_id",
        "mode",
        "provider",
        "model",
        "dimension",
        "embedding_config_signature",
        "status",
        "dry_run",
        "documents",
        "chunks",
        "pending",
        "indexed",
        "reindexed",
        "unchanged",
        "deactivated",
        "skipped",
        "failed",
        "batches",
        "last_error",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(RagRetrievalEvent)
class RagRetrievalEventAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "status",
        "hit",
        "backend",
        "dry_run",
        "provider",
        "model",
        "result_count",
        "candidate_count",
        "max_score",
        "duration_ms",
        "created_at",
    ]
    list_filter = ["tenant", "status", "hit", "dry_run", "backend", "provider"]
    search_fields = ["tenant__slug", "reason", "backend", "model"]
    readonly_fields = [
        "tenant",
        "conversation_id",
        "status",
        "reason",
        "backend",
        "provider",
        "model",
        "duration_ms",
        "candidate_count",
        "result_count",
        "max_score",
        "threshold",
        "threshold_source",
        "dry_run",
        "hit",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False
