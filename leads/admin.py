from django.contrib import admin

from .models import LeadDraft


@admin.register(LeadDraft)
class LeadDraftAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "company",
        "email",
        "phone",
        "tenant",
        "status",
        "updated_at",
    ]
    list_filter = ["tenant", "status"]
    search_fields = [
        "name",
        "company",
        "email",
        "phone",
        "city",
        "need_summary",
        "tenant__name",
        "tenant__slug",
    ]
    readonly_fields = ["created_at", "updated_at", "sent_to_crm_at"]
