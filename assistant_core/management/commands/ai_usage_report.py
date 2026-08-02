from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from assistant_core.models import AiUsageEvent
from knowledge_base.rag.operational_metrics import build_ai_usage_summary
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Relatório de uso OpenAI por tenant (tokens/latência, sem prompts)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        days = int(options["days"] or 0)
        if days <= 0:
            raise CommandError("--days must be a positive integer.")

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        since = timezone.now() - timedelta(days=days)
        qs = AiUsageEvent.objects.filter(tenant=tenant, created_at__gte=since)
        if not qs.exists():
            payload = build_ai_usage_summary(tenant=tenant, period="7d" if days >= 7 else "24h")
            payload["tenant"] = tenant.slug
            payload["window_days"] = days
            payload["total_events"] = 0
            payload["note"] = "No AiUsageEvent rows in window (run chat/soak after migration)."
            self._emit(payload, json_mode=options["json"])
            return

        period = "30d" if days >= 30 else "7d" if days >= 7 else "24h"
        payload = build_ai_usage_summary(tenant=tenant, period=period)
        payload["tenant"] = tenant.slug
        payload["window_days"] = days
        self._emit(payload, json_mode=options["json"])

    def _emit(self, payload: dict, *, json_mode: bool) -> None:
        if json_mode:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return
        self.stdout.write(f"Tenant: {payload['tenant']}")
        self.stdout.write(f"Window: last {payload.get('window_days', '?')} days")
        if payload.get("total_events") == 0 or payload.get("has_data") is False:
            self.stdout.write(payload.get("note", "no data"))
            return
        self.stdout.write(f"requests: {payload['requests']}")
        self.stdout.write(f"success: {payload['success']} | failure: {payload['failure']}")
        self.stdout.write(
            f"tokens: prompt={payload['prompt_tokens']} completion={payload['completion_tokens']} total={payload['total_tokens']}"
        )
        self.stdout.write(
            f"latency ms: avg={payload['avg_latency_ms']} median={payload['median_latency_ms']} p95={payload['p95_latency_ms']}"
        )
        for row in payload.get("by_operation", []):
            self.stdout.write(
                f"  - {row['operation']}: req={row['requests']} tokens={row['total_tokens']} "
                f"median={row['median_latency_ms']}ms p95={row['p95_latency_ms']}ms"
            )
