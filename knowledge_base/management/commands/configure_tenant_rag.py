from __future__ import annotations

import math

from django.core.management.base import BaseCommand, CommandError

from audit.models import ACTION_TENANT_RAG_CONFIGURED
from audit.services import record_audit_event
from knowledge_base.models import TenantRagConfiguration
from knowledge_base.rag.google_drive_inventory import validate_drive_folder_id
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Configura RAG do tenant para conhecimento manual/local ou Google Drive."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument(
            "--source-mode",
            choices=[TenantRagConfiguration.SOURCE_MANUAL, TenantRagConfiguration.SOURCE_GOOGLE_DRIVE],
            default="",
            help="Origem operacional do conhecimento: manual ou google_drive.",
        )
        parser.add_argument("--approved-folder-id", default="", help="ID da pasta aprovada no Google Drive.")
        parser.add_argument("--clear-approved-folder-id", action="store_true", help="Remove o ID de pasta aprovado. Use para tenants manuais.")
        parser.add_argument("--enable-sync", action="store_true", help="Habilita sincronizacao Google Drive para este tenant.")
        parser.add_argument("--disable-sync", action="store_true", help="Desabilita sincronizacao Google Drive para este tenant.")
        parser.add_argument(
            "--enable-retrieval",
            action="store_true",
            help="Habilita recuperacao semantica no fluxo de conversa deste tenant.",
        )
        parser.add_argument(
            "--disable-retrieval",
            action="store_true",
            help="Desabilita recuperacao semantica no fluxo de conversa deste tenant.",
        )
        parser.add_argument(
            "--min-similarity-score",
            type=float,
            default=None,
            help="Override do threshold de similaridade (0.0 a 1.0) para este tenant.",
        )
        parser.add_argument(
            "--clear-min-similarity-score",
            action="store_true",
            help="Remove override de threshold e volta ao default global.",
        )

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        folder_id_raw = str(options["approved_folder_id"]).strip()
        source_mode_raw = str(options["source_mode"] or "").strip()
        if options["enable_sync"] and options["disable_sync"]:
            raise CommandError("Use only one of --enable-sync or --disable-sync.")
        if options["enable_retrieval"] and options["disable_retrieval"]:
            raise CommandError("Use only one of --enable-retrieval or --disable-retrieval.")
        if options["min_similarity_score"] is not None and options["clear_min_similarity_score"]:
            raise CommandError("Use only one of --min-similarity-score or --clear-min-similarity-score.")
        if folder_id_raw and options["clear_approved_folder_id"]:
            raise CommandError("Use only one of --approved-folder-id or --clear-approved-folder-id.")

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        existing = TenantRagConfiguration.objects.filter(tenant=tenant).first()
        approved_folder_id = getattr(existing, "approved_folder_id", "") or ""
        if options["clear_approved_folder_id"]:
            approved_folder_id = ""
        elif folder_id_raw:
            try:
                approved_folder_id = validate_drive_folder_id(folder_id_raw)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        if source_mode_raw:
            source_mode = source_mode_raw
        elif existing is not None:
            source_mode = existing.source_mode
        elif approved_folder_id or options["enable_sync"]:
            source_mode = TenantRagConfiguration.SOURCE_GOOGLE_DRIVE
        else:
            source_mode = TenantRagConfiguration.SOURCE_MANUAL

        sync_enabled = bool(options["enable_sync"]) if (options["enable_sync"] or options["disable_sync"]) else bool(getattr(existing, "sync_enabled", False))
        retrieval_enabled = (
            bool(options["enable_retrieval"])
            if (options["enable_retrieval"] or options["disable_retrieval"])
            else bool(getattr(existing, "retrieval_enabled", False))
        )

        if sync_enabled and source_mode != TenantRagConfiguration.SOURCE_GOOGLE_DRIVE:
            raise CommandError("--enable-sync requires --source-mode google_drive and --approved-folder-id.")
        if sync_enabled and not approved_folder_id:
            raise CommandError("--enable-sync for Google Drive requires --approved-folder-id.")

        min_similarity_score = getattr(existing, "min_similarity_score", None)
        if options["clear_min_similarity_score"]:
            min_similarity_score = None
        elif options["min_similarity_score"] is not None:
            value = float(options["min_similarity_score"])
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise CommandError("--min-similarity-score must be a finite number between 0 and 1.")
            min_similarity_score = value

        configuration, created = TenantRagConfiguration.objects.update_or_create(
            tenant=tenant,
            defaults={
                "source_mode": source_mode,
                "approved_folder_id": approved_folder_id,
                "sync_enabled": sync_enabled,
                "retrieval_enabled": retrieval_enabled,
                "min_similarity_score": min_similarity_score,
            },
        )

        changed = (
            created
            or existing is None
            or existing.source_mode != source_mode
            or existing.approved_folder_id != approved_folder_id
            or existing.sync_enabled != sync_enabled
            or getattr(existing, "retrieval_enabled", False) != retrieval_enabled
            or getattr(existing, "min_similarity_score", None) != min_similarity_score
        )
        operation = "created" if created else ("updated" if changed else "unchanged")

        record_audit_event(
            action=ACTION_TENANT_RAG_CONFIGURED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / {configuration.source_mode}",
            metadata={
                "source": "management_command.configure_tenant_rag",
                "operation": operation,
                "source_mode_before": None if existing is None else existing.source_mode,
                "source_mode_after": source_mode,
                "sync_enabled": sync_enabled,
                "retrieval_enabled": retrieval_enabled,
                "min_similarity_score_before": None if existing is None else existing.min_similarity_score,
                "min_similarity_score_after": min_similarity_score,
                "approved_folder_id_before": None if existing is None else existing.approved_folder_id,
                "approved_folder_id_after": approved_folder_id,
            },
        )

        self.stdout.write(self.style.SUCCESS("Tenant RAG configuration saved."))
        self.stdout.write(f"tenant={tenant.slug}")
        self.stdout.write(f"source_mode={configuration.source_mode}")
        self.stdout.write(f"approved_folder_id={configuration.approved_folder_id}")
        self.stdout.write(f"sync_enabled={configuration.sync_enabled}")
        self.stdout.write(f"retrieval_enabled={configuration.retrieval_enabled}")
        self.stdout.write(f"min_similarity_score={configuration.min_similarity_score}")
        self.stdout.write(f"operation={operation}")
