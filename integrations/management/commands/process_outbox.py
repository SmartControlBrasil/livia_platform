from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, ProgrammingError

from integrations.models import OutboxEvent
from integrations.outbox.processor import process_outbox_batch
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Processa um lote da outbox. Sem --execute, apenas mostra relatório dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--batch-size", type=int, default=None)
        parser.add_argument("--worker-id", default="")
        parser.add_argument("--event-type", default="")
        parser.add_argument("--tenant", default="")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size is not None and (batch_size < 1 or batch_size > 500):
            raise CommandError("--batch-size deve estar entre 1 e 500.")
        event_type = str(options["event_type"] or "").strip()
        if event_type and event_type not in OutboxEvent.EventType.values:
            raise CommandError("event_type inválido.")
        tenant_slug = str(options["tenant"] or "").strip()
        if tenant_slug and not Tenant.objects.filter(slug=tenant_slug).exists():
            raise CommandError("tenant inválido.")
        if not options["execute"]:
            queryset = OutboxEvent.objects.filter(status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.RETRY])
            if event_type:
                queryset = queryset.filter(event_type=event_type)
            if tenant_slug:
                queryset = queryset.filter(tenant__slug=tenant_slug)
            try:
                eligible = queryset.count()
            except (OperationalError, ProgrammingError) as exc:
                self.stdout.write(json.dumps({"dry_run": True, "schema_available": False, "error": exc.__class__.__name__, "claimed": 0, "succeeded": 0, "skipped": 0, "retry": 0, "dead_letter": 0}, sort_keys=True))
                return
            self.stdout.write(json.dumps({"dry_run": True, "schema_available": True, "eligible": eligible, "claimed": 0, "succeeded": 0, "skipped": 0, "retry": 0, "dead_letter": 0}, sort_keys=True))
            return
        summary = process_outbox_batch(batch_size=batch_size, worker_id=options["worker_id"], event_type=event_type, tenant_slug=tenant_slug)
        self.stdout.write(json.dumps({"dry_run": False, **summary.as_dict()}, sort_keys=True))
