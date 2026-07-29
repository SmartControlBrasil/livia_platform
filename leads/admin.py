from django.contrib import admin, messages

from assistant_core.discovery import analyze_message
from audit.models import ACTION_LEAD_CRM_DISPATCH_RETRIED
from audit.services import audit_model_snapshot, record_audit_event
from integrations.outbox.service import enqueue_lead_qualified

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



@admin.action(description="Reprocessar envio ao CRM dos LeadDrafts com falha")
def retry_failed_crm_dispatch(modeladmin, request, queryset):
    selected = queryset.select_related("tenant", "conversation")
    retried = 0
    skipped = 0
    succeeded = 0
    failed = 0

    for lead_draft in selected:
        if lead_draft.status != LeadDraft.Status.FAILED:
            skipped += 1
            continue
        if lead_draft.crm_external_id or lead_draft.sent_to_crm_at:
            skipped += 1
            continue

        lead_draft.status = LeadDraft.Status.QUALIFIED
        lead_draft.crm_error = ""
        lead_draft.save(update_fields=["status", "crm_error", "updated_at"])
        before_data = audit_model_snapshot(lead_draft, fields=["status", "crm_error", "crm_external_id", "sent_to_crm_at"])
        event, created = enqueue_lead_qualified(lead_draft)
        retried += 1
        if created:
            succeeded += 1
        else:
            skipped += 1
        record_audit_event(
            action=ACTION_LEAD_CRM_DISPATCH_RETRIED,
            actor=request.user,
            tenant=lead_draft.tenant,
            obj=lead_draft,
            before_data=before_data,
            after_data=audit_model_snapshot(lead_draft, fields=["status", "crm_error", "crm_external_id", "sent_to_crm_at"]),
            metadata={"source": "django_admin", "outbox_event_id": str(event.event_id), "outbox_created": created},
            request=request,
        )

    modeladmin.message_user(
        request,
        (
            f"Reprocessamento CRM enfileirado: {retried} tentativa(s), "
            f"{succeeded} evento(s) novo(s), {failed} falha(s), {skipped} ignorado(s)."
        ),
        messages.INFO,
    )


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
        "crm_dispatch_state",
        "crm_external_id",
        "sent_to_crm_at",
        "created_at",
        "updated_at",
    ]
    list_filter = ["tenant", "status", LeadServiceAreaFilter, "sent_to_crm_at", "created_at"]
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
        "crm_external_id",
        "crm_error",
    ]
    readonly_fields = [
        "crm_dispatch_state",
        "crm_external_id",
        "crm_error",
        "created_at",
        "updated_at",
        "sent_to_crm_at",
    ]
    actions = [retry_failed_crm_dispatch]

    @admin.display(description="Service area")
    def service_area(self, obj):
        return analyze_message(obj.need_summary).service_area

    @admin.display(description="CRM dispatch")
    def crm_dispatch_state(self, obj):
        if obj.status == LeadDraft.Status.SENT_TO_CRM:
            return "sent"
        if obj.status == LeadDraft.Status.FAILED:
            return "failed"
        if obj.status == LeadDraft.Status.QUALIFIED:
            return "ready"
        return "pending"
