from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from tenants.services.staging_deployment import (
    PILOT_TENANT_SLUG,
    build_staging_deployment_report,
)


class Command(BaseCommand):
    help = "Relatório sanitizado de deploy staging (sem credenciais)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default=PILOT_TENANT_SLUG)
        parser.add_argument("--json", action="store_true")
        parser.add_argument(
            "--fail-on-warning",
            action="store_true",
            help="Exit 1 when environment readiness is READY_WITH_WARNINGS.",
        )

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"]).strip()
        payload = build_staging_deployment_report(tenant_slug=tenant_slug)

        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"environment={payload['environment']}")
            self.stdout.write(f"commit={payload.get('commit') or 'unknown'}")
            db = payload["database"]
            self.stdout.write(f"database.engine={db['engine']}")
            self.stdout.write(f"database.name={db['name']}")
            self.stdout.write(f"database.host={db['host']}")
            self.stdout.write(f"database.port={db['port']}")
            self.stdout.write(f"database.sanitized_url={db['sanitized_url']}")
            self.stdout.write(f"database.pending_migrations={db['pending_migrations']}")
            self.stdout.write(f"database.pgvector_extension={db['pgvector_extension']}")
            tenant = payload["tenant"]
            self.stdout.write(f"tenant.slug={tenant['slug']}")
            self.stdout.write(f"tenant.exists={tenant['exists']}")
            self.stdout.write(f"tenant.is_active={tenant['is_active']}")
            self.stdout.write(f"tenant.allowed_origins={tenant['allowed_origins']}")
            gates = payload["feature_gates"]
            self.stdout.write(f"embedding_provider={gates['embedding_provider']}")
            self.stdout.write(f"vector_backend={gates['vector_backend']}")
            self.stdout.write(f"rag_allowlist={gates['rag_allowlist']}")
            self.stdout.write(f"grounded_allowlist={gates['grounded_allowlist']}")
            integrations = payload["integrations"]
            self.stdout.write(
                "integrations="
                + json.dumps(integrations, ensure_ascii=False, sort_keys=True)
            )
            readiness = payload["readiness"]["environment_status"]
            self.stdout.write(f"environment_readiness={readiness}")

        env_status = payload["readiness"]["environment_status"]
        if env_status == "NOT_READY":
            raise SystemExit(1)
        if options["fail_on_warning"] and env_status == "READY_WITH_WARNINGS":
            raise SystemExit(1)
