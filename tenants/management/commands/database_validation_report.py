from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from tenants.services.database_validation import build_database_validation_report


class Command(BaseCommand):
    help = "Gera relatório readonly de comparação de dados sem PII para cutover SQLite/PostgreSQL."

    def handle(self, *args, **options):
        report = build_database_validation_report()
        payload = {
            "totals": report.totals,
            "by_tenant": report.by_tenant,
            "tenant_integrity": report.tenant_integrity,
            "kpis": report.kpis,
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
