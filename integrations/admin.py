from django import forms
from django.contrib import admin

from audit.models import ACTION_WEBHOOK_CONFIG_CREATED, ACTION_WEBHOOK_CONFIG_UPDATED
from audit.services import audit_model_snapshot, changed_fields, record_audit_event

from .models import TenantWebhookConfig, WebhookDeliveryLog


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
