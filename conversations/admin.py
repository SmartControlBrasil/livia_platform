from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["role", "content", "created_at"]
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        "tenant",
        "session_id",
        "visitor_name",
        "visitor_email",
        "visitor_phone",
        "is_qualified",
        "updated_at",
    ]
    list_filter = ["tenant", "is_qualified"]
    search_fields = [
        "session_id",
        "visitor_name",
        "visitor_email",
        "visitor_phone",
        "tenant__name",
        "tenant__slug",
    ]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["conversation", "role", "created_at"]
    list_filter = ["role", "conversation__tenant"]
    search_fields = ["content", "conversation__session_id"]
