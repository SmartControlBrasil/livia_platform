from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration
from knowledge_base.services.importing import import_tenant_knowledge_path
from knowledge_base.services.lifecycle import KnowledgeLifecycleService
from tenants.models import AssistantProfile, Tenant

DEFAULT_FIXTURE_RELATIVE = Path("knowledge_fixtures") / "smart-control-brasil"
TENANT_FOLDER_ID = "19_1rnVSslm6yse6Kas79ul3Q35uYUEVe"
COMMERCIAL_EMAIL = "comercial@smartcontrolbrasil.com.br"


class Command(BaseCommand):
    help = (
        "Configura Smart Control Brasil: e-mail comercial, pasta Drive curada, "
        "importa knowledge fixtures e prepara RAG (manual + Drive)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="smart-control-brasil")
        parser.add_argument(
            "--fixture-dir",
            default="",
            help="Diretório com .md/.txt curados. Default: knowledge_fixtures/smart-control-brasil",
        )
        parser.add_argument("--skip-import", action="store_true")
        parser.add_argument("--skip-reindex", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--enable-drive",
            action="store_true",
            help="Aponta source_mode=google_drive para a pasta curada do tenant.",
        )
        parser.add_argument(
            "--drive-folder-id",
            default=TENANT_FOLDER_ID,
            help="Folder ID autoritativo do tenant no Drive.",
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"]).strip()).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        fixture_dir = Path(options["fixture_dir"]).expanduser() if options["fixture_dir"] else Path(settings.BASE_DIR) / DEFAULT_FIXTURE_RELATIVE
        if not fixture_dir.exists():
            raise CommandError(f"Fixture dir not found: {fixture_dir}")

        profile, _ = AssistantProfile.objects.get_or_create(tenant=tenant)
        if not options["dry_run"]:
            if profile.notification_email != COMMERCIAL_EMAIL:
                profile.notification_email = COMMERCIAL_EMAIL
                profile.save(update_fields=["notification_email", "updated_at"])
            self.stdout.write(f"notification_email={profile.notification_email}")
        else:
            self.stdout.write(f"DRY RUN notification_email -> {COMMERCIAL_EMAIL}")

        if options["enable_drive"]:
            folder_id = str(options["drive_folder_id"] or "").strip()
            cfg, created = TenantRagConfiguration.objects.get_or_create(tenant=tenant)
            if options["dry_run"]:
                self.stdout.write(
                    f"DRY RUN drive config folder={folder_id} created={created} "
                    f"current_mode={cfg.source_mode}"
                )
            else:
                cfg.source_mode = TenantRagConfiguration.SOURCE_GOOGLE_DRIVE
                cfg.approved_folder_id = folder_id
                cfg.sync_enabled = True
                cfg.retrieval_enabled = True
                cfg.save()
                self.stdout.write(
                    f"drive_config mode={cfg.source_mode} folder={cfg.approved_folder_id} "
                    f"sync={cfg.sync_enabled} retrieval={cfg.retrieval_enabled}"
                )
        else:
            # Mantém/ativa retrieval via knowledge manual mesmo sem Drive.
            cfg, _ = TenantRagConfiguration.objects.get_or_create(
                tenant=tenant,
                defaults={
                    "source_mode": TenantRagConfiguration.SOURCE_MANUAL,
                    "sync_enabled": False,
                    "retrieval_enabled": True,
                },
            )
            if not options["dry_run"] and not cfg.retrieval_enabled:
                cfg.retrieval_enabled = True
                cfg.save(update_fields=["retrieval_enabled", "updated_at"])
            self.stdout.write(f"rag_config mode={cfg.source_mode} retrieval={cfg.retrieval_enabled}")

        if not options["skip_import"]:
            result = import_tenant_knowledge_path(
                tenant=tenant,
                source=fixture_dir,
                source_type="curated_fixture",
                tags=["smart-control", "curated"],
                status=KnowledgeDocument.Status.ACTIVE,
                replace=True,
                dry_run=bool(options["dry_run"]),
            )
            if result.dry_run:
                self.stdout.write("DRY RUN import planned:")
                for item in result.planned:
                    self.stdout.write(f"- {item.slug} :: {item.title} ({len(item.content)} chars)")
            else:
                self.stdout.write(
                    f"import created={result.created} updated={result.updated} "
                    f"unchanged={result.unchanged} skipped={result.skipped}"
                )

        if options["skip_reindex"] or options["dry_run"]:
            self.stdout.write("reindex skipped")
            return

        lifecycle = KnowledgeLifecycleService()
        results = lifecycle.reindex_tenant(tenant=tenant)
        indexed = sum(1 for item in results if getattr(item, "status", "") in {"INDEXED", "UNCHANGED"})
        failed = sum(1 for item in results if getattr(item, "status", "") == "FAILED")
        self.stdout.write(f"reindex documents={len(results)} indexed_or_unchanged={indexed} failed={failed}")
        readiness = lifecycle.readiness(tenant=tenant)
        self.stdout.write(f"readiness={readiness}")
