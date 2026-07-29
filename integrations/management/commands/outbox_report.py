from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import OperationalError, ProgrammingError
from django.db.models import Count
from django.utils import timezone

from integrations.models import OutboxEvent


class Command(BaseCommand):
    help = "Relatório readonly da outbox transacional."

    def handle(self, *args, **options):
        now = timezone.now()
        lock_timeout = max(int(getattr(settings, "LIVIA_OUTBOX_LOCK_TIMEOUT_SECONDS", 60) or 60), 1)
        try:
            payload = {
                "schema_available": True,
                "totals": {
                    "outbox_events": OutboxEvent.objects.count(),
                    "pending_due": OutboxEvent.objects.filter(status=OutboxEvent.Status.PENDING, available_at__lte=now).count(),
                    "retry_due": OutboxEvent.objects.filter(status=OutboxEvent.Status.RETRY, available_at__lte=now).count(),
                    "processing_abandoned": OutboxEvent.objects.filter(status=OutboxEvent.Status.PROCESSING, locked_at__lt=now - timedelta(seconds=lock_timeout)).count(),
                    "dead_letter": OutboxEvent.objects.filter(status=OutboxEvent.Status.DEAD_LETTER).count(),
                },
                "by_status": list(OutboxEvent.objects.values("status").annotate(count=Count("id")).order_by("status")),
                "by_event_type": list(OutboxEvent.objects.values("event_type", "status").annotate(count=Count("id")).order_by("event_type", "status")),
            }
        except (OperationalError, ProgrammingError) as exc:
            payload = {"schema_available": False, "error": exc.__class__.__name__, "message": "OutboxEvent table is not available. Run migrations before using this report."}
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
