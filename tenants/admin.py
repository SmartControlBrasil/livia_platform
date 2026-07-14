from django.contrib import admin

from .models import AssistantProfile, Tenant


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
    list_display = ["name", "slug", "domain", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "domain"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = ["created_at", "updated_at"]
    actions = [activate_tenants, deactivate_tenants]


@admin.register(AssistantProfile)
class AssistantProfileAdmin(admin.ModelAdmin):
    list_display = ["tenant", "name", "use_ai", "primary_goal", "is_active"]
    list_filter = ["use_ai", "is_active", "tenant"]
    search_fields = ["name", "tenant__name", "tenant__slug", "primary_goal"]
    readonly_fields = ["created_at", "updated_at"]
    actions = [enable_profile_ai, disable_profile_ai]
