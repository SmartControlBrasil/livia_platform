from __future__ import annotations

import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_NOTIFICATION_FAILED,
    ACTION_OPERATIONAL_NOTIFICATION_RETRY_SCHEDULED,
    ACTION_OPERATIONAL_NOTIFICATION_SENT,
)
from audit.services import record_audit_event
from knowledge_base.models import TenantOperationalNotification, TenantOperationalNotificationWorkerRun
from knowledge_base.rag.operational_notification_delivery import deliver_notification

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessNotificationsSummary:
    claimed: int = 0
    delivered: int = 0
    failed: int = 0
    cancelled: int = 0
    retry_scheduled: int = 0

    def as_dict(self):
        return self.__dict__.copy()


def build_worker_id(worker_id: str = "") -> str:
    return str(worker_id or f"{socket.gethostname()}-{uuid.uuid4()}")[:120]


def process_operational_notifications_batch(
    *,
    limit: int | None = None,
    worker_id: str = "",
    channel: str = "",
    tenant_slug: str = "",
    dry_run: bool = False,
) -> ProcessNotificationsSummary:
    worker_id = build_worker_id(worker_id)
    batch_size = _batch_size(limit)
    recover_stale_processing()

    run = TenantOperationalNotificationWorkerRun.objects.create(
        worker_identifier=worker_id,
        dry_run=dry_run,
        channel_filter=channel or "",
        tenant_slug=tenant_slug or "",
        started_at=timezone.now(),
        status=TenantOperationalNotificationWorkerRun.Status.RUNNING,
    )

    notifications = claim_operational_notifications(
        batch_size=batch_size,
        worker_id=worker_id,
        channel=channel,
        tenant_slug=tenant_slug,
        dry_run=dry_run,
    )
    summary = ProcessNotificationsSummary(claimed=len(notifications))

    for notification in notifications:
        if dry_run:
            continue
        result = deliver_notification(notification)
        status = finalize_notification_delivery(notification, worker_id=worker_id, result=result)
        if status == TenantOperationalNotification.Status.DELIVERED:
            summary = ProcessNotificationsSummary(
                claimed=summary.claimed,
                delivered=summary.delivered + 1,
                failed=summary.failed,
                cancelled=summary.cancelled,
                retry_scheduled=summary.retry_scheduled,
            )
        elif status == TenantOperationalNotification.Status.FAILED:
            summary = ProcessNotificationsSummary(
                claimed=summary.claimed,
                delivered=summary.delivered,
                failed=summary.failed + 1,
                cancelled=summary.cancelled,
                retry_scheduled=summary.retry_scheduled,
            )
        elif status == TenantOperationalNotification.Status.CANCELLED:
            summary = ProcessNotificationsSummary(
                claimed=summary.claimed,
                delivered=summary.delivered,
                failed=summary.failed,
                cancelled=summary.cancelled + 1,
                retry_scheduled=summary.retry_scheduled,
            )
        elif status == TenantOperationalNotification.Status.PENDING:
            summary = ProcessNotificationsSummary(
                claimed=summary.claimed,
                delivered=summary.delivered,
                failed=summary.failed,
                cancelled=summary.cancelled,
                retry_scheduled=summary.retry_scheduled + 1,
            )

    run.claimed = summary.claimed
    run.delivered = summary.delivered
    run.failed = summary.failed
    run.cancelled = summary.cancelled
    run.retry_scheduled = summary.retry_scheduled
    run.finished_at = timezone.now()
    run.status = (
        TenantOperationalNotificationWorkerRun.Status.SUCCEEDED
        if summary.failed == 0
        else TenantOperationalNotificationWorkerRun.Status.PARTIAL
    )
    run.save()
    return summary


def claim_operational_notifications(
    *,
    batch_size: int,
    worker_id: str,
    channel: str = "",
    tenant_slug: str = "",
    dry_run: bool = False,
) -> list[TenantOperationalNotification]:
    now = timezone.now()
    with transaction.atomic():
        queryset = TenantOperationalNotification.objects.filter(
            status=TenantOperationalNotification.Status.PENDING,
            scheduled_at__lte=now,
        ).select_related("tenant", "recipient_membership", "recipient_membership__user").order_by("scheduled_at", "created_at")
        if channel:
            queryset = queryset.filter(channel=channel)
        if tenant_slug:
            queryset = queryset.filter(tenant__slug=tenant_slug)
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
        items = list(queryset[:batch_size])
        ids = [item.pk for item in items]
        if not ids or dry_run:
            return items if dry_run else []
        TenantOperationalNotification.objects.filter(pk__in=ids).update(
            status=TenantOperationalNotification.Status.PROCESSING,
            processing_token=worker_id,
            locked_at=now,
        )
    return list(TenantOperationalNotification.objects.filter(pk__in=ids).select_related("tenant", "recipient_membership", "recipient_membership__user"))


def finalize_notification_delivery(
    notification: TenantOperationalNotification,
    *,
    worker_id: str,
    result,
) -> str:
    with transaction.atomic():
        current = TenantOperationalNotification.objects.select_for_update().get(pk=notification.pk)
        if current.processing_token != worker_id or current.status != TenantOperationalNotification.Status.PROCESSING:
            return current.status

        now = timezone.now()
        current.processing_token = ""
        current.locked_at = None
        current.attempt_count += 1

        if not current.recipient_membership.is_active or not current.recipient_membership.user.is_active:
            current.status = TenantOperationalNotification.Status.CANCELLED
            current.cancellation_reason = "inactive_membership"
            current.save(update_fields=["status", "cancellation_reason", "processing_token", "locked_at", "updated_at"])
            return current.status

        if result.success:
            if current.channel == TenantOperationalNotification.Channel.IN_APP:
                current.status = TenantOperationalNotification.Status.DELIVERED
            else:
                current.status = TenantOperationalNotification.Status.SENT
            current.sent_at = now
            current.last_error_category = ""
            current.last_error_summary = ""
            current.save(
                update_fields=[
                    "status",
                    "sent_at",
                    "attempt_count",
                    "last_error_category",
                    "last_error_summary",
                    "processing_token",
                    "locked_at",
                    "updated_at",
                ]
            )
            record_audit_event(
                action=ACTION_OPERATIONAL_NOTIFICATION_SENT,
                actor=None,
                tenant=current.tenant,
                object_type="TenantOperationalNotification",
                object_id=str(current.pk),
                object_repr=current.event_type,
                metadata={"channel": current.channel, "dry_run": result.dry_run},
            )
            return current.status

        current.last_error_category = result.error_category[:40]
        current.last_error_summary = result.message[:500]
        current.failed_at = now

        if not result.retryable or current.attempt_count >= current.max_attempts:
            current.status = TenantOperationalNotification.Status.FAILED
            current.save(
                update_fields=[
                    "status",
                    "failed_at",
                    "attempt_count",
                    "last_error_category",
                    "last_error_summary",
                    "processing_token",
                    "locked_at",
                    "updated_at",
                ]
            )
            record_audit_event(
                action=ACTION_OPERATIONAL_NOTIFICATION_FAILED,
                actor=None,
                tenant=current.tenant,
                object_type="TenantOperationalNotification",
                object_id=str(current.pk),
                object_repr=current.event_type,
                metadata={"channel": current.channel, "error_category": result.error_category},
            )
            return current.status

        backoff = calculate_backoff_seconds(current.attempt_count)
        current.status = TenantOperationalNotification.Status.PENDING
        current.next_attempt_at = now + timedelta(seconds=backoff)
        current.scheduled_at = current.next_attempt_at
        current.save(
            update_fields=[
                "status",
                "failed_at",
                "attempt_count",
                "last_error_category",
                "last_error_summary",
                "next_attempt_at",
                "scheduled_at",
                "processing_token",
                "locked_at",
                "updated_at",
            ]
        )
        record_audit_event(
            action=ACTION_OPERATIONAL_NOTIFICATION_RETRY_SCHEDULED,
            actor=None,
            tenant=current.tenant,
            object_type="TenantOperationalNotification",
            object_id=str(current.pk),
            object_repr=current.event_type,
            metadata={"next_attempt_at": current.next_attempt_at.isoformat(), "attempt": current.attempt_count},
        )
        return current.status


def recover_stale_processing() -> int:
    timeout = int(getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_LOCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = timezone.now() - timedelta(seconds=max(timeout, 1))
    updated = TenantOperationalNotification.objects.filter(
        status=TenantOperationalNotification.Status.PROCESSING,
        locked_at__lt=cutoff,
    ).update(
        status=TenantOperationalNotification.Status.PENDING,
        processing_token="",
        locked_at=None,
        last_error_category="stale_processing_recovered",
        last_error_summary="Processing lock expired.",
    )
    if updated:
        logger.info("operational_notification_stale_recovered count=%s", updated)
    return updated


def calculate_backoff_seconds(attempts: int) -> int:
    base = max(int(getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_BASE_RETRY_SECONDS", 30) or 30), 1)
    cap = max(int(getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_MAX_RETRY_SECONDS", 3600) or 3600), base)
    return min(base * (2 ** max(attempts - 1, 0)), cap)


def _batch_size(value) -> int:
    batch_size = int(value or getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_BATCH_SIZE", 50) or 50)
    if batch_size < 1 or batch_size > 500:
        raise ValueError("limit must be between 1 and 500.")
    return batch_size
