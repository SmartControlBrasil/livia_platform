from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from knowledge_base.rag.operational_monitoring import prune_operational_monitoring_runs


class Command(BaseCommand):
    help = "Remove execuções antigas de monitoramento operacional conforme política de retenção."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Dias de retenção (default: settings).")
        parser.add_argument("--json", action="store_true", help="Saída JSON.")

    def handle(self, *args, **options):
        result = prune_operational_monitoring_runs(days=options.get("days"))
        if options.get("json"):
            self.stdout.write(json.dumps(result, indent=2))
            return
        self.stdout.write(
            "Pruned monitoring runs: "
            f"batches={result['batches_deleted']} tenant_runs={result['tenant_runs_deleted']} "
            f"retention_days={result['retention_days']}"
        )
