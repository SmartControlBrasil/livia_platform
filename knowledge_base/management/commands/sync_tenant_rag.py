from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError

from audit.models import ACTION_TENANT_RAG_CONFIGURED
from audit.services import record_audit_event
from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.google_drive_inventory import (
    GoogleDriveAuthenticationError,
    GoogleDriveConfigurationError,
    GoogleDriveInventoryService,
    GoogleDrivePermissionError,
    sanitize_external_error_message,
    build_google_drive_readonly_service,
)
from knowledge_base.rag.sync import (
    TenantRagSyncError,
    acquire_tenant_sync_lock,
    mark_configuration_failed,
    run_chunk_build_for_tenant,
    run_sync_for_inventory,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Executa inventario seguro dos arquivos da pasta aprovada do tenant (somente leitura)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--inventory-only", action="store_true", help="Inventaria metadados sem exportacao de texto.")
        parser.add_argument("--export-text", action="store_true", help="Inventaria e exporta texto somente de Google Docs.")
        parser.add_argument("--build-chunks", action="store_true", help="Constroi chunks locais a partir do staging do tenant.")

    def handle(self, *args, **options):
        selected_modes = [name for name in ("inventory_only", "export_text", "build_chunks") if bool(options.get(name))]
        if len(selected_modes) != 1:
            raise CommandError("Choose exactly one mode: --inventory-only, --export-text or --build-chunks.")
        mode = selected_modes[0]

        tenant_slug = str(options["tenant"]).strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        run_id = str(uuid.uuid4())
        try:
            configuration = acquire_tenant_sync_lock(tenant=tenant, mode=mode)
        except TenantRagSyncError as exc:
            raise CommandError(str(exc)) from exc

        record_audit_event(
            action=ACTION_TENANT_RAG_CONFIGURED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / sync",
            metadata={
                "source": "management_command.sync_tenant_rag",
                "phase": "started",
                "run_id": run_id,
                "mode": mode,
            },
        )

        try:
            if mode == "build_chunks":
                outcome = run_chunk_build_for_tenant(configuration=configuration)
            else:
                service = build_google_drive_readonly_service()
                inventory = GoogleDriveInventoryService(service).inventory_approved_folder(configuration.approved_folder_id)
                outcome = run_sync_for_inventory(
                    configuration=configuration,
                    mode=mode,
                    inventory_summary=inventory,
                    drive_service=GoogleDriveInventoryService(service),
                )
        except (GoogleDriveConfigurationError, GoogleDriveAuthenticationError, GoogleDrivePermissionError) as exc:
            safe_error = sanitize_external_error_message(str(exc))
            mark_configuration_failed(configuration, error_message=safe_error)
            record_audit_event(
                action=ACTION_TENANT_RAG_CONFIGURED,
                tenant=tenant,
                object_type="knowledge_base.tenantragconfiguration",
                object_id=str(configuration.pk),
                object_repr=f"{tenant.slug} / sync",
                metadata={
                    "source": "management_command.sync_tenant_rag",
                    "phase": "failed",
                    "run_id": run_id,
                    "mode": mode,
                    "error": safe_error,
                },
            )
            raise CommandError(str(exc)) from exc
        except TenantRagSyncError as exc:
            safe_error = sanitize_external_error_message(str(exc))
            mark_configuration_failed(configuration, error_message=safe_error)
            record_audit_event(
                action=ACTION_TENANT_RAG_CONFIGURED,
                tenant=tenant,
                object_type="knowledge_base.tenantragconfiguration",
                object_id=str(configuration.pk),
                object_repr=f"{tenant.slug} / sync",
                metadata={
                    "source": "management_command.sync_tenant_rag",
                    "phase": "failed",
                    "run_id": run_id,
                    "mode": mode,
                    "error": safe_error,
                },
            )
            raise CommandError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive fallback
            safe_error = "Unexpected inventory failure."
            mark_configuration_failed(configuration, error_message=safe_error)
            record_audit_event(
                action=ACTION_TENANT_RAG_CONFIGURED,
                tenant=tenant,
                object_type="knowledge_base.tenantragconfiguration",
                object_id=str(configuration.pk),
                object_repr=f"{tenant.slug} / sync",
                metadata={
                    "source": "management_command.sync_tenant_rag",
                    "phase": "failed",
                    "run_id": run_id,
                    "mode": mode,
                    "error": safe_error,
                },
            )
            raise CommandError("Unexpected inventory failure.") from exc

        counters = outcome.counters.as_dict()
        self.stdout.write(self.style.SUCCESS("Tenant RAG sync completed."))
        if mode == "build_chunks":
            self.stdout.write(
                "summary "
                f"tenant={tenant.slug} mode={mode} status={outcome.status} "
                f"documents={outcome.file_count} created={counters['discovered']} rebuilt={counters['exported']} "
                f"unchanged={counters['unchanged']} deactivated={counters['removed']} chunks_created={counters['updated']} "
                f"skipped={counters['skipped']} failed={counters['failed']}"
            )
        else:
            self.stdout.write(
                "summary "
                f"tenant={tenant.slug} mode={mode} status={outcome.status} "
                f"files={outcome.file_count} folders={outcome.folder_count} blocked_shortcuts={outcome.blocked_shortcuts} "
                f"discovered={counters['discovered']} exported={counters['exported']} updated={counters['updated']} "
                f"unchanged={counters['unchanged']} removed={counters['removed']} skipped={counters['skipped']} failed={counters['failed']}"
            )

        record_audit_event(
            action=ACTION_TENANT_RAG_CONFIGURED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / sync",
            metadata={
                "source": "management_command.sync_tenant_rag",
                "phase": "completed",
                "run_id": run_id,
                "mode": mode,
                "status": outcome.status,
                **counters,
            },
        )
        if outcome.status == TenantRagConfiguration.InventoryStatus.PARTIAL:
            raise CommandError("Sync finished with partial failures. Review summary and manifest status.")
