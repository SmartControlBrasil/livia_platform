from django.contrib import admin

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
