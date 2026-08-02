from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.rag.operations import process_pending_operation_requests, recover_stale_operation_requests
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Processa solicitações operacionais RAG pendentes (control plane worker)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="", help="Slug opcional para limitar a um tenant.")
        parser.add_argument("--limit", type=int, default=5, help="Máximo de solicitações por execução.")
        parser.add_argument(
            "--recover-stale-only",
            action="store_true",
            help="Apenas recupera execuções expiradas sem processar pendentes.",
        )

    def handle(self, *args, **options):
        tenant_slug = str(options.get("tenant") or "").strip()
        tenant = None
        if tenant_slug:
            tenant = Tenant.objects.filter(slug=tenant_slug).first()
            if tenant is None:
                raise CommandError("Tenant not found.")

        recovered = recover_stale_operation_requests(tenant=tenant)
        self.stdout.write(f"recovered_stale={recovered}")
        if options.get("recover_stale_only"):
            return

        limit = int(options.get("limit") or 0)
        if limit <= 0:
            raise CommandError("--limit must be a positive integer.")
        processed = process_pending_operation_requests(tenant=tenant, limit=limit)
        self.stdout.write(f"processed={len(processed)} ids={','.join(str(item) for item in processed)}")
