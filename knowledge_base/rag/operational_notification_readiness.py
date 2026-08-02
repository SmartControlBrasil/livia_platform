from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from knowledge_base.rag.operational_notification_metrics import build_notification_metrics


@dataclass(frozen=True)
class NotificationReadinessCheck:
    ok: bool
    code: str
    detail: str
    level: str = "info"


def inspect_operational_notification_readiness(*, tenant=None) -> list[NotificationReadinessCheck]:
    checks: list[NotificationReadinessCheck] = []
    metrics = build_notification_metrics(tenant=tenant)

    email_enabled = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED", False))
    email_dry_run = bool(getattr(settings, "LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN", True))
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()

    checks.append(
        NotificationReadinessCheck(
            ok=True,
            code="notification_worker_configured",
            detail=f"batch_size={getattr(settings, 'LIVIA_OPERATIONAL_NOTIFICATION_BATCH_SIZE', 50)}",
        )
    )

    if email_enabled and not email_dry_run:
        checks.append(
            NotificationReadinessCheck(
                ok=False,
                code="email_real_dispatch",
                detail="Real operational email is forbidden in this phase.",
                level="critical",
            )
        )
    else:
        checks.append(
            NotificationReadinessCheck(
                ok=True,
                code="email_gate",
                detail=f"enabled={email_enabled} dry_run={email_dry_run}",
            )
        )

    if env == "staging" and email_enabled and not email_dry_run:
        checks.append(
            NotificationReadinessCheck(
                ok=False,
                code="staging_email_safety",
                detail="Staging must keep LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN=True",
                level="critical",
            )
        )

    if metrics["pending_old"] > 0:
        checks.append(
            NotificationReadinessCheck(
                ok=False,
                code="pending_stale",
                detail=f"{metrics['pending_old']} pending notifications older than 24h",
                level="warning",
            )
        )

    if metrics["processing_stale"] > 0:
        checks.append(
            NotificationReadinessCheck(
                ok=False,
                code="processing_stale",
                detail=f"{metrics['processing_stale']} notifications stuck in processing",
                level="warning",
            )
        )

    if metrics["failed"] > 10:
        checks.append(
            NotificationReadinessCheck(
                ok=False,
                code="failures_accumulated",
                detail=f"{metrics['failed']} failed delivery records",
                level="warning",
            )
        )

    last_run = metrics.get("last_worker_run")
    if last_run is None:
        checks.append(
            NotificationReadinessCheck(
                ok=True,
                code="worker_never_run",
                detail="Worker has not executed yet.",
                level="info",
            )
        )

    return checks
