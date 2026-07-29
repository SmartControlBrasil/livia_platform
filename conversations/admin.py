from django.contrib import admin
from django.utils import timezone

from .models import ChatRequest, Conversation, HandoffRequest, Message


def _short_text(value, limit=90):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


@admin.action(description="Marcar handoffs selecionados como resolvidos")
def mark_handoffs_resolved(modeladmin, request, queryset):
    queryset.update(status=HandoffRequest.Status.RESOLVED, resolved_at=timezone.now())


@admin.action(description="Marcar handoffs selecionados como cancelados")
def mark_handoffs_cancelled(modeladmin, request, queryset):
    queryset.update(status=HandoffRequest.Status.CANCELLED)


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
        "lead_draft_status",
        "handoff_status",
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

    @admin.display(description="LeadDraft")
    def lead_draft_status(self, obj):
        lead_draft = getattr(obj, "lead_draft", None)
        if lead_draft is None:
            return "-"
        return lead_draft.status

    @admin.display(description="Handoff")
    def handoff_status(self, obj):
        handoff = obj.handoff_requests.order_by("-created_at").first()
        if handoff is None:
            return "-"
        return f"{handoff.status} / {handoff.priority}"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["conversation", "role", "short_content", "created_at"]
    list_filter = ["role", "created_at"]
    search_fields = ["content", "conversation__session_id", "conversation__tenant__name", "conversation__tenant__slug"]
    readonly_fields = ["created_at"]

    @admin.display(description="Content")
    def short_content(self, obj):
        return _short_text(obj.content)


@admin.register(HandoffRequest)
class HandoffRequestAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "conversation",
        "lead_draft",
        "status",
        "reason",
        "priority",
        "visitor_name",
        "visitor_phone",
        "short_summary",
        "created_at",
        "resolved_at",
    ]
    list_filter = ["tenant", "status", "reason", "priority", "created_at"]
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
    actions = [mark_handoffs_resolved, mark_handoffs_cancelled]

    @admin.display(description="Summary")
    def short_summary(self, obj):
        return _short_text(obj.summary)


@admin.register(ChatRequest)
class ChatRequestAdmin(admin.ModelAdmin):
    list_display = ["tenant", "request_id", "status", "created_at", "completed_at"]
    list_filter = ["tenant", "status", "created_at", "completed_at"]
    search_fields = ["request_id", "session_id", "tenant__slug", "tenant__name"]
    readonly_fields = [
        "tenant",
        "conversation",
        "session_id",
        "request_id",
        "status",
        "request_fingerprint",
        "response_status_code",
        "error_code",
        "created_at",
        "updated_at",
        "completed_at",
        "safe_response_payload",
    ]
    fields = readonly_fields

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

    @admin.display(description="Response payload")
    def safe_response_payload(self, obj):
        return _short_text(obj.response_payload, limit=240)
