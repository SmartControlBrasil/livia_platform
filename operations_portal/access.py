from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from tenants.access import (
    ALL_CAPABILITIES,
    CAPABILITY_ASSISTANT_PROFILE_CHANGE,
    CAPABILITY_ASSISTANT_PROFILE_VIEW,
    CAPABILITY_COMMERCIAL_MANAGE,
    CAPABILITY_COMMERCIAL_VIEW,
    CAPABILITY_CONVERSATIONS_VIEW,
    CAPABILITY_HANDOFFS_CHANGE_STATUS,
    CAPABILITY_HANDOFFS_VIEW,
    CAPABILITY_KNOWLEDGE_BASE_CONFIGURE,
    CAPABILITY_KNOWLEDGE_BASE_OPERATE,
    CAPABILITY_KNOWLEDGE_BASE_REINDEX,
    CAPABILITY_KNOWLEDGE_BASE_VIEW,
    CAPABILITY_LEADS_RETRY_CRM,
    CAPABILITY_LEADS_VIEW,
    CAPABILITY_MEMBERSHIPS_MANAGE,
    CAPABILITY_MEMBERSHIPS_VIEW,
    CAPABILITY_PORTAL_VIEW_DASHBOARD,
    capabilities_for_user,
    get_accessible_tenants,
    require_tenant_capability,
    user_has_tenant_capability,
)

SESSION_ACTIVE_TENANT_KEY = "operations_portal_active_tenant_id"


@dataclass(frozen=True)
class PortalAccessContext:
    user: object
    tenant: object | None
    accessible_tenants: list
    is_global: bool
    capabilities: frozenset

    @property
    def tenant_scope_note(self):
        if self.is_global:
            return "Consolidação administrativa de todos os tenants."
        if self.tenant is None:
            return "Nenhum tenant selecionado."
        return f"Tenant ativo: {self.tenant.name}."

    @property
    def template_capabilities(self):
        return {
            "portal_view_dashboard": CAPABILITY_PORTAL_VIEW_DASHBOARD in self.capabilities,
            "conversations_view": CAPABILITY_CONVERSATIONS_VIEW in self.capabilities,
            "leads_view": CAPABILITY_LEADS_VIEW in self.capabilities,
            "leads_retry_crm": CAPABILITY_LEADS_RETRY_CRM in self.capabilities,
            "handoffs_view": CAPABILITY_HANDOFFS_VIEW in self.capabilities,
            "handoffs_change_status": CAPABILITY_HANDOFFS_CHANGE_STATUS in self.capabilities,
            "commercial_view": CAPABILITY_COMMERCIAL_VIEW in self.capabilities,
            "commercial_manage": CAPABILITY_COMMERCIAL_MANAGE in self.capabilities,
            "assistant_profile_view": CAPABILITY_ASSISTANT_PROFILE_VIEW in self.capabilities,
            "assistant_profile_change": CAPABILITY_ASSISTANT_PROFILE_CHANGE in self.capabilities,
            "memberships_view": CAPABILITY_MEMBERSHIPS_VIEW in self.capabilities,
            "memberships_manage": CAPABILITY_MEMBERSHIPS_MANAGE in self.capabilities,
            "knowledge_base_view": CAPABILITY_KNOWLEDGE_BASE_VIEW in self.capabilities,
            "knowledge_base_configure": CAPABILITY_KNOWLEDGE_BASE_CONFIGURE in self.capabilities,
            "knowledge_base_operate": CAPABILITY_KNOWLEDGE_BASE_OPERATE in self.capabilities,
            "knowledge_base_reindex": CAPABILITY_KNOWLEDGE_BASE_REINDEX in self.capabilities,
        }


def resolve_portal_access(request, *, capability, allow_global=False, require_tenant=False):
    user = request.user
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied

    accessible_tenants = list(get_accessible_tenants(user))
    selected_tenant = _resolve_selected_tenant(request, accessible_tenants, allow_global=allow_global)

    if require_tenant and selected_tenant is None:
        raise PermissionDenied
    if not accessible_tenants and not user.is_superuser:
        raise PermissionDenied

    is_global = user.is_superuser and selected_tenant is None
    capabilities = ALL_CAPABILITIES if is_global else capabilities_for_user(user, selected_tenant)
    context = PortalAccessContext(
        user=user,
        tenant=selected_tenant,
        accessible_tenants=accessible_tenants,
        is_global=is_global,
        capabilities=capabilities,
    )

    if is_global:
        if capability not in ALL_CAPABILITIES:
            raise PermissionDenied
    else:
        require_tenant_capability(user, selected_tenant, capability)

    return context


def require_portal_capability(context, capability):
    if context.is_global:
        return True
    return require_tenant_capability(context.user, context.tenant, capability)


def portal_template_context(context):
    payload = {
        "portal_access": context,
        "active_tenant": context.tenant,
        "accessible_tenants": context.accessible_tenants,
        "portal_is_global": context.is_global,
        "portal_caps": context.template_capabilities,
        "tenant_scope_note": context.tenant_scope_note,
        "unread_notification_count": 0,
    }
    if context.tenant is not None and not context.is_global:
        from knowledge_base.rag.operational_notification_services import count_unread_notifications
        from tenants.access import get_active_membership

        membership = get_active_membership(context.user, context.tenant)
        if membership is not None:
            payload["unread_notification_count"] = count_unread_notifications(
                tenant=context.tenant,
                membership=membership,
            )
    return payload


def can_access_tenant(user, tenant):
    return user_has_tenant_capability(user, tenant, CAPABILITY_PORTAL_VIEW_DASHBOARD)


def _resolve_selected_tenant(request, accessible_tenants, *, allow_global):
    tenant_ids = {str(tenant.pk): tenant for tenant in accessible_tenants}
    requested = request.POST.get("tenant") or request.GET.get("tenant")
    if requested:
        if requested == "global" and request.user.is_superuser and allow_global:
            request.session.pop(SESSION_ACTIVE_TENANT_KEY, None)
            return None
        tenant = tenant_ids.get(str(requested))
        if tenant is None:
            raise PermissionDenied
        request.session[SESSION_ACTIVE_TENANT_KEY] = str(tenant.pk)
        return tenant

    stored = request.session.get(SESSION_ACTIVE_TENANT_KEY)
    if stored:
        tenant = tenant_ids.get(str(stored))
        if tenant is None:
            raise PermissionDenied
        return tenant

    if request.user.is_superuser and allow_global:
        return None
    return accessible_tenants[0] if accessible_tenants else None
