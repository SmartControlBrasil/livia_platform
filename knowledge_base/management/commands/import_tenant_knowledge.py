from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from knowledge_base.models import KnowledgeDocument
from knowledge_base.services.importing import (
    SUPPORTED_EXTENSIONS,
    TenantKnowledgeImportError,
    import_tenant_knowledge_path,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Importa arquivos texto/Markdown como KnowledgeDocument tenant-aware."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant dono dos documentos.")
        parser.add_argument("--source", required=True, help="Arquivo ou diretório .txt/.md/.markdown.")
        parser.add_argument("--source-type", default="import", help="source_type salvo no KnowledgeDocument.")
        parser.add_argument("--tag", action="append", dest="tags", default=[], help="Tag adicional; pode ser repetida.")
        parser.add_argument("--status", choices=[choice[0] for choice in KnowledgeDocument.Status.choices], default=KnowledgeDocument.Status.ACTIVE)
        parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria importado sem gravar.")
        parser.add_argument("--replace", action="store_true", help="Atualiza documentos existentes com mesmo tenant+slug.")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"] or "").strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        try:
            result = import_tenant_knowledge_path(
                tenant=tenant,
                source=Path(options["source"]).expanduser(),
                source_type=options.get("source_type") or "import",
                tags=options.get("tags") or [],
                status=options.get("status") or KnowledgeDocument.Status.ACTIVE,
                replace=bool(options.get("replace")),
                dry_run=bool(options.get("dry_run")),
            )
        except TenantKnowledgeImportError as exc:
            raise CommandError(str(exc)) from exc

        if result.dry_run:
            self.stdout.write("DRY RUN - nenhuma alteração gravada")
            for item in result.planned:
                self.stdout.write(f"- {item.slug} :: {item.title} ({len(item.content)} chars)")
            return

        self.stdout.write(self.style.SUCCESS("Tenant knowledge import completed."))
        self.stdout.write(f"tenant={tenant.slug}")
        self.stdout.write(f"created={result.created} updated={result.updated} skipped={result.skipped}")
        self.stdout.write("formats=" + ",".join(sorted(SUPPORTED_EXTENSIONS)))
        self.stdout.write("Next: execute the existing tenant RAG chunking/indexing commands when semantic retrieval is required.")
