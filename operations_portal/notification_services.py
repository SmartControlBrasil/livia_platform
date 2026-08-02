from __future__ import annotations

from django.core.paginator import Paginator
from django.urls import reverse

from knowledge_base.models import TenantOperationalNotification


def build_notification_destination_url(*, notification: TenantOperationalNotification) -> str:
    route = notification.destination_route
    object_id = notification.destination_object_id
    if route == TenantOperationalNotification.DestinationRoute.ALERT_DETAIL and object_id:
        return reverse("operations_portal:knowledge_base_alert_detail", kwargs={"pk": object_id})
    if route == TenantOperationalNotification.DestinationRoute.WORK_QUEUE:
        return reverse("operations_portal:operational_work_queue")
    if route == TenantOperationalNotification.DestinationRoute.MY_WORK:
        return reverse("operations_portal:operational_my_work")
    if route == TenantOperationalNotification.DestinationRoute.MAINTENANCE:
        return reverse("operations_portal:knowledge_base_maintenance")
    if route == TenantOperationalNotification.DestinationRoute.HEALTH:
        return reverse("operations_portal:knowledge_base_health")
    return reverse("operations_portal:operational_notifications")


def get_notification_list(
    *,
    tenant,
    membership,
    page_number=1,
    filter_key: str = "all",
    per_page: int = 20,
):
    qs = TenantOperationalNotification.objects.filter(
        tenant=tenant,
        recipient_membership=membership,
        channel=TenantOperationalNotification.Channel.IN_APP,
    ).exclude(status=TenantOperationalNotification.Status.CANCELLED)

    if filter_key == "unread":
        qs = qs.filter(
            read_at__isnull=True,
            status__in=[
                TenantOperationalNotification.Status.SENT,
                TenantOperationalNotification.Status.DELIVERED,
            ],
        )
    elif filter_key == "critical":
        qs = qs.filter(severity=TenantOperationalNotification.Severity.CRITICAL)
    elif filter_key == "sla":
        qs = qs.filter(category=TenantOperationalNotification.Category.SLA)
    elif filter_key == "escalation":
        qs = qs.filter(category=TenantOperationalNotification.Category.ESCALATION)
    elif filter_key == "assigned":
        qs = qs.filter(event_type__in=["alert_assigned", "alert_transferred"])
    elif filter_key == "resolution":
        qs = qs.filter(event_type="alert_resolved")

    paginator = Paginator(qs.order_by("-created_at"), per_page)
    page = paginator.get_page(page_number)
    for item in page.object_list:
        item.destination_url = build_notification_destination_url(notification=item)
    return page


def enrich_portal_notification_context(context, *, access):
    from knowledge_base.rag.operational_notification_services import count_unread_notifications
    from tenants.access import get_active_membership

    if access.tenant is None or access.is_global:
        context["unread_notification_count"] = 0
        return context
    membership = get_active_membership(access.user, access.tenant)
    if membership is None:
        context["unread_notification_count"] = 0
        return context
    context["unread_notification_count"] = count_unread_notifications(tenant=access.tenant, membership=membership)
    return context
