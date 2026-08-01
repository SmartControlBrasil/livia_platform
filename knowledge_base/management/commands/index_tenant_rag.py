from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError

from audit.models import (
    ACTION_TENANT_RAG_INDEX_COMPLETED,
    ACTION_TENANT_RAG_INDEX_FAILED,
    ACTION_TENANT_RAG_INDEX_STARTED,
)
from audit.services import record_audit_event
from knowledge_base.rag.embeddings import EmbeddingConfigurationError, load_embedding_config
from knowledge_base.rag.indexing import (
    TenantRagIndexingError,
    _decide_pending,
    acquire_tenant_index_lock,
    mark_index_failed,
    run_index_for_tenant,
)
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        "Indexa embeddings dos chunks ativos do tenant de forma incremental. "
        "Nao acessa Google Drive nem ativa o retriever publico."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula decisoes de indexacao sem chamar o provedor nem gravar embeddings.",
        )

        parser.add_argument(
            "--only-stale",
            action="store_true",
            help="Reindexa apenas chunks com embedding existente incompatível; não indexa chunks novos.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Máximo de chunks a indexar/reindexar nesta execução (ordem por id).",
        )
        parser.add_argument(
            "--drive-file-id",
            default="",
            help="Restringe a um manifest (Google Drive file id) do tenant.",
        )

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        dry_run = bool(options.get("dry_run"))
        only_stale = bool(options.get("only_stale"))
        limit = options.get("limit")
        drive_file_id = str(options.get("drive_file_id") or "").strip() or None
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant not found.")

        try:
            config = load_embedding_config()
        except EmbeddingConfigurationError as exc:
            raise CommandError(str(exc)) from exc

        if not dry_run and not config.indexing_enabled:
            raise CommandError(
                "Indexing is disabled. Set LIVIA_RAG_INDEXING_ENABLED=True only after explicit authorization."
            )

        if limit is not None and int(limit) <= 0:
            raise CommandError("--limit must be a positive integer.")

        run_id = str(uuid.uuid4())
        mode = "dry_run" if dry_run else "index"
        try:
            configuration = acquire_tenant_index_lock(tenant=tenant, mode=mode, run_id=run_id)
        except TenantRagIndexingError as exc:
            raise CommandError(str(exc)) from exc

        record_audit_event(
            action=ACTION_TENANT_RAG_INDEX_STARTED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / index",
            metadata={
                "source": "management_command.index_tenant_rag",
                "phase": "started",
                "run_id": run_id,
                "mode": mode,
                "dry_run": dry_run,
                "provider": config.provider,
                "model": config.model,
                "dimension": config.dimension,
                "embedding_config_signature": config.signature,
            },
        )

        pending_preview, preview_counters, _ = _decide_pending(
            tenant=tenant,
            config=config,
            only_stale=only_stale,
            drive_file_id=drive_file_id,
            max_pending=int(limit) if limit is not None else None,
        )
        if pending_preview:
            batch_size = config.batch_size
            batches = (len(pending_preview) + batch_size - 1) // batch_size
            self.stdout.write(
                f"estimate selected={len(pending_preview)} batch_size={batch_size} "
                f"batches={batches} model={config.model} dimension={config.dimension}"
            )

        try:
            outcome = run_index_for_tenant(
                configuration=configuration,
                dry_run=dry_run,
                only_stale=only_stale,
                limit=int(limit) if limit is not None else None,
                drive_file_id=drive_file_id,
                config=config,
                run_id=run_id,
            )
        except (TenantRagIndexingError, EmbeddingConfigurationError) as exc:
            mark_index_failed(configuration=configuration, run=None, error=str(exc))
            record_audit_event(
                action=ACTION_TENANT_RAG_INDEX_FAILED,
                tenant=tenant,
                object_type="knowledge_base.tenantragconfiguration",
                object_id=str(configuration.pk),
                object_repr=f"{tenant.slug} / index",
                metadata={
                    "source": "management_command.index_tenant_rag",
                    "phase": "failed",
                    "run_id": run_id,
                    "mode": mode,
                    "dry_run": dry_run,
                    "error": str(exc)[:500],
                },
            )
            raise CommandError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            mark_index_failed(configuration=configuration, run=None, error="indexing_unexpected_error")
            record_audit_event(
                action=ACTION_TENANT_RAG_INDEX_FAILED,
                tenant=tenant,
                object_type="knowledge_base.tenantragconfiguration",
                object_id=str(configuration.pk),
                object_repr=f"{tenant.slug} / index",
                metadata={
                    "source": "management_command.index_tenant_rag",
                    "phase": "failed",
                    "run_id": run_id,
                    "mode": mode,
                    "dry_run": dry_run,
                    "error": "indexing_unexpected_error",
                },
            )
            raise CommandError("Indexing failed.") from exc

        counters = outcome.counters.as_dict()
        record_audit_event(
            action=ACTION_TENANT_RAG_INDEX_COMPLETED,
            tenant=tenant,
            object_type="knowledge_base.tenantragconfiguration",
            object_id=str(configuration.pk),
            object_repr=f"{tenant.slug} / index",
            metadata={
                "source": "management_command.index_tenant_rag",
                "phase": "completed",
                "run_id": outcome.run_id,
                "mode": outcome.mode,
                "dry_run": outcome.dry_run,
                "status": outcome.status,
                "provider": outcome.provider,
                "model": outcome.model,
                "dimension": outcome.dimension,
                "embedding_config_signature": outcome.embedding_config_signature,
                **counters,
            },
        )

        summary = (
            f"summary tenant={tenant.slug} mode={outcome.mode} status={outcome.status} "
            f"documents={counters['documents']} chunks={counters['chunks']} pending={counters['pending']} "
            f"indexed={counters['indexed']} reindexed={counters['reindexed']} unchanged={counters['unchanged']} "
            f"deactivated={counters['deactivated']} skipped={counters['skipped']} failed={counters['failed']} "
            f"batches={counters['batches']}"
        )
        self.stdout.write(self.style.SUCCESS("Tenant RAG indexing completed."))
        self.stdout.write(summary)

        if outcome.status in {"partial", "failed"}:
            raise CommandError(f"Indexing finished with status={outcome.status}.")
