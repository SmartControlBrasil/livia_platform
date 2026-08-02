from __future__ import annotations

from knowledge_base.rag.operational_analytics import AnalyticsFilters, build_operational_analytics
from tenants.models import TenantMembership


def parse_analytics_filters(request) -> AnalyticsFilters:
    return AnalyticsFilters(
        priority=str(request.GET.get("priority") or "").strip(),
        severity=str(request.GET.get("severity") or "").strip(),
        category=str(request.GET.get("category") or "").strip(),
        assigned_to=str(request.GET.get("assigned_to") or "").strip(),
        status=str(request.GET.get("status") or "").strip(),
    )


def build_operational_analytics_dashboard(*, tenant, period: str | None, request=None, include_capacity_detail: bool = False) -> dict:
    filters = parse_analytics_filters(request) if request is not None else AnalyticsFilters()
    payload = build_operational_analytics(tenant=tenant, period=period, filters=filters)
    if not include_capacity_detail:
        payload = dict(payload)
        payload["ownership"] = {
            **payload["ownership"],
            "assignees": [
                row
                for row in payload["ownership"]["assignees"]
                if row.get("membership_id") or row.get("total", 0) > 0
            ][:10],
        }
    payload["filter_memberships"] = list(
        TenantMembership.objects.filter(tenant=tenant, is_active=True).select_related("user").order_by("user__username")
    )
    payload["querystring_base"] = _build_querystring(request, exclude=("page",))
    return payload


def _build_querystring(request, exclude=()) -> str:
    if request is None:
        return ""
    parts = []
    for key, value in request.GET.items():
        if key in exclude or not value:
            continue
        parts.append(f"{key}={value}")
    return "&".join(parts)
