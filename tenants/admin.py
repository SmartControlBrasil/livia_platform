from django.contrib import admin

from audit.models import (
    ACTION_ASSISTANT_PROFILE_UPDATED,
    ACTION_TENANT_CREATED,
    ACTION_TENANT_MEMBERSHIP_CREATED,
    ACTION_TENANT_MEMBERSHIP_DEACTIVATED,
    ACTION_TENANT_MEMBERSHIP_UPDATED,
    ACTION_TENANT_ORIGIN_CREATED,
    ACTION_TENANT_ORIGIN_DEACTIVATED,
    ACTION_TENANT_ORIGIN_UPDATED,
    ACTION_TENANT_UPDATED,
)
from audit.services import audit_model_snapshot, changed_fields, record_audit_event

from .models import AssistantProfile, Tenant, TenantAllowedOrigin, TenantMembership
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

    def save_model(self, request, obj, form, change):
        before_data = {}
        if change:
            before_obj = Tenant.objects.get(pk=obj.pk)
            before_data = audit_model_snapshot(before_obj, fields=form.changed_data)
        super().save_model(request, obj, form, change)
        if change:
            changes = changed_fields(before_data, audit_model_snapshot(obj, fields=form.changed_data))
            if not changes["before"] and not changes["after"]:
                return
            action = ACTION_TENANT_UPDATED
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            action = ACTION_TENANT_CREATED
            before_payload = {}
            after_payload = audit_model_snapshot(obj, fields=["name", "slug", "domain", "is_active"])
        record_audit_event(
            action=action,
            actor=request.user,
            tenant=obj,
            obj=obj,
            before_data=before_payload,
            after_data=after_payload,
            metadata={"source": "django_admin"},
            request=request,
        )


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
        "human_handoff_status",
        "use_ai",
        "primary_goal",
    ]
    list_filter = ["is_widget_enabled", "position", "show_branding", "human_handoff_enabled", "human_handoff_channel", "use_ai", "tenant"]
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
        (
            "Atendimento humano",
            {
                "fields": (
                    "human_handoff_enabled",
                    "human_handoff_channel",
                    "handoff_whatsapp_number",
                    "handoff_whatsapp_label",
                    "handoff_whatsapp_message",
                )
            },
        ),
        ("Datas", {"fields": ("created_at", "updated_at")}),
    )
    actions = [enable_profile_ai, disable_profile_ai]

    @admin.display(description="Atendimento humano", boolean=True)
    def human_handoff_status(self, obj):
        return bool(obj.human_handoff_enabled and obj.has_valid_whatsapp_handoff)

    def save_model(self, request, obj, form, change):
        before_data = {}
        if change:
            before_obj = AssistantProfile.objects.get(pk=obj.pk)
            before_data = audit_model_snapshot(before_obj, fields=form.changed_data)
        super().save_model(request, obj, form, change)
        fields = form.changed_data if change else [
            "tenant",
            "name",
            "tone",
            "primary_goal",
            "use_ai",
            "widget_title",
            "launcher_label",
            "primary_color",
            "position",
            "show_branding",
            "collect_contact_hint",
            "placeholder_text",
            "is_widget_enabled",
            "human_handoff_enabled",
            "human_handoff_channel",
            "handoff_whatsapp_number",
            "handoff_whatsapp_label",
            "handoff_whatsapp_message",
            "is_active",
        ]
        changes = changed_fields(before_data, audit_model_snapshot(obj, fields=fields)) if change else None
        if change and not changes["before"] and not changes["after"]:
            return
        record_audit_event(
            action=ACTION_ASSISTANT_PROFILE_UPDATED,
            actor=request.user,
            tenant=obj.tenant,
            obj=obj,
            before_data=changes["before"] if change else {},
            after_data=changes["after"] if change else audit_model_snapshot(obj, fields=fields),
            metadata={"source": "django_admin", "created": not change},
            request=request,
        )


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ["tenant", "user", "role", "is_active", "updated_at"]
    list_filter = ["tenant", "role", "is_active"]
    search_fields = ["user__username", "user__email", "tenant__name", "tenant__slug"]
    autocomplete_fields = ["tenant", "user", "created_by"]
    readonly_fields = ["created_at", "updated_at", "created_by"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        before_data = {}
        fields = ["role", "is_active"]
        if change:
            before_obj = TenantMembership.objects.select_related("tenant", "user").get(pk=obj.pk)
            before_data = audit_model_snapshot(before_obj, fields=fields)
        elif obj.created_by_id is None:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        if change:
            after_data = audit_model_snapshot(obj, fields=fields)
            changes = changed_fields(before_data, after_data)
            if not changes["before"] and not changes["after"]:
                return
            action = ACTION_TENANT_MEMBERSHIP_DEACTIVATED if changes["after"].get("is_active") is False else ACTION_TENANT_MEMBERSHIP_UPDATED
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            action = ACTION_TENANT_MEMBERSHIP_CREATED
            before_payload = {}
            after_payload = audit_model_snapshot(obj, fields=["role", "is_active"])

        record_audit_event(
            action=action,
            actor=request.user,
            tenant=obj.tenant,
            obj=obj,
            object_repr=f"{obj.user_id} / {obj.tenant.slug} / {obj.role}",
            before_data=before_payload,
            after_data=after_payload,
            metadata={"source": "django_admin", "affected_user_id": obj.user_id},
            request=request,
        )


@admin.register(TenantAllowedOrigin)
class TenantAllowedOriginAdmin(admin.ModelAdmin):
    list_display = ["tenant", "origin", "is_active", "updated_at"]
    list_filter = ["tenant", "is_active"]
    search_fields = ["tenant__name", "tenant__slug", "origin"]
    autocomplete_fields = ["tenant", "created_by"]
    readonly_fields = ["created_at", "updated_at", "created_by"]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        before_data = {}
        fields = ["origin", "is_active"]
        if change:
            before_obj = TenantAllowedOrigin.objects.get(pk=obj.pk)
            before_data = audit_model_snapshot(before_obj, fields=fields)
        elif obj.created_by_id is None:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        if change:
            changes = changed_fields(before_data, audit_model_snapshot(obj, fields=fields))
            if not changes["before"] and not changes["after"]:
                return
            action = ACTION_TENANT_ORIGIN_DEACTIVATED if changes["after"].get("is_active") is False else ACTION_TENANT_ORIGIN_UPDATED
            before_payload = changes["before"]
            after_payload = changes["after"]
        else:
            action = ACTION_TENANT_ORIGIN_CREATED
            before_payload = {}
            after_payload = audit_model_snapshot(obj, fields=fields)

        record_audit_event(
            action=action,
            actor=request.user,
            tenant=obj.tenant,
            obj=obj,
            object_repr=obj.origin,
            before_data=before_payload,
            after_data=after_payload,
            metadata={"source": "django_admin"},
            request=request,
        )
