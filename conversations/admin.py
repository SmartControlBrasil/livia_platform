from django.contrib import admin
from django.utils import timezone

from .models import Conversation, HandoffRequest, Message


def _short_text(value, limit=90):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


@admin.action(description="Marcar handoffs selecionados como resolvidos")
def mark_handoffs_resolved(modeladmin, request, queryset):
    queryset.update(status=HandoffRequest.Status.RESOLVED, resolved_at=timezone.now())


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ["role", "content", "created_at"]
    readonly_fields = ["role", "content", "created_at"]
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "session_id",
        "lead_state",
        "is_qualified",
        "created_at",
        "updated_at",
    ]
    list_filter = ["tenant", "is_qualified", "lead_state"]
    search_fields = [
        "session_id",
        "visitor_name",
        "visitor_email",
        "visitor_phone",
        "tenant__name",
        "tenant__slug",
    ]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["conversation", "role", "content_short", "created_at"]
    list_filter = ["role", "conversation__tenant"]
    search_fields = ["content", "conversation__session_id", "conversation__tenant__name", "conversation__tenant__slug"]
    readonly_fields = ["created_at"]

    @admin.display(description="Content")
    def content_short(self, obj):
        return _short_text(obj.content)


@admin.register(HandoffRequest)
class HandoffRequestAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "status",
        "reason",
        "priority",
        "visitor_name",
        "visitor_phone",
        "created_at",
    ]
    list_filter = ["status", "reason", "priority", "tenant"]
    search_fields = [
        "visitor_name",
        "visitor_company",
        "visitor_phone",
        "visitor_email",
        "summary",
        "conversation__session_id",
        "tenant__name",
        "tenant__slug",
    ]
    readonly_fields = ["created_at", "updated_at", "resolved_at", "metadata"]
    actions = [mark_handoffs_resolved]
