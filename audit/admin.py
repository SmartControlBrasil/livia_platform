from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["created_at", "action", "tenant", "actor", "object_type", "object_id", "object_repr", "ip_address"]
    list_filter = ["action", "tenant", "created_at"]
    search_fields = ["object_type", "object_id", "object_repr", "actor__username", "actor__email"]
    readonly_fields = [
        "tenant",
        "actor",
        "action",
        "object_type",
        "object_id",
        "object_repr",
        "before_data",
        "after_data",
        "metadata",
        "ip_address",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
