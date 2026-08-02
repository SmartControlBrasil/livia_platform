from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from knowledge_base.rag.operational_analytics import build_operational_analytics
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Relatório de analytics operacional tenant-scoped."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", dest="tenant_slug", required=True)
        parser.add_argument("--period", default="7d")
        parser.add_argument("--as-json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant_slug"]).strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise SystemExit(f"Tenant '{tenant_slug}' não encontrado.")
        payload = build_operational_analytics(tenant=tenant, period=options.get("period"))
        if options.get("as_json"):
            serializable = _serialize(payload)
            self.stdout.write(json.dumps(serializable, ensure_ascii=False, default=str))
            return
        self.stdout.write(
            f"tenant={tenant.slug} period={payload['period']} backlog={payload['backlog']['total_open']} "
            f"p1={payload['backlog']['by_priority'].get('P1', 0)} ack_sla={payload['ack_sla'].get('compliance_percent')}"
        )


def _serialize(payload: dict) -> dict:
    data = dict(payload)
    data.pop("start_at", None)
    data.pop("end_at", None)
    return data
