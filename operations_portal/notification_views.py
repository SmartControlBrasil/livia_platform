from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from knowledge_base.models import TenantOperationalNotification
from knowledge_base.rag.operational_notification_services import (
    NotificationError,
    get_or_create_preference,
    mark_all_notifications_read,
    mark_notification_read,
    update_notification_preferences,
)
from tenants.access import CAPABILITY_KNOWLEDGE_BASE_VIEW, get_active_membership

from .access import portal_template_context, resolve_portal_access
from .notification_services import enrich_portal_notification_context, get_notification_list
from .selectors import clean_querystring


def _notification_access(request):
    access = resolve_portal_access(
        request,
        capability=CAPABILITY_KNOWLEDGE_BASE_VIEW,
        allow_global=False,
        require_tenant=True,
    )
    membership = get_active_membership(access.user, access.tenant)
    if membership is None:
        raise PermissionDenied
    return access, membership


def _notification_context(access, membership, **extra):
    context = portal_template_context(access)
    context.update(extra)
    context["active_section"] = "notificacoes"
    context["current_membership"] = membership
    context["preference"] = get_or_create_preference(tenant=access.tenant, membership=membership)
    enrich_portal_notification_context(context, access=access)
    return context


@login_required(login_url="/admin/login/")
def operational_notifications(request):
    access, membership = _notification_access(request)
    filter_key = request.GET.get("filter", "all")
    page = get_notification_list(
        tenant=access.tenant,
        membership=membership,
        page_number=request.GET.get("page", 1),
        filter_key=filter_key,
    )
    return render(
        request,
        "operations_portal/notifications/list.html",
        _notification_context(
            access,
            membership,
            page_obj=page,
            filter_key=filter_key,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def operational_notification_preferences(request):
    access, membership = _notification_access(request)
    pref = get_or_create_preference(tenant=access.tenant, membership=membership)
    if request.method == "POST":
        update_notification_preferences(
            tenant=access.tenant,
            membership=membership,
            actor=request.user,
            request=request,
            in_app_enabled=request.POST.get("in_app_enabled") == "on",
            email_enabled=request.POST.get("email_enabled") == "on",
            notify_on_assignment=request.POST.get("notify_on_assignment") == "on",
            notify_on_escalation=request.POST.get("notify_on_escalation") == "on",
            notify_on_sla_breach=request.POST.get("notify_on_sla_breach") == "on",
            notify_on_resolution=request.POST.get("notify_on_resolution") == "on",
            digest_frequency=request.POST.get("digest_frequency", pref.digest_frequency),
            timezone=request.POST.get("timezone", pref.timezone),
        )
        messages.success(request, "Preferências de notificação atualizadas.")
        return redirect("operations_portal:operational_notification_preferences")
    return render(
        request,
        "operations_portal/notifications/preferences.html",
        _notification_context(access, membership, preference=pref),
    )


@login_required(login_url="/admin/login/")
@require_POST
def operational_notification_mark_read(request, pk: int):
    access, membership = _notification_access(request)
    notification = get_object_or_404(
        TenantOperationalNotification,
        pk=pk,
        tenant=access.tenant,
        recipient_membership=membership,
    )
    try:
        mark_notification_read(notification=notification, membership=membership, actor=request.user, request=request)
        messages.success(request, "Notificação marcada como lida.")
    except NotificationError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "operations_portal:operational_notifications")


@login_required(login_url="/admin/login/")
@require_POST
def operational_notification_mark_all_read(request):
    access, membership = _notification_access(request)
    updated = mark_all_notifications_read(
        tenant=access.tenant,
        membership=membership,
        actor=request.user,
        request=request,
    )
    messages.success(request, f"{updated} notificação(ões) marcada(s) como lida(s).")
    return redirect("operations_portal:operational_notifications")
