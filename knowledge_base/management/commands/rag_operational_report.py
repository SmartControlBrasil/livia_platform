from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from knowledge_base.rag.operational_metrics import build_rag_operational_report_payload
from knowledge_base.rag.vector_search import get_vector_search_backend
from tenants.models import AssistantProfile, Tenant


class Command(BaseCommand):
    help = "Relatório operacional consolidado RAG + gates + readiness por tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--days", type=int, default=7, help="Janela de métricas de retrieval (dias).")
        parser.add_argument("--json", action="store_true", help="Emitir JSON consolidado.")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        days = int(options["days"] or 0)
        if days <= 0:
            raise CommandError("--days must be a positive integer.")

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        payload = self._build_payload(
            tenant=tenant,
            days=days,
        )

        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return

        self._write_human(payload)

    def _build_payload(self, *, tenant, days: int) -> dict:
        base = build_rag_operational_report_payload(tenant=tenant, days=days)
        try:
            backend_name = get_vector_search_backend().name
        except Exception as exc:  # noqa: BLE001
            backend_name = f"unavailable:{exc.__class__.__name__}"

        try:
            from assistant_core.models import AiUsageEvent

            has_usage = AiUsageEvent.objects.exists()
        except Exception:  # noqa: BLE001
            has_usage = False

        return {
            **base,
            "environment": {
                "LIVIA_ENVIRONMENT": getattr(settings, "LIVIA_ENVIRONMENT", ""),
                "DEBUG": settings.DEBUG,
                "RUNNING_TESTS": getattr(settings, "RUNNING_TESTS", False),
            },
            "feature_flags": {
                "LIVIA_RAG_ENABLED": settings.LIVIA_RAG_ENABLED,
                "LIVIA_RAG_DRY_RUN": settings.LIVIA_RAG_DRY_RUN,
                "LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST": settings.LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST,
                "LIVIA_AI_ENABLED": settings.LIVIA_AI_ENABLED,
                "LIVIA_AI_DRY_RUN": settings.LIVIA_AI_DRY_RUN,
                "LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED": settings.LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED,
                "LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST": settings.LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST,
                "SMART360_LEAD_DISPATCH_ENABLED": settings.SMART360_LEAD_DISPATCH_ENABLED,
                "SMART360_LEAD_DISPATCH_DRY_RUN": settings.SMART360_LEAD_DISPATCH_DRY_RUN,
                "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED": settings.LIVIA_HANDOFF_NOTIFICATIONS_ENABLED,
                "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN": settings.LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN,
            },
            "vector_backend": backend_name,
            "observability_notes": {
                "evidence_status_persisted": False,
                "openai_token_usage_persisted": has_usage,
                "portal_rag_visibility": True,
            },
        }

    def _write_human(self, payload: dict) -> None:
        self.stdout.write(f"Tenant: {payload['tenant']}")
        self.stdout.write(f"Window: last {payload['window_days']} days")
        self.stdout.write("")
        self.stdout.write("Environment:")
        for key, value in payload["environment"].items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("")
        self.stdout.write("Feature flags:")
        for key, value in payload["feature_flags"].items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("")
        self.stdout.write("Tenant gates:")
        for key, value in payload["tenant_gates"].items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write("")
        if payload["embedding_error"]:
            self.stdout.write(f"Embedding profile ERROR: {payload['embedding_error']}")
        elif payload["embedding_profile"]:
            prof = payload["embedding_profile"]
            self.stdout.write(
                f"Embedding profile: {prof['provider']} / {prof['model']} / dim={prof['dimension']}"
            )
        self.stdout.write(f"Vector backend: {payload['vector_backend']}")
        self.stdout.write(f"Retrieval enabled: {payload['retrieval_enabled']}")
        self.stdout.write("")
        metrics = payload["retrieval_metrics"]
        self.stdout.write("Retrieval metrics:")
        self.stdout.write(f"  executed: {metrics['executed']}")
        self.stdout.write(f"  hits: {metrics['hits']}")
        self.stdout.write(f"  empty: {metrics['empty']}")
        self.stdout.write(f"  failed: {metrics['failed']}")
        self.stdout.write(f"  skipped: {metrics['skipped']}")
        self.stdout.write(f"  dry_run: {metrics.get('dry_run_events', 0)} | active: {metrics.get('active_events', 0)}")
        self.stdout.write(f"  avg latency: {metrics['avg_latency_ms']} ms")
        self.stdout.write(f"  avg max score: {metrics['avg_max_score']}")
        self.stdout.write("")
        self.stdout.write("Readiness:")
        for check in payload["readiness"]:
            mark = "OK" if check["ok"] else "FAIL"
            self.stdout.write(f"  [{mark}] {check['code']}: {check['detail']}")
