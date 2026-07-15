from django import forms
from django.contrib import admin

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
