from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Avg, Count
from django.utils import timezone

from knowledge_base.models import RagRetrievalEvent, TenantRagConfiguration
from knowledge_base.rag.readiness import inspect_rag_vector_readiness
from knowledge_base.rag.vector_search import get_vector_search_backend
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Relatorio operacional de retrieval RAG por tenant (sem perguntas/documentos)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--days", type=int, default=7, help="Janela em dias (default 7).")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        days = int(options["days"] or 0)
        if days <= 0:
            raise CommandError("--days must be a positive integer.")

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        try:
            backend = get_vector_search_backend()
            backend_name = backend.name
        except Exception as exc:  # noqa: BLE001
            backend_name = f"unavailable:{exc.__class__.__name__}"

        since = timezone.now() - timedelta(days=days)
        events = RagRetrievalEvent.objects.filter(tenant=tenant, created_at__gte=since)

        # Hit-rate: hits / (completed + empty + failed). Skips nao entram no denominador.
        executed_qs = events.exclude(status=RagRetrievalEvent.Status.SKIPPED)
        executed = executed_qs.count()
        hits = executed_qs.filter(hit=True).count()
        empty = executed_qs.filter(status=RagRetrievalEvent.Status.EMPTY).count()
        failed = executed_qs.filter(status=RagRetrievalEvent.Status.FAILED).count()
        skipped = events.filter(status=RagRetrievalEvent.Status.SKIPPED).count()
        hit_rate = (hits / executed * 100.0) if executed else 0.0
        empty_rate = (empty / executed * 100.0) if executed else 0.0
        failure_rate = (failed / executed * 100.0) if executed else 0.0
        dry_run_observe = executed_qs.filter(reason="dry_run_observe").count()
        dry_run_events = executed_qs.filter(dry_run=True).count()
        active_events = executed_qs.filter(dry_run=False).count()
        active_hits = executed_qs.filter(dry_run=False, hit=True).count()
        active_empty = executed_qs.filter(dry_run=False, status=RagRetrievalEvent.Status.EMPTY).count()
        active_failed = executed_qs.filter(dry_run=False, status=RagRetrievalEvent.Status.FAILED).count()
        dry_hits = executed_qs.filter(dry_run=True, hit=True).count()
        dry_empty = executed_qs.filter(dry_run=True, status=RagRetrievalEvent.Status.EMPTY).count()
        dry_failed = executed_qs.filter(dry_run=True, status=RagRetrievalEvent.Status.FAILED).count()
        threshold_source_counts = list(
            executed_qs.values("threshold_source").annotate(total=Count("id")).order_by("threshold_source")
        )

        aggregates = executed_qs.aggregate(
            avg_latency=Avg("duration_ms"),
            avg_results=Avg("result_count"),
            avg_max_score=Avg("max_score"),
        )

        self.stdout.write(f"Tenant: {tenant.slug}")
        self.stdout.write(f"Backend: {backend_name}")
        self.stdout.write(
            "Retrieval enabled: "
            + ("yes" if configuration and configuration.retrieval_enabled else "no")
        )
        self.stdout.write("")
        self.stdout.write(f"Últimos {days} dias:")
        self.stdout.write(f"executed: {executed}")
        self.stdout.write(f"hits: {hits}")
        self.stdout.write(f"empty: {empty}")
        self.stdout.write(f"failed: {failed}")
        self.stdout.write(f"skipped: {skipped}")
        self.stdout.write(f"hit rate: {hit_rate:.1f}%")
        self.stdout.write(f"empty rate: {empty_rate:.1f}%")
        self.stdout.write(f"failure rate: {failure_rate:.1f}%")
        self.stdout.write(f"dry_run_observe: {dry_run_observe}")
        self.stdout.write("mode split:")
        self.stdout.write(f"  dry_run: executed={dry_run_events} hits={dry_hits} empty={dry_empty} failed={dry_failed}")
        self.stdout.write(f"  active: executed={active_events} hits={active_hits} empty={active_empty} failed={active_failed}")
        if threshold_source_counts:
            breakdown = ", ".join(
                f"{row['threshold_source'] or 'unknown'}={row['total']}" for row in threshold_source_counts
            )
            self.stdout.write(f"threshold sources: {breakdown}")
        self.stdout.write(f"avg latency: {float(aggregates['avg_latency'] or 0):.0f} ms")
        self.stdout.write(f"avg results: {float(aggregates['avg_results'] or 0):.1f}")
        self.stdout.write(f"avg max score: {float(aggregates['avg_max_score'] or 0):.2f}")
        self.stdout.write("")
        self.stdout.write("Readiness snapshot:")
        for check in inspect_rag_vector_readiness():
            mark = "OK" if check.ok else "FAIL"
            self.stdout.write(f"[{mark}] {check.code}: {check.detail}")
