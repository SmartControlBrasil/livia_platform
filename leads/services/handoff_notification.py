from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandoffNotificationResult:
    success: bool
    dry_run: bool
    channel: str
    message: str


class HandoffNotificationService:
    channel = "email"

    def notify(self, handoff) -> HandoffNotificationResult:
        enabled = bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED", False))
        dry_run = bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True))
        recipient = str(getattr(settings, "LIVIA_HANDOFF_NOTIFICATION_EMAIL", "contato@smartcontrolbrasil.com.br") or "").strip()

        if dry_run or not enabled:
            message = f"Dry-run handoff notification prepared for {recipient or 'no-recipient'}."
            self._log("handoff_notification_dry_run", handoff, message=message)
            return HandoffNotificationResult(success=True, dry_run=True, channel=self.channel, message=message)

        if not recipient:
            message = "Handoff notification recipient is not configured."
            self._log("handoff_notification_missing_recipient", handoff, message=message)
            return HandoffNotificationResult(success=False, dry_run=False, channel=self.channel, message=message)

        message = "Real handoff notification transport is not implemented in this phase."
        self._log("handoff_notification_not_implemented", handoff, message=message)
        return HandoffNotificationResult(success=False, dry_run=False, channel=self.channel, message=message)

    def _log(self, event: str, handoff, *, message: str) -> None:
        logger.info(
            "event=%s handoff_id=%s tenant_slug=%s status=%s priority=%s dry_run_message=%s",
            event,
            getattr(handoff, "id", ""),
            getattr(getattr(handoff, "tenant", None), "slug", ""),
            getattr(handoff, "status", ""),
            getattr(handoff, "priority", ""),
            message,
        )
