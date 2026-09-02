from __future__ import annotations

from django.core.management.base import BaseCommand

from knowledge_base.models import TenantRagConfiguration
from tenants.models import AssistantProfile, Tenant

PITONDO_FOLDER = "1IsNb2Rq7G9F-48GLYw2OKw962uUpLVxj"
COMMERCIAL_EMAIL = "contato@granimarmorespitondo.com.br"


class Command(BaseCommand):
    help = "Configura RAG Drive e notificação comercial do tenant granimarmores-pitondo."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="granimarmores-pitondo")
        parser.add_argument("--drive-folder-id", default=PITONDO_FOLDER)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"]).strip()).first()
        if tenant is None:
            self.stderr.write("Tenant não encontrado.")
            return
        folder = str(options["drive_folder_id"]).strip()
        dry = bool(options["dry_run"])
        profile, _ = AssistantProfile.objects.get_or_create(tenant=tenant)
        cfg, _ = TenantRagConfiguration.objects.get_or_create(tenant=tenant)

        updates = {
            "notification_email": COMMERCIAL_EMAIL,
            "business_domain": "marmoraria, pedras naturais e projetos sob medida",
            "business_name": profile.business_name or "Granimármores Pitondo",
            "short_description": profile.short_description
            or "Qualifica projetos de bancadas, cozinhas, banheiros, escadas e áreas gourmet com pedras naturais.",
            "initial_message": profile.initial_message
            or "Olá! Sou a Lívia da Granimármores Pitondo. Como posso ajudar?",
        }
        cfg_updates = {
            "source_mode": "google_drive",
            "approved_folder_id": folder,
            "sync_enabled": True,
            "retrieval_enabled": True,
        }
        if dry:
            self.stdout.write(f"DRY RUN profile={updates}")
            self.stdout.write(f"DRY RUN rag={cfg_updates}")
            return

        for key, value in updates.items():
            setattr(profile, key, value)
        profile.save()
        for key, value in cfg_updates.items():
            setattr(cfg, key, value)
        cfg.save()
        self.stdout.write(f"notification_email={profile.notification_email}")
        self.stdout.write(
            f"rag mode={cfg.source_mode} folder={cfg.approved_folder_id} "
            f"sync={cfg.sync_enabled} retrieval={cfg.retrieval_enabled}"
        )
