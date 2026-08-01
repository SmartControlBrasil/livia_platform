from __future__ import annotations

import json
import statistics
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from assistant_core.models import AiUsageEvent
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
            payload = {
                "tenant": tenant.slug,
                "window_days": days,
                "total_events": 0,
                "note": "No AiUsageEvent rows in window (run chat/soak after migration).",
            }
            self._emit(payload, json_mode=options["json"])
            return

        latencies = [int(value or 0) for value in qs.values_list("latency_ms", flat=True)]
        totals = qs.aggregate(
            requests=Count("id"),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            total_tokens=Sum("total_tokens"),
            avg_latency=Avg("latency_ms"),
        )
        success_count = qs.filter(success=True).count()
        failure_count = qs.filter(success=False).count()

        by_operation = []
        operations = sorted(set(qs.values_list("operation", flat=True)))
        for operation in operations:
            op_qs = qs.filter(operation=operation)
            op_latencies = [int(value or 0) for value in op_qs.values_list("latency_ms", flat=True)]
            op_totals = op_qs.aggregate(
                requests=Count("id"),
                prompt_tokens=Sum("prompt_tokens"),
                completion_tokens=Sum("completion_tokens"),
                total_tokens=Sum("total_tokens"),
            )
            by_operation.append(
                {
                    "operation": operation,
                    "requests": op_totals["requests"],
                    "success": op_qs.filter(success=True).count(),
                    "failure": op_qs.filter(success=False).count(),
                    "prompt_tokens": int(op_totals["prompt_tokens"] or 0),
                    "completion_tokens": int(op_totals["completion_tokens"] or 0),
                    "total_tokens": int(op_totals["total_tokens"] or 0),
                    "median_latency_ms": int(statistics.median(op_latencies)) if op_latencies else 0,
                    "p95_latency_ms": _percentile(op_latencies, 95),
                }
            )

        payload = {
            "tenant": tenant.slug,
            "window_days": days,
            "requests": totals["requests"],
            "success": success_count,
            "failure": failure_count,
            "prompt_tokens": int(totals["prompt_tokens"] or 0),
            "completion_tokens": int(totals["completion_tokens"] or 0),
            "total_tokens": int(totals["total_tokens"] or 0),
            "avg_latency_ms": round(float(totals["avg_latency"] or 0), 1),
            "median_latency_ms": int(statistics.median(latencies)) if latencies else 0,
            "p95_latency_ms": _percentile(latencies, 95),
            "by_operation": sorted(by_operation, key=lambda item: item["operation"]),
            "estimated_cost_usd": None,
        }
        self._emit(payload, json_mode=options["json"])

    def _emit(self, payload: dict, *, json_mode: bool) -> None:
        if json_mode:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return
        self.stdout.write(f"Tenant: {payload['tenant']}")
        self.stdout.write(f"Window: last {payload.get('window_days', '?')} days")
        if payload.get("total_events") == 0:
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


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]
