from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Avg, Count
from django.utils import timezone

from assistant_core.services.ai_feature_gates import (
    is_grounded_synthesis_allowed,
    is_rag_semantic_context_active,
)
from knowledge_base.models import RagRetrievalEvent, TenantRagConfiguration
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.readiness import inspect_rag_vector_readiness
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

        profile = AssistantProfile.objects.filter(tenant=tenant, is_active=True).first()
        configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()

        payload = self._build_payload(
            tenant=tenant,
            profile=profile,
            configuration=configuration,
            days=days,
        )

        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return

        self._write_human(payload)

    def _build_payload(self, *, tenant, profile, configuration, days: int) -> dict:
        since = timezone.now() - timedelta(days=days)
        events = RagRetrievalEvent.objects.filter(tenant=tenant, created_at__gte=since)
        executed_qs = events.exclude(status=RagRetrievalEvent.Status.SKIPPED)
        executed = executed_qs.count()

        embedding_profile = None
        embedding_error = ""
        try:
            cfg = load_embedding_config()
            embedding_profile = {
                "provider": cfg.provider,
                "model": cfg.model,
                "dimension": cfg.dimension,
                "signature": cfg.signature[:16] + "…",
            }
        except EmbeddingConfigurationError as exc:
            embedding_error = str(exc)

        try:
            backend_name = get_vector_search_backend().name
        except Exception as exc:  # noqa: BLE001
            backend_name = f"unavailable:{exc.__class__.__name__}"

        readiness = [
            {"ok": check.ok, "code": check.code, "detail": check.detail}
            for check in inspect_rag_vector_readiness()
        ]

        aggregates = executed_qs.aggregate(
            avg_latency=Avg("duration_ms"),
            avg_max_score=Avg("max_score"),
        )

        status_counts = {
            row["status"]: row["total"]
            for row in executed_qs.values("status").annotate(total=Count("id"))
        }

        try:
            from assistant_core.models import AiUsageEvent

            has_usage = AiUsageEvent.objects.exists()
        except Exception:  # noqa: BLE001
            has_usage = False
        return {
            "tenant": tenant.slug,
            "window_days": days,
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
            "tenant_gates": {
                "rag_semantic_active": is_rag_semantic_context_active(tenant_slug=tenant.slug),
                "grounded_synthesis_allowed": bool(
                    profile
                    and is_grounded_synthesis_allowed(tenant_slug=tenant.slug, assistant_profile=profile)
                ),
            },
            "embedding_profile": embedding_profile,
            "embedding_error": embedding_error,
            "vector_backend": backend_name,
            "retrieval_enabled": bool(configuration and configuration.retrieval_enabled),
            "retrieval_metrics": {
                "executed": executed,
                "hits": executed_qs.filter(hit=True).count(),
                "empty": executed_qs.filter(status=RagRetrievalEvent.Status.EMPTY).count(),
                "failed": executed_qs.filter(status=RagRetrievalEvent.Status.FAILED).count(),
                "skipped": events.filter(status=RagRetrievalEvent.Status.SKIPPED).count(),
                "dry_run_events": executed_qs.filter(dry_run=True).count(),
                "active_events": executed_qs.filter(dry_run=False).count(),
                "status_breakdown": status_counts,
                "avg_latency_ms": round(float(aggregates["avg_latency"] or 0), 1),
                "avg_max_score": round(float(aggregates["avg_max_score"] or 0), 3),
            },
            "readiness": readiness,
            "observability_notes": {
                "evidence_status_persisted": False,
                "openai_token_usage_persisted": has_usage,
                "portal_rag_visibility": False,
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
        self.stdout.write(f"  dry_run: {metrics['dry_run_events']} | active: {metrics['active_events']}")
        self.stdout.write(f"  avg latency: {metrics['avg_latency_ms']} ms")
        self.stdout.write(f"  avg max score: {metrics['avg_max_score']}")
        self.stdout.write("")
        self.stdout.write("Readiness:")
        for check in payload["readiness"]:
            mark = "OK" if check["ok"] else "FAIL"
            self.stdout.write(f"  [{mark}] {check['code']}: {check['detail']}")
