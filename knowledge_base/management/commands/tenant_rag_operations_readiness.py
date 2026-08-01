from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.rag.operations_readiness import inspect_rag_operations_readiness, readiness_has_blocking_errors
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Validação operacional de prontidão das operações RAG (somente leitura)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="", help="Slug opcional para checks específicos do tenant.")
        parser.add_argument("--json", action="store_true", help="Emitir JSON sanitizado.")

    def handle(self, *args, **options):
        tenant_slug = str(options.get("tenant") or "").strip()
        tenant = None
        if tenant_slug:
            tenant = Tenant.objects.filter(slug=tenant_slug).first()
            if tenant is None:
                raise CommandError("Tenant not found.")

        checks = inspect_rag_operations_readiness(tenant=tenant)
        payload = {
            "tenant": tenant.slug if tenant else None,
            "blocking_errors": readiness_has_blocking_errors(checks),
            "checks": [
                {
                    "code": check.code,
                    "ok": check.ok,
                    "severity": check.severity,
                    "detail": check.detail,
                }
                for check in checks
            ],
        }

        if options.get("json"):
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            tenant_label = payload["tenant"] or "global"
            self.stdout.write(f"tenant={tenant_label}")
            for check in checks:
                marker = "OK" if check.ok else check.severity.upper()
                self.stdout.write(f"[{marker}] {check.code}: {check.detail}")

        if payload["blocking_errors"]:
            raise CommandError("Operations readiness has blocking errors.")
