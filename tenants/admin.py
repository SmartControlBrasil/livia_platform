from django.contrib import admin

from .models import AssistantProfile, Tenant
from .services.install_package import build_install_url
from .services.onboarding import build_widget_snippet


@admin.action(description="Marcar tenants selecionados como ativos")
def activate_tenants(modeladmin, request, queryset):
    queryset.update(is_active=True)


@admin.action(description="Marcar tenants selecionados como inativos")
def deactivate_tenants(modeladmin, request, queryset):
    queryset.update(is_active=False)


@admin.action(description="Ativar IA nos perfis selecionados")
def enable_profile_ai(modeladmin, request, queryset):
    queryset.update(use_ai=True)


@admin.action(description="Desativar IA nos perfis selecionados")
def disable_profile_ai(modeladmin, request, queryset):
    queryset.update(use_ai=False)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "domain", "is_active", "created_at", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "domain"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = ["created_at", "updated_at", "install_url", "widget_snippet_preview"]
    actions = [activate_tenants, deactivate_tenants]

    @admin.display(description="Install URL")
    def install_url(self, obj):
        if not obj or not obj.slug:
            return ""
        return build_install_url(obj.slug)

    @admin.display(description="Widget snippet")
    def widget_snippet_preview(self, obj):
        if not obj or not obj.slug:
            return ""
        return build_widget_snippet(obj.slug)


@admin.register(AssistantProfile)
class AssistantProfileAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "name",
        "widget_title",
        "launcher_label",
        "primary_color",
        "position",
        "is_widget_enabled",
        "use_ai",
        "primary_goal",
    ]
    list_filter = ["is_widget_enabled", "position", "show_branding", "use_ai", "tenant"]
    search_fields = [
        "name",
        "widget_title",
        "launcher_label",
        "tenant__name",
        "tenant__slug",
        "primary_goal",
    ]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("tenant", "name", "initial_message", "tone", "primary_goal", "use_ai", "is_active")}),
        (
            "Widget",
            {
                "fields": (
                    "widget_title",
                    "launcher_label",
                    "primary_color",
                    "position",
                    "show_branding",
                    "collect_contact_hint",
                    "placeholder_text",
                    "is_widget_enabled",
                )
            },
        ),
        ("Datas", {"fields": ("created_at", "updated_at")}),
    )
    actions = [enable_profile_ai, disable_profile_ai]
