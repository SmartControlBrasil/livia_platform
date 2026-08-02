from __future__ import annotations

from django.db.models import Count
from django.utils import timezone

from knowledge_base.models import TenantOperationalNotification, TenantOperationalNotificationWorkerRun


def build_notification_metrics(*, tenant=None) -> dict:
    qs = TenantOperationalNotification.objects.all()
    if tenant is not None:
        qs = qs.filter(tenant=tenant)

    by_status = {
        row["status"]: row["total"]
        for row in qs.values("status").annotate(total=Count("id"))
    }
    in_app = qs.filter(channel=TenantOperationalNotification.Channel.IN_APP)
    email = qs.filter(channel=TenantOperationalNotification.Channel.EMAIL)

    last_run = TenantOperationalNotificationWorkerRun.objects.order_by("-started_at").first()

    pending_old = qs.filter(
        status=TenantOperationalNotification.Status.PENDING,
        scheduled_at__lt=timezone.now() - timezone.timedelta(hours=24),
    ).count()

    processing_stale = qs.filter(
        status=TenantOperationalNotification.Status.PROCESSING,
        locked_at__lt=timezone.now() - timezone.timedelta(minutes=30),
    ).count()

    return {
        "pending": by_status.get(TenantOperationalNotification.Status.PENDING, 0),
        "processing": by_status.get(TenantOperationalNotification.Status.PROCESSING, 0),
        "sent": by_status.get(TenantOperationalNotification.Status.SENT, 0),
        "delivered": by_status.get(TenantOperationalNotification.Status.DELIVERED, 0),
        "read": by_status.get(TenantOperationalNotification.Status.READ, 0),
        "failed": by_status.get(TenantOperationalNotification.Status.FAILED, 0),
        "suppressed": by_status.get(TenantOperationalNotification.Status.SUPPRESSED, 0),
        "cancelled": by_status.get(TenantOperationalNotification.Status.CANCELLED, 0),
        "in_app_unread": in_app.filter(
            read_at__isnull=True,
            status__in=[
                TenantOperationalNotification.Status.SENT,
                TenantOperationalNotification.Status.DELIVERED,
            ],
        ).count(),
        "email_pending": email.filter(status=TenantOperationalNotification.Status.PENDING).count(),
        "email_failed": email.filter(status=TenantOperationalNotification.Status.FAILED).count(),
        "pending_old": pending_old,
        "processing_stale": processing_stale,
        "last_worker_run": last_run,
    }
