from django.contrib import admin

from audit.models import ACTION_KNOWLEDGE_DOCUMENT_CREATED, ACTION_KNOWLEDGE_DOCUMENT_UPDATED
from audit.services import audit_model_snapshot, changed_fields, record_audit_event

from .models import KnowledgeDocument


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
