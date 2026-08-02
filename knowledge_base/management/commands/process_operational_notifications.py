from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from knowledge_base.rag.operational_notification_processor import process_operational_notifications_batch
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Processa notificações operacionais pendentes (one-shot)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--channel", default="")
        parser.add_argument("--tenant", dest="tenant_slug", default="")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        tenant_slug = str(options.get("tenant_slug") or "").strip()
        if tenant_slug and not Tenant.objects.filter(slug=tenant_slug).exists():
            raise SystemExit(f"Tenant '{tenant_slug}' não encontrado.")

        summary = process_operational_notifications_batch(
            limit=options.get("limit"),
            channel=str(options.get("channel") or "").strip(),
            tenant_slug=tenant_slug,
            dry_run=bool(options.get("dry_run")),
        )
        payload = summary.as_dict()
        if options.get("json"):
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
            return
        self.stdout.write(
            f"claimed={payload['claimed']} delivered={payload['delivered']} "
            f"failed={payload['failed']} cancelled={payload['cancelled']} "
            f"retry_scheduled={payload['retry_scheduled']}"
        )
