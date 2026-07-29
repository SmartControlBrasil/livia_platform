from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from audit.models import ACTION_TENANT_RAG_CONFIGURED
from audit.services import record_audit_event
from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.google_drive_inventory import validate_drive_folder_id
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Configura o tenant para inventario RAG em pasta aprovada do Google Drive."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--approved-folder-id", required=True, help="ID da pasta aprovada no Google Drive.")
        parser.add_argument("--enable-sync", action="store_true", help="Habilita sincronizacao para este tenant.")
        parser.add_argument("--disable-sync", action="store_true", help="Desabilita sincronizacao para este tenant.")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        folder_id_raw = str(options["approved_folder_id"]).strip()
        if options["enable_sync"] and options["disable_sync"]:
            raise CommandError("Use only one of --enable-sync or --disable-sync.")

        try:
            approved_folder_id = validate_drive_folder_id(folder_id_raw)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        existing = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        sync_enabled = bool(options["enable_sync"]) if (options["enable_sync"] or options["disable_sync"]) else bool(getattr(existing, "sync_enabled", False))

        configuration, created = TenantRagConfiguration.objects.update_or_create(
            tenant=tenant,
            defaults={
                "approved_folder_id": approved_folder_id,
                "sync_enabled": sync_enabled,
            },
        )

        changed = (
            created
            or existing is None
            or existing.approved_folder_id != approved_folder_id
            or existing.sync_enabled != sync_enabled
        )
        operation = "created" if created else ("updated" if changed else "unchanged")

        record_audit_event(
            action=ACTION_TENANT_RAG_CONFIGURED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / {approved_folder_id}",
            metadata={
                "source": "management_command.configure_tenant_rag",
                "operation": operation,
                "sync_enabled": sync_enabled,
            },
        )

        self.stdout.write(self.style.SUCCESS("Tenant RAG configuration saved."))
        self.stdout.write(f"tenant={tenant.slug}")
        self.stdout.write(f"approved_folder_id={configuration.approved_folder_id}")
        self.stdout.write(f"sync_enabled={configuration.sync_enabled}")
        self.stdout.write(f"operation={operation}")
