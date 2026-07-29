from django import forms
from django.contrib import admin, messages
from django.utils import timezone

from audit.models import ACTION_OUTBOX_REQUEUED, ACTION_WEBHOOK_CONFIG_CREATED, ACTION_WEBHOOK_CONFIG_UPDATED
from audit.services import audit_model_snapshot, changed_fields, record_audit_event

from .models import OutboxEvent, TenantWebhookConfig, WebhookDeliveryLog


def _short_text(value, limit=120):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


@admin.action(description="Ativar configs selecionadas")
def activate_webhook_configs(modeladmin, request, queryset):
    queryset.update(is_active=True)


@admin.action(description="Desativar configs selecionadas")
def deactivate_webhook_configs(modeladmin, request, queryset):
    queryset.update(is_active=False)


@admin.action(description="Marcar configs selecionadas como dry-run")
def mark_webhook_configs_dry_run(modeladmin, request, queryset):
    queryset.update(dry_run=True)


class TenantWebhookConfigAdminForm(forms.ModelForm):
    class Meta:
        model = TenantWebhookConfig
        fields = "__all__"
        widgets = {"secret_token": forms.PasswordInput(render_value=False)}


@admin.register(TenantWebhookConfig)
class TenantWebhookConfigAdmin(admin.ModelAdmin):
    form = TenantWebhookConfigAdminForm
    list_display = ["tenant", "name", "event_type", "is_active", "dry_run", "target_url", "updated_at"]
    list_filter = ["tenant", "event_type", "is_active", "dry_run"]
    search_fields = ["tenant__name", "tenant__slug", "name", "target_url"]
    readonly_fields = ["created_at", "updated_at"]
    actions = [activate_webhook_configs, deactivate_webhook_configs, mark_webhook_configs_dry_run]

    audit_fields = ["tenant", "name", "event_type", "target_url", "secret_token", "is_active", "dry_run"]

    def save_model(self, request, obj, form, change):
        before_data = {}
        fields = [field for field in (form.changed_data if change else self.audit_fields) if field in self.audit_fields]
        if change:
            before_obj = TenantWebhookConfig.objects.get(pk=obj.pk)
            before_data = audit_model_snapshot(before_obj, fields=fields)
        super().save_model(request, obj, form, change)
        if change:
            changes = changed_fields(before_data, audit_model_snapshot(obj, fields=fields))
            if not changes["before"] and not changes["after"]:
                return
            action = ACTION_WEBHOOK_CONFIG_UPDATED
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            action = ACTION_WEBHOOK_CONFIG_CREATED
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


@admin.register(WebhookDeliveryLog)
class WebhookDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ["tenant", "event_type", "status", "status_code", "created_at"]
    list_filter = ["tenant", "event_type", "status", "created_at"]
    search_fields = ["tenant__name", "tenant__slug", "error_message"]
    readonly_fields = [
        "tenant",
        "webhook_config",
        "event_type",
        "status",
        "status_code",
        "error_message",
        "payload_preview",
        "related_handoff",
        "related_lead",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.action(description="Reenfileirar eventos dead_letter/retry selecionados", permissions=["view"])
def requeue_outbox_events(modeladmin, request, queryset):
    eligible = queryset.filter(status__in=[OutboxEvent.Status.DEAD_LETTER, OutboxEvent.Status.RETRY])
    updated = 0
    for event in eligible.select_related("tenant"):
        before = {"status": event.status, "attempts": event.attempts, "locked_by": event.locked_by}
        event.status = OutboxEvent.Status.PENDING
        event.available_at = timezone.now()
        event.locked_at = None
        event.locked_by = ""
        event.last_error_code = "manual_requeue"
        event.last_error_message = "Manual requeue from Django Admin."
        event.save(update_fields=["status", "available_at", "locked_at", "locked_by", "last_error_code", "last_error_message", "updated_at"])
        record_audit_event(
            action=ACTION_OUTBOX_REQUEUED,
            actor=request.user,
            tenant=event.tenant,
            obj=event,
            before_data=before,
            after_data={"status": event.status, "attempts": event.attempts, "locked_by": event.locked_by},
            metadata={"source": "django_admin", "event_id": str(event.event_id)},
            request=request,
        )
        updated += 1
    modeladmin.message_user(request, f"{updated} evento(s) reenfileirado(s).", messages.INFO)


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ["tenant", "event_id", "event_type", "status", "attempts", "available_at", "processed_at", "created_at"]
    list_filter = ["tenant", "status", "event_type", "created_at"]
    search_fields = ["event_id", "aggregate_id", "locked_by", "tenant__slug", "tenant__name"]
    readonly_fields = [
        "event_id", "tenant", "event_type", "aggregate_type", "aggregate_id", "deduplication_key",
        "safe_payload", "status", "attempts", "max_attempts", "available_at", "locked_at", "locked_by",
        "last_attempt_at", "processed_at", "last_error_code", "last_error_message", "safe_result_metadata",
        "created_at", "updated_at",
    ]
    fields = readonly_fields
    actions = [requeue_outbox_events]

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Payload")
    def safe_payload(self, obj):
        return _short_text(obj.payload, 400)

    @admin.display(description="Result metadata")
    def safe_result_metadata(self, obj):
        return _short_text(obj.result_metadata, 300)
