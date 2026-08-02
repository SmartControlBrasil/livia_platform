from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.rag.operational_monitoring import (
    monitoring_gate_status,
    process_operational_monitoring,
    recover_stale_monitoring_batches,
)


class Command(BaseCommand):
    help = "Executa monitoramento operacional tenant-scoped (snapshot + sync de alertas)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Slug do tenant alvo.")
        parser.add_argument("--all-eligible", action="store_true", help="Processa tenants elegíveis.")
        parser.add_argument("--limit", type=int, help="Limite de tenants por execução.")
        parser.add_argument("--period", default="7d", help="Período de métricas (24h, 7d, 30d).")
        parser.add_argument("--dry-run", action="store_true", help="Força dry-run desta execução.")
        parser.add_argument("--no-dry-run", action="store_true", help="Força execução real desta execução.")
        parser.add_argument("--json", action="store_true", help="Saída JSON.")
        parser.add_argument("--fail-fast", action="store_true", help="Interrompe no primeiro tenant com falha.")
        parser.add_argument(
            "--recover-stale-only",
            action="store_true",
            help="Apenas recupera execuções stale.",
        )

    def handle(self, *args, **options):
        if options.get("recover_stale_only"):
            recovered = recover_stale_monitoring_batches()
            self.stdout.write(f"Recovered stale monitoring batches: {recovered}")
            return

        tenant_slug = options.get("tenant")
        all_eligible = bool(options.get("all_eligible"))
        if not tenant_slug and not all_eligible:
            gate = monitoring_gate_status()
            raise CommandError(
                "Informe --tenant <slug> ou --all-eligible. "
                f"Gate enabled={gate.enabled} dry_run={gate.dry_run}."
            )

        dry_run = None
        if options.get("dry_run"):
            dry_run = True
        if options.get("no_dry_run"):
            dry_run = False

        result = process_operational_monitoring(
            tenant_slug=tenant_slug,
            all_eligible=all_eligible,
            limit=options.get("limit"),
            period=options.get("period") or "7d",
            trigger="cli",
            dry_run=dry_run,
            fail_fast=bool(options.get("fail_fast")),
        )

        if options.get("json"):
            payload = {
                "batch_id": result.batch_id,
                "status": result.status,
                "dry_run": result.dry_run,
                "tenants_processed": result.tenants_processed,
                "tenants_failed": result.tenants_failed,
                "tenants_skipped": result.tenants_skipped,
                "alerts_created": result.alerts_created,
                "alerts_updated": result.alerts_updated,
                "alerts_resolved": result.alerts_resolved,
                "alerts_reopened": result.alerts_reopened,
                "duration_ms": result.duration_ms,
                "error_summary": result.error_summary,
            }
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        self.stdout.write(
            f"status={result.status} batch_id={result.batch_id} dry_run={result.dry_run} "
            f"processed={result.tenants_processed} failed={result.tenants_failed} "
            f"created={result.alerts_created} updated={result.alerts_updated} "
            f"resolved={result.alerts_resolved} duration_ms={result.duration_ms}"
        )
        if result.error_summary:
            self.stderr.write(result.error_summary)
