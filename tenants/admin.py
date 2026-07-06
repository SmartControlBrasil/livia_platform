from django.contrib import admin

from .models import AssistantProfile, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "domain", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "domain"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(AssistantProfile)
class AssistantProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant", "primary_goal", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "tenant__name", "tenant__slug"]
