from django.contrib import admin

from assistant_core.discovery import analyze_message

from .models import LeadDraft


class LeadServiceAreaFilter(admin.SimpleListFilter):
    title = "service area"
    parameter_name = "service_area"

    def lookups(self, request, model_admin):
        return [
            ("automation", "Automation"),
            ("robotics", "Robotics"),
            ("maintenance", "Maintenance"),
            ("software_web", "Software/Web"),
            ("support", "Support"),
            ("unknown", "Unknown"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        matching_ids = [
            lead.pk
            for lead in queryset.only("id", "need_summary")
            if analyze_message(lead.need_summary).service_area == value
        ]
        return queryset.filter(pk__in=matching_ids)


@admin.register(LeadDraft)
class LeadDraftAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "conversation",
        "name",
        "company",
        "phone",
        "email",
        "status",
        "service_area",
        "created_at",
    ]
    list_filter = ["status", "tenant", LeadServiceAreaFilter]
    search_fields = [
        "name",
        "company",
        "email",
        "phone",
        "city",
        "need_summary",
        "tenant__name",
        "tenant__slug",
        "conversation__session_id",
    ]
    readonly_fields = ["created_at", "updated_at", "sent_to_crm_at"]

    @admin.display(description="Service area")
    def service_area(self, obj):
        return analyze_message(obj.need_summary).service_area
