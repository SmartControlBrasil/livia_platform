from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from tenants.services.database_validation import build_database_validation_report


class Command(BaseCommand):
    help = "Mostra readiness readonly do banco atual sem imprimir credenciais."

    def handle(self, *args, **options):
        ready = True
        self.stdout.write("Database readiness")
        config = connection.settings_dict
        engine = str(config.get("ENGINE", ""))
        self.stdout.write(f"engine={engine}")
        self.stdout.write(f"type={_database_type(engine)}")
        self.stdout.write(f"name={_safe_name(config.get('NAME'))}")
        self.stdout.write(f"host={_safe_host(config.get('HOST'))}")
        self.stdout.write(f"conn_max_age={config.get('CONN_MAX_AGE', 0)}")
        self.stdout.write(f"conn_health_checks={config.get('CONN_HEALTH_CHECKS', False)}")
        self.stdout.write(f"django_timezone={timezone.get_current_timezone_name()}")

        try:
            connection.ensure_connection()
            self.stdout.write("connection=available")
            self.stdout.write(f"vendor={connection.vendor}")
            self.stdout.write(f"database_version={_database_version()}")
            self.stdout.write(f"database_timezone={_database_timezone()}")
        except Exception as exc:
            ready = False
            self.stdout.write(self.style.ERROR(f"connection=unavailable error={exc.__class__.__name__}"))

        try:
            pending = _pending_migrations()
            self.stdout.write(f"pending_migrations={len(pending)}")
            for migration, _backwards in pending[:20]:
                self.stdout.write(f"pending={migration.app_label}.{migration.name}")
            if pending:
                ready = False
        except Exception as exc:
            ready = False
            self.stdout.write(self.style.ERROR(f"migrations=unknown error={exc.__class__.__name__}"))

        try:
            report = build_database_validation_report()
            for key, value in report.totals.items():
                self.stdout.write(f"count_{key}={value}")
            for key, value in report.tenant_integrity.items():
                self.stdout.write(f"integrity_{key}={value}")
                if value:
                    ready = False
        except Exception as exc:
            ready = False
            self.stdout.write(self.style.ERROR(f"counts=unavailable error={exc.__class__.__name__}"))

        try:
            from knowledge_base.rag.readiness import inspect_rag_vector_readiness

            self.stdout.write("rag_vector_readiness")
            for check in inspect_rag_vector_readiness():
                line = f"[{'OK' if check.ok else 'FAIL'}] {check.code}: {check.detail}"
                if check.ok:
                    self.stdout.write(line)
                else:
                    # Readiness RAG nao derruba READY geral em SQLite; apenas reporta.
                    if connection.vendor == "postgresql" and check.code in {
                        "pgvector_extension",
                        "vector_column",
                        "vector_index",
                        "active_backend",
                        "embedding_profile",
                        "indexed_embeddings",
                    }:
                        ready = False
                    self.stdout.write(self.style.ERROR(line))
        except Exception as exc:
            ready = False
            self.stdout.write(self.style.ERROR(f"rag_vector_readiness=unavailable error={exc.__class__.__name__}"))

        try:
            from config.environment_safety import inspect_environment_safety, summarize_environment_readiness

            self.stdout.write("environment_safety")
            env_checks = inspect_environment_safety()
            env_status = summarize_environment_readiness(env_checks)
            for item in env_checks:
                if item.level == "info" and item.ok:
                    continue
                mark = "OK" if item.ok else item.level.upper()
                line = f"[{mark}] {item.code}: {item.detail}"
                if item.ok:
                    self.stdout.write(line)
                else:
                    self.stdout.write(self.style.ERROR(line))
                    if item.level == "critical":
                        ready = False
            self.stdout.write(f"environment_status={env_status}")
            if env_status == "NOT_READY":
                ready = False
        except Exception as exc:
            ready = False
            self.stdout.write(self.style.ERROR(f"environment_safety=unavailable error={exc.__class__.__name__}"))

        label = "READY" if ready else "NOT READY"
        self.stdout.write(self.style.SUCCESS(label) if ready else self.style.ERROR(label))
        if not ready:
            raise SystemExit(1)


def _database_type(engine: str) -> str:
    if "postgresql" in engine:
        return "PostgreSQL"
    if "sqlite3" in engine:
        return "SQLite"
    return "Other"


def _safe_name(name) -> str:
    text = str(name or "")
    return text.rsplit("/", 1)[-1] if text else "configured"


def _safe_host(host) -> str:
    return "configured" if str(host or "").strip() else "local"


def _pending_migrations():
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return executor.migration_plan(targets)


def _database_version() -> str:
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("SELECT version()")
        elif connection.vendor == "sqlite":
            cursor.execute("SELECT sqlite_version()")
        else:
            return "unknown"
        return str(cursor.fetchone()[0]).split("\\n", 1)[0]

def _database_timezone() -> str:
    if connection.vendor != "postgresql":
        return "not_applicable"
    with connection.cursor() as cursor:
        cursor.execute("SHOW TIME ZONE")
        return str(cursor.fetchone()[0])
