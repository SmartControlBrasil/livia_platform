from __future__ import annotations

import math

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.embedding_profile import embedding_coverage_breakdown, inspect_tenant_embedding_health
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Saúde operacional dos embeddings RAG por tenant (sem exibir vetores ou documentos)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        health = inspect_tenant_embedding_health(tenant=tenant)
        coverage = embedding_coverage_breakdown(tenant=tenant, profile=health.profile)
        configuration = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        default_threshold = float(getattr(settings, "LIVIA_RAG_MIN_SIMILARITY_SCORE", 0.25) or 0.0)
        if not math.isfinite(default_threshold):
            default_threshold = 0.25
        tenant_threshold = getattr(configuration, "min_similarity_score", None)
        if tenant_threshold is not None:
            effective_threshold = float(tenant_threshold)
            threshold_source = "tenant"
        else:
            effective_threshold = float(default_threshold)
            threshold_source = "global_default"

        self.stdout.write(f"Tenant: {health.tenant_slug}")
        self.stdout.write("")
        self.stdout.write("Profile:")
        self.stdout.write(f"  provider: {health.profile.provider}")
        self.stdout.write(f"  model: {health.profile.model}")
        self.stdout.write(f"  dimension: {health.profile.dimension}")
        self.stdout.write(f"  profile_key: {health.profile.profile_key}")
        if health.profile.schema_vector_dimension is not None:
            self.stdout.write(f"  schema_vector_dimension: {health.profile.schema_vector_dimension}")
        self.stdout.write(f"  threshold_default: {default_threshold:.2f}")
        self.stdout.write(f"  threshold_tenant: {tenant_threshold if tenant_threshold is not None else 'none'}")
        self.stdout.write(f"  threshold_effective: {effective_threshold:.2f} ({threshold_source})")
        self.stdout.write("")
        self.stdout.write("Embeddings:")
        self.stdout.write(f"  total: {health.total}")
        self.stdout.write(f"  compatible: {health.compatible}")
        self.stdout.write(f"  null: {health.null_vectors}")
        self.stdout.write(f"  wrong model: {health.wrong_model}")
        self.stdout.write(f"  wrong dimension: {health.wrong_dimension}")
        self.stdout.write(f"  wrong signature: {health.wrong_signature}")
        self.stdout.write(f"  inactive: {health.inactive}")
        self.stdout.write(f"  stale (signature): {health.stale}")
        self.stdout.write(f"  reindex_required: {health.reindex_required}")
        self.stdout.write(f"  invalid: {health.invalid}")
        self.stdout.write(f"  indexable_chunks: coverage={coverage['coverage'] * 100:.1f}%")
        self.stdout.write(f"  coverage_total: {coverage['indexable_chunks']}")
        self.stdout.write(f"  coverage_compatible: {coverage['compatible']}")
        self.stdout.write(f"  coverage_missing_embedding: {coverage['missing_embedding']}")
        self.stdout.write(f"  coverage_incompatible_embedding: {coverage['incompatible_embedding']}")
        self.stdout.write(f"  coverage_stale: {coverage['stale']}")
        self.stdout.write("")
        self.stdout.write(f"Status: {health.status_label}")
