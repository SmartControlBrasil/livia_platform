from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.template.loader import render_to_string

from knowledge_base.models import TenantOperationalNotification

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    dry_run: bool = False
    retryable: bool = False
    error_category: str = ""
    message: str = ""


def deliver_notification(notification: TenantOperationalNotification) -> DeliveryResult:
    if notification.channel == TenantOperationalNotification.Channel.IN_APP:
        return DeliveryResult(success=True, dry_run=False, message="In-app notification persisted.")

    if notification.channel == TenantOperationalNotification.Channel.EMAIL:
        return _deliver_email(notification)

    if notification.channel == TenantOperationalNotification.Channel.WEBHOOK:
        return _deliver_webhook_dry_run(notification)

    return DeliveryResult(
        success=False,
        retryable=False,
        error_category="configuration_error",
        message="Unknown notification channel.",
    )


def _deliver_email(notification: TenantOperationalNotification) -> DeliveryResult:
    enabled = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED", False))
    dry_run = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN", True))

    if not enabled or dry_run:
        body = render_to_string(
            "knowledge_base/emails/operational_notification.txt",
            {
                "notification": notification,
                "tenant_name": notification.tenant.name,
            },
        ).strip()
        recipient = notification.recipient_membership.user.email or "no-recipient"
        logger.info(
            "operational_email_dry_run notification_id=%s tenant=%s recipient=%s bytes=%s",
            notification.pk,
            notification.tenant.slug,
            recipient,
            len(body),
        )
        return DeliveryResult(success=True, dry_run=True, message=f"Dry-run email prepared for {recipient}.")

    return DeliveryResult(
        success=False,
        retryable=False,
        error_category="configuration_error",
        message="Real operational email transport is forbidden in this phase.",
    )


def _deliver_webhook_dry_run(notification: TenantOperationalNotification) -> DeliveryResult:
    logger.info(
        "operational_webhook_dry_run notification_id=%s tenant=%s event=%s",
        notification.pk,
        notification.tenant.slug,
        notification.event_type,
    )
    return DeliveryResult(success=True, dry_run=True, message="Webhook dry-run only.")
