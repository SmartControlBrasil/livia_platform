from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from audit.models import ACTION_OUTBOX_REQUEUED
from audit.services import record_audit_event
from integrations.models import OutboxEvent
from tenants.access import CAPABILITY_MEMBERSHIPS_MANAGE, CAPABILITY_PORTAL_VIEW_DASHBOARD
from tenants.models import Tenant

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .integration_services import build_integrations_context, filter_outbox_queryset, requeue_outbox_event_for_portal
from .selectors import clean_querystring


def _tenant_queryset_for_access(access):
    if access.is_global:
        return Tenant.objects.filter(is_active=True).order_by("name")
    return Tenant.objects.filter(pk__in=[tenant.pk for tenant in access.accessible_tenants], is_active=True).order_by("name")


@login_required(login_url="/admin/login/")
def integrations_dashboard(request):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=False, require_tenant=True)
    tenant = access.tenant
    queryset = filter_outbox_queryset(
        tenant=tenant,
        status=str(request.GET.get("status") or "").strip(),
        event_type=str(request.GET.get("event_type") or "").strip(),
    )
    context = {
        "active_section": "integracoes",
        "selected_tenant": tenant,
        "tenants": access.accessible_tenants,
        "outbox_page": Paginator(queryset, 25).get_page(request.GET.get("page", 1)),
        "querystring": clean_querystring(request.GET),
        "can_requeue_outbox": CAPABILITY_MEMBERSHIPS_MANAGE in access.capabilities,
    }
    context.update(build_integrations_context(tenant=tenant))
    context.update(portal_template_context(access))
    return render(request, "operations_portal/integrations/dashboard.html", context)


@login_required(login_url="/admin/login/")
def outbox_event_detail(request, pk):
    access = resolve_portal_access(request, capability=CAPABILITY_PORTAL_VIEW_DASHBOARD, allow_global=False, require_tenant=True)
    event = get_object_or_404(OutboxEvent.objects.filter(tenant=access.tenant), pk=pk)
    context = {
        "active_section": "integracoes",
        "event": event,
        "selected_tenant": access.tenant,
        "safe_payload": _redact_payload(event.payload),
        "safe_metadata": _redact_payload(event.result_metadata),
        "can_requeue_outbox": CAPABILITY_MEMBERSHIPS_MANAGE in access.capabilities,
    }
    context.update(portal_template_context(access))
    return render(request, "operations_portal/integrations/outbox_detail.html", context)


@login_required(login_url="/admin/login/")
def outbox_event_requeue(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    access = resolve_portal_access(request, capability=CAPABILITY_MEMBERSHIPS_MANAGE, allow_global=False, require_tenant=True)
    require_portal_capability(access, CAPABILITY_MEMBERSHIPS_MANAGE)
    event = get_object_or_404(OutboxEvent.objects.filter(tenant=access.tenant), pk=pk)
    result = requeue_outbox_event_for_portal(event=event)
    if result is None:
        messages.warning(request, "Apenas eventos dead_letter ou retry podem ser reenfileirados pelo portal.")
        return redirect("operations_portal:outbox_event_detail", pk=event.pk)
    before, after = result
    record_audit_event(
        action=ACTION_OUTBOX_REQUEUED,
        actor=request.user,
        tenant=event.tenant,
        obj=event,
        before_data=before,
        after_data=after,
        metadata={"source": "operations_portal.integrations", "event_id": str(event.event_id)},
        request=request,
    )
    messages.success(request, "Evento reenfileirado com segurança.")
    return redirect("operations_portal:outbox_event_detail", pk=event.pk)


def _redact_payload(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in ("secret", "token", "password", "authorization", "api_key")):
                safe[key] = "[redacted]"
            else:
                safe[key] = _redact_payload(item)
        return safe
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value
