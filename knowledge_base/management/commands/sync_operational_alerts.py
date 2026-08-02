from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.rag.operational_alert_sync import sync_operational_alerts
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Sincroniza alertas operacionais persistentes a partir dos diagnósticos RAG/IA."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Slug do tenant alvo.")
        parser.add_argument("--all-tenants", action="store_true", help="Processa todos os tenants ativos.")
        parser.add_argument("--period", default="7d", help="Período de métricas (24h, 7d, 30d).")
        parser.add_argument("--dry-run", action="store_true", help="Simula sem persistir alterações.")
        parser.add_argument("--json", action="store_true", help="Saída JSON.")

    def handle(self, *args, **options):
        tenant_slug = options.get("tenant")
        all_tenants = bool(options.get("all_tenants"))
        dry_run = bool(options.get("dry_run"))
        period = options.get("period") or "7d"
        as_json = bool(options.get("json"))

        if not tenant_slug and not all_tenants:
            raise CommandError("Informe --tenant <slug> ou --all-tenants (opcionalmente com --dry-run).")

        tenants = Tenant.objects.filter(is_active=True).order_by("slug")
        if tenant_slug:
            tenants = tenants.filter(slug=tenant_slug)
        if not tenants.exists():
            raise CommandError("Nenhum tenant encontrado para os filtros informados.")

        results = []
        for tenant in tenants:
            result = sync_operational_alerts(
                tenant=tenant,
                period=period,
                source="management.sync_operational_alerts",
                dry_run=dry_run,
            )
            results.append(result)

        if as_json:
            payload = [
                {
                    "tenant": item.tenant_slug,
                    "created": item.created,
                    "updated": item.updated,
                    "reopened": item.reopened,
                    "auto_resolved": item.auto_resolved,
                    "active": item.active,
                    "dry_run": item.dry_run,
                }
                for item in results
            ]
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        for item in results:
            prefix = "[dry-run] " if item.dry_run else ""
            self.stdout.write(
                f"{prefix}{item.tenant_slug}: "
                f"created={item.created} updated={item.updated} "
                f"reopened={item.reopened} auto_resolved={item.auto_resolved} active={item.active}"
            )
