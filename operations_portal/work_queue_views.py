from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from knowledge_base.rag.operational_work_queue_services import WorkQueueError
from tenants.access import (
    CAPABILITY_KNOWLEDGE_BASE_CONFIGURE,
    CAPABILITY_KNOWLEDGE_BASE_OPERATE,
    CAPABILITY_KNOWLEDGE_BASE_VIEW,
)
from tenants.models import TenantMembership

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .knowledge_base_views import _knowledge_base_context, _resolve_knowledge_base_access
from .operational_alert_services import get_operational_alert_detail
from .selectors import clean_querystring
from .work_queue_services import get_personal_work_queue, get_tenant_work_queue
from knowledge_base.rag.operational_work_queue_services import (
    claim_operational_alert,
    deescalate_operational_alert,
    escalate_operational_alert_manual,
    transfer_operational_alert,
    unassign_operational_alert_work_queue,
)


def _work_queue_context(access, *, sub_section: str, **extra):
    from tenants.access import get_active_membership

    membership = get_active_membership(access.user, access.tenant)
    context = _knowledge_base_context(access, sub_section=sub_section, **extra)
    context["current_membership"] = membership
    return context


@login_required(login_url="/admin/login/")
def operational_my_work(request):
    access = _resolve_knowledge_base_access(request)
    from tenants.access import get_active_membership

    membership = get_active_membership(access.user, access.tenant)
    page, personal_count = get_personal_work_queue(
        tenant=access.tenant,
        membership=membership,
        page_number=request.GET.get("page", 1),
    )
    return render(
        request,
        "operations_portal/work_queue/my_work.html",
        _work_queue_context(
            access,
            sub_section="my-work",
            page_obj=page,
            personal_count=personal_count,
            querystring=clean_querystring(request.GET),
        ),
    )


@login_required(login_url="/admin/login/")
def operational_work_queue(request):
    access = _resolve_knowledge_base_access(request)
    page, summary = get_tenant_work_queue(
        tenant=access.tenant,
        page_number=request.GET.get("page", 1),
        priority=request.GET.get("priority"),
        assigned_to=request.GET.get("assigned_to"),
        unassigned=request.GET.get("unassigned"),
        sla_breached=request.GET.get("sla_breached"),
        under_maintenance=request.GET.get("under_maintenance"),
        silenced=request.GET.get("silenced"),
        reopened=request.GET.get("reopened"),
        escalated=request.GET.get("escalated"),
        status=request.GET.get("status"),
        severity=request.GET.get("severity"),
        category=request.GET.get("category"),
    )
    memberships = TenantMembership.objects.filter(tenant=access.tenant, is_active=True).select_related("user")
    return render(
        request,
        "operations_portal/work_queue/tenant_queue.html",
        _work_queue_context(
            access,
            sub_section="work-queue",
            page_obj=page,
            summary=summary,
            memberships=memberships,
            querystring=clean_querystring(request.GET),
            filters={
                "priority": request.GET.get("priority", ""),
                "assigned_to": request.GET.get("assigned_to", ""),
                "unassigned": request.GET.get("unassigned", ""),
                "sla_breached": request.GET.get("sla_breached", ""),
                "under_maintenance": request.GET.get("under_maintenance", ""),
                "silenced": request.GET.get("silenced", ""),
                "reopened": request.GET.get("reopened", ""),
                "escalated": request.GET.get("escalated", ""),
                "status": request.GET.get("status", ""),
                "severity": request.GET.get("severity", ""),
                "category": request.GET.get("category", ""),
            },
        ),
    )


def _redirect_alert_detail(pk: int, tenant_pk: int):
    from django.urls import reverse

    return redirect(f"{reverse('operations_portal:knowledge_base_alert_detail', kwargs={'pk': pk})}?tenant={tenant_pk}")


@login_required(login_url="/admin/login/")
def operational_alert_claim(request, pk: int):
    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_OPERATE)
    if request.POST.get("tenant") and str(request.POST.get("tenant")) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        claim_operational_alert(tenant=access.tenant, alert_id=pk, actor=request.user, request=request)
    except WorkQueueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Alerta assumido.")
    return _redirect_alert_detail(pk, access.tenant.pk)


@login_required(login_url="/admin/login/")
def operational_alert_transfer(request, pk: int):
    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.POST.get("tenant") and str(request.POST.get("tenant")) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        transfer_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            membership_id=int(request.POST.get("membership_id", "0")),
            reason=request.POST.get("reason", ""),
            request=request,
        )
    except (WorkQueueError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Responsabilidade transferida.")
    return _redirect_alert_detail(pk, access.tenant.pk)


@login_required(login_url="/admin/login/")
def operational_alert_unassign(request, pk: int):
    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.POST.get("tenant") and str(request.POST.get("tenant")) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        unassign_operational_alert_work_queue(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            reason=request.POST.get("reason", ""),
            request=request,
        )
    except WorkQueueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Responsável removido.")
    return _redirect_alert_detail(pk, access.tenant.pk)


@login_required(login_url="/admin/login/")
def operational_alert_escalate(request, pk: int):
    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.POST.get("tenant") and str(request.POST.get("tenant")) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        escalate_operational_alert_manual(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            target_level=int(request.POST.get("target_level", "0")),
            reason=request.POST.get("reason", ""),
            request=request,
        )
    except (WorkQueueError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Alerta escalado.")
    return _redirect_alert_detail(pk, access.tenant.pk)


@login_required(login_url="/admin/login/")
def operational_alert_deescalate(request, pk: int):
    if request.method != "POST":
        raise PermissionDenied
    access = _resolve_knowledge_base_access(request, configure=True)
    require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    if request.POST.get("tenant") and str(request.POST.get("tenant")) != str(access.tenant.pk):
        raise PermissionDenied
    try:
        deescalate_operational_alert(
            tenant=access.tenant,
            alert_id=pk,
            actor=request.user,
            reason=request.POST.get("reason", ""),
            request=request,
        )
    except WorkQueueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Escalonamento encerrado.")
    return _redirect_alert_detail(pk, access.tenant.pk)
