from __future__ import annotations

import csv
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from audit.models import ACTION_OPERATIONAL_ANALYTICS_EXPORTED
from audit.services import record_audit_event
from knowledge_base.rag.operational_analytics import export_analytics_csv_rows
from tenants.access import CAPABILITY_KNOWLEDGE_BASE_CONFIGURE, CAPABILITY_KNOWLEDGE_BASE_VIEW

from .access import portal_template_context, require_portal_capability, resolve_portal_access
from .knowledge_base_views import _knowledge_base_context, _resolve_knowledge_base_access
from .operational_analytics_services import build_operational_analytics_dashboard
from .selectors import clean_querystring


@login_required(login_url="/admin/login/")
def operational_analytics(request):
    access = _resolve_knowledge_base_access(request)
    include_capacity = require_portal_capability(access, CAPABILITY_KNOWLEDGE_BASE_CONFIGURE)
    dashboard = build_operational_analytics_dashboard(
        tenant=access.tenant,
        period=request.GET.get("period", "7d"),
        request=request,
        include_capacity_detail=include_capacity,
    )
    context = _knowledge_base_context(access, sub_section="analytics", dashboard=dashboard, querystring=clean_querystring(request.GET))
    context["can_export"] = include_capacity
    context["can_view_capacity_detail"] = include_capacity
    return render(request, "operations_portal/analytics/dashboard.html", context)


@login_required(login_url="/admin/login/")
def operational_analytics_export(request):
    access = resolve_portal_access(
        request,
        capability=CAPABILITY_KNOWLEDGE_BASE_CONFIGURE,
        allow_global=False,
        require_tenant=True,
    )
    period = request.GET.get("period", "7d")
    rows = export_analytics_csv_rows(tenant=access.tenant, period=period)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    record_audit_event(
        action=ACTION_OPERATIONAL_ANALYTICS_EXPORTED,
        actor=request.user,
        tenant=access.tenant,
        object_type="OperationalAnalytics",
        object_id=access.tenant.slug,
        object_repr=f"analytics export {period}",
        metadata={"period": period, "row_count": len(rows)},
        request=request,
    )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="operational-analytics-{access.tenant.slug}-{period}.csv"'
    messages.success(request, "Exportação CSV gerada.")
    return response
