from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .selectors import get_dashboard_context, has_secure_portal_scope, tenant_scope_note

PLACEHOLDERS = {
    "conversas": "Conversas",
    "leads": "Leads",
    "handoffs": "Handoffs",
    "tenants": "Tenants",
    "base-de-conhecimento": "Base de conhecimento",
    "integracoes": "Integrações",
    "configuracoes": "Configurações",
}


@login_required(login_url="/admin/login/")
def dashboard(request):
    _require_staff_scope(request.user)
    context = get_dashboard_context()
    context.update({"active_section": "overview", "tenant_scope_note": tenant_scope_note(request.user)})
    return render(request, "operations_portal/dashboard.html", context)


@login_required(login_url="/admin/login/")
def placeholder(request, section):
    _require_staff_scope(request.user)
    title = PLACEHOLDERS.get(section)
    if title is None:
        raise PermissionDenied
    return render(
        request,
        "operations_portal/placeholder.html",
        {
            "title": title,
            "active_section": section,
            "tenant_scope_note": tenant_scope_note(request.user),
        },
    )


def _require_staff_scope(user):
    if not user.is_staff:
        raise PermissionDenied
    if not has_secure_portal_scope(user):
        raise PermissionDenied
