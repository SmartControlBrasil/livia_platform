from django.core.management.base import BaseCommand, CommandError

from knowledge_base.services.lifecycle import KnowledgeLifecycleService
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Reindexa documentos de conhecimento de um tenant pelo lifecycle central."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--document-id", type=int, default=None, help="ID opcional de um KnowledgeDocument.")
        parser.add_argument("--dry-run", action="store_true", help="Calcula sem persistir alterações.")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"] or "").strip()).first()
        if tenant is None:
            raise CommandError("Tenant not found.")
        service = KnowledgeLifecycleService()
        document_id = options.get("document_id")
        try:
            if document_id:
                results = [
                    service.reindex_document(
                        tenant=tenant,
                        document_id=document_id,
                        dry_run=bool(options.get("dry_run")),
                        source="management.reindex_tenant_knowledge",
                    )
                ]
            else:
                results = service.reindex_tenant(
                    tenant=tenant,
                    dry_run=bool(options.get("dry_run")),
                    source="management.reindex_tenant_knowledge",
                )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        if options.get("dry_run"):
            self.stdout.write("DRY RUN - nenhuma alteração gravada")
        self.stdout.write(f"tenant={tenant.slug}")
        for result in results:
            document = result.document
            self.stdout.write(
                "document={doc} status={status} chunks_created={chunks_created} chunks_rebuilt={chunks_rebuilt} "
                "chunks_unchanged={chunks_unchanged} embeddings_indexed={embeddings_indexed} "
                "embeddings_reindexed={embeddings_reindexed} embeddings_unchanged={embeddings_unchanged} failed={failed}".format(
                    doc=getattr(document, "slug", document_id or "-"),
                    status=result.status,
                    chunks_created=result.chunks_created,
                    chunks_rebuilt=result.chunks_rebuilt,
                    chunks_unchanged=result.chunks_unchanged,
                    embeddings_indexed=result.embeddings_indexed,
                    embeddings_reindexed=result.embeddings_reindexed,
                    embeddings_unchanged=result.embeddings_unchanged,
                    failed=result.failed,
                )
            )
            if result.error:
                self.stdout.write(f"error={result.error}")
