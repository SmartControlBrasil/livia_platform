from __future__ import annotations

import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from integrations.models import OutboxEvent

from .handlers import PermanentOutboxError, HandlerResult
from .registry import get_handler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessOutboxSummary:
    claimed: int = 0
    succeeded: int = 0
    skipped: int = 0
    retry: int = 0
    dead_letter: int = 0
    permanent_failure: int = 0

    def as_dict(self):
        return self.__dict__.copy()


def build_worker_id(worker_id: str = "") -> str:
    return str(worker_id or f"{socket.gethostname()}-{uuid.uuid4()}")[:120]


def process_outbox_batch(*, batch_size: int | None = None, worker_id: str = "", event_type: str = "", tenant_slug: str = "") -> ProcessOutboxSummary:
    batch_size = _batch_size(batch_size)
    worker_id = build_worker_id(worker_id)
    recover_abandoned_locks()
    events = claim_outbox_events(batch_size=batch_size, worker_id=worker_id, event_type=event_type, tenant_slug=tenant_slug)
    summary = {"claimed": len(events), "succeeded": 0, "skipped": 0, "retry": 0, "dead_letter": 0, "permanent_failure": 0}
    for event in events:
        started = timezone.now()
        try:
            handler = get_handler(event.event_type)
            result = handler.handle(event)
        except KeyError as exc:
            result = HandlerResult("permanent_failure", code="unknown_event_type", message=str(exc))
        except PermanentOutboxError as exc:
            result = HandlerResult("permanent_failure", code=exc.code, message=exc.message)
        except Exception as exc:
            result = HandlerResult("retryable_failure", code=exc.__class__.__name__, message="Unexpected handler error.", retryable=True)
        status = finalize_outbox_event(event, worker_id=worker_id, result=result)
        duration_ms = int((timezone.now() - started).total_seconds() * 1000)
        _log_delivery(status, event, worker_id=worker_id, duration_ms=duration_ms, code=result.code)
        if status == OutboxEvent.Status.SUCCEEDED:
            summary["succeeded"] += 1
        elif status == OutboxEvent.Status.SKIPPED:
            summary["skipped"] += 1
        elif status == OutboxEvent.Status.RETRY:
            summary["retry"] += 1
        elif status == OutboxEvent.Status.DEAD_LETTER:
            summary["dead_letter"] += 1
            if result.status == "permanent_failure":
                summary["permanent_failure"] += 1
    return ProcessOutboxSummary(**summary)


def claim_outbox_events(*, batch_size: int, worker_id: str, event_type: str = "", tenant_slug: str = "") -> list[OutboxEvent]:
    now = timezone.now()
    with transaction.atomic():
        queryset = OutboxEvent.objects.filter(
            status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.RETRY],
            available_at__lte=now,
        ).select_related("tenant").order_by("available_at", "created_at")
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        if tenant_slug:
            queryset = queryset.filter(tenant__slug=tenant_slug)
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
        events = list(queryset[:batch_size])
        ids = [event.pk for event in events]
        if not ids:
            return []
        OutboxEvent.objects.filter(pk__in=ids).update(
            status=OutboxEvent.Status.PROCESSING,
            locked_at=now,
            locked_by=worker_id,
            last_attempt_at=now,
        )
    claimed = list(OutboxEvent.objects.filter(pk__in=ids).select_related("tenant"))
    for event in claimed:
        logger.info("outbox_delivery_started event_id=%s event_type=%s tenant_slug=%s attempt=%s worker_id=%s", event.event_id, event.event_type, event.tenant.slug, event.attempts + 1, worker_id)
    return claimed


def finalize_outbox_event(event: OutboxEvent, *, worker_id: str, result: HandlerResult) -> str:
    with transaction.atomic():
        current = OutboxEvent.objects.select_for_update().get(pk=event.pk)
        if current.locked_by != worker_id or current.status != OutboxEvent.Status.PROCESSING:
            logger.warning("outbox_finish_rejected event_id=%s worker_id=%s locked_by=%s status=%s", current.event_id, worker_id, current.locked_by, current.status)
            return current.status
        current.attempts += 1
        current.last_error_code = str(result.code or result.status)[:120]
        current.last_error_message = str(result.message or "")[:500]
        current.result_metadata = _safe_metadata(result.metadata)
        current.locked_at = None
        current.locked_by = ""
        now = timezone.now()
        if result.status == "succeeded":
            current.status = OutboxEvent.Status.SUCCEEDED
            current.processed_at = now
        elif result.status == "skipped":
            current.status = OutboxEvent.Status.SKIPPED
            current.processed_at = now
        elif result.status == "permanent_failure":
            current.status = OutboxEvent.Status.DEAD_LETTER
            current.processed_at = now
        else:
            if current.attempts >= current.max_attempts:
                current.status = OutboxEvent.Status.DEAD_LETTER
                current.processed_at = now
            else:
                current.status = OutboxEvent.Status.RETRY
                current.available_at = now + timedelta(seconds=calculate_backoff_seconds(current.attempts))
        current.save(update_fields=["attempts", "last_error_code", "last_error_message", "result_metadata", "locked_at", "locked_by", "status", "processed_at", "available_at", "updated_at"])
        return current.status


def recover_abandoned_locks() -> int:
    timeout = int(getattr(settings, "LIVIA_OUTBOX_LOCK_TIMEOUT_SECONDS", 60) or 60)
    cutoff = timezone.now() - timedelta(seconds=max(timeout, 1))
    updated = OutboxEvent.objects.filter(status=OutboxEvent.Status.PROCESSING, locked_at__lt=cutoff).update(
        status=OutboxEvent.Status.RETRY,
        locked_at=None,
        locked_by="",
        available_at=timezone.now(),
        last_error_code="abandoned_lock_recovered",
        last_error_message="Processing lock expired and was returned to retry.",
    )
    if updated:
        logger.info("outbox_abandoned_lock_recovered count=%s", updated)
    return updated


def calculate_backoff_seconds(attempts: int) -> int:
    base = max(int(getattr(settings, "LIVIA_OUTBOX_BASE_RETRY_SECONDS", 30) or 30), 1)
    cap = max(int(getattr(settings, "LIVIA_OUTBOX_MAX_RETRY_SECONDS", 3600) or 3600), base)
    return min(base * (2 ** max(attempts - 1, 0)), cap)


def _batch_size(value) -> int:
    batch_size = int(value or getattr(settings, "LIVIA_OUTBOX_BATCH_SIZE", 20) or 20)
    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch-size must be between 1 and 500.")
    return batch_size


def _safe_metadata(metadata):
    safe = {}
    for key, value in dict(metadata or {}).items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in ("token", "secret", "authorization")):
            continue
        safe[key] = value
    return safe


def _log_delivery(status, event, *, worker_id: str, duration_ms: int, code: str):
    name = {
        OutboxEvent.Status.SUCCEEDED: "outbox_delivery_succeeded",
        OutboxEvent.Status.SKIPPED: "outbox_delivery_skipped",
        OutboxEvent.Status.RETRY: "outbox_delivery_retry_scheduled",
        OutboxEvent.Status.DEAD_LETTER: "outbox_delivery_dead_letter",
    }.get(status, "outbox_delivery_finished")
    logger.info("%s event_id=%s event_type=%s tenant_slug=%s attempt=%s worker_id=%s duration_ms=%s code=%s", name, event.event_id, event.event_type, event.tenant.slug, event.attempts + 1, worker_id, duration_ms, code)
