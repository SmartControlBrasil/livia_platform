from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import OperationalError, ProgrammingError
from django.db.models import Count
from django.utils import timezone

from conversations.models import ChatRequest


class Command(BaseCommand):
    help = "Mostra relatório operacional readonly de ChatRequest sem PII. Limpeza é dry-run por padrão."

    def add_arguments(self, parser):
        parser.add_argument("--retention-days", type=int, default=30)
        parser.add_argument("--execute-cleanup", action="store_true")

    def handle(self, *args, **options):
        retention_days = max(int(options["retention_days"]), 1)
        cutoff = timezone.now() - timedelta(days=retention_days)
        cleanup_queryset = ChatRequest.objects.filter(
            status__in=[ChatRequest.Status.COMPLETED, ChatRequest.Status.FAILED],
            updated_at__lt=cutoff,
        )
        try:
            payload = {
                "schema_available": True,
                "totals": {
                    "chat_requests": ChatRequest.objects.count(),
                    "processing": ChatRequest.objects.filter(status=ChatRequest.Status.PROCESSING).count(),
                    "completed": ChatRequest.objects.filter(status=ChatRequest.Status.COMPLETED).count(),
                    "failed": ChatRequest.objects.filter(status=ChatRequest.Status.FAILED).count(),
                },
                "by_status": list(ChatRequest.objects.values("status").annotate(count=Count("id")).order_by("status")),
                "cleanup": {
                    "dry_run": not options["execute_cleanup"],
                    "retention_days": retention_days,
                    "eligible_chat_requests": cleanup_queryset.count(),
                },
            }
            if options["execute_cleanup"]:
                deleted_count, _ = cleanup_queryset.delete()
                payload["cleanup"]["deleted_chat_requests"] = deleted_count
        except (OperationalError, ProgrammingError) as exc:
            payload = {
                "schema_available": False,
                "error": exc.__class__.__name__,
                "message": "ChatRequest table is not available. Run migrations before using this report.",
                "cleanup": {"dry_run": True, "retention_days": retention_days, "eligible_chat_requests": 0},
            }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
