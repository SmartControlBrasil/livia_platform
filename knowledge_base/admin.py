from django.contrib import admin

from .models import KnowledgeDocument


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "tenant", "status", "updated_at"]
    list_filter = ["tenant", "status"]
    search_fields = ["title", "slug", "content", "tenant__name", "tenant__slug"]
    prepopulated_fields = {"slug": ["title"]}
