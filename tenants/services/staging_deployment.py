from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

STAGING_ALLOWED_BRANCH = "chore/postgresql-readiness"
PILOT_TENANT_SLUG = "granimarmores-pitondo"
STAGING_EXPECTED_DB_NAME = "livia_staging"
STAGING_ALLOWED_DB_HOSTS = {"127.0.0.1", "localhost", "::1", ""}
STAGING_VECTOR_BACKENDS = {"postgres_pgvector", "auto", "pgvector"}
PLACEHOLDER_MARKERS = (
    "CHANGE_ME",
    "change-me",
    "changeme",
    "REPLACE_ME",
    "your-",
)
SECRET_ENV_KEYS = (
    "DJANGO_SECRET_KEY",
    "DATABASE_URL",
    "LIVIA_OPENAI_API_KEY",
    "LIVIA_RAG_EMBEDDING_API_KEY",
    "SMART360_M2M_TOKEN",
)


@dataclass(frozen=True)
class StagingCheckResult:
    code: str
    status: str  # PASS | WARN | FAIL
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


@dataclass
class StagingCheckReport:
    checks: list[StagingCheckResult] = field(default_factory=list)

    def add(self, code: str, status: str, detail: str) -> None:
        self.checks.append(StagingCheckResult(code=code, status=status, detail=detail))

    def append(self, result: StagingCheckResult) -> None:
        self.checks.append(result)

    @property
    def has_failures(self) -> bool:
        return any(item.failed for item in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(item.status == "WARN" for item in self.checks)

    def summary_status(self) -> str:
        if self.has_failures:
            return "FAIL"
        if self.has_warnings:
            return "WARN"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary_status(),
            "checks": [asdict(item) for item in self.checks],
        }


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def sanitize_database_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if not parsed.scheme:
        return "(unset)"
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    db_name = (parsed.path or "").lstrip("/") or "(unknown)"
    user = parsed.username or "(unknown)"
    return f"{parsed.scheme}://{user}:***@{host}:{port}/{db_name}"


def is_placeholder_value(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return True
    lower = normalized.lower()
    return any(marker.lower() in lower for marker in PLACEHOLDER_MARKERS)


def normalize_allowlist(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def check_git_branch(*, allowed_branch: str = STAGING_ALLOWED_BRANCH) -> StagingCheckResult:
    try:
        current = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return StagingCheckResult("git_branch", "FAIL", f"unable to read branch: {exc.__class__.__name__}")
    if current != allowed_branch:
        return StagingCheckResult(
            "git_branch",
            "FAIL",
            f"branch={current} expected={allowed_branch}",
        )
    return StagingCheckResult("git_branch", "PASS", f"branch={current}")


def check_git_clean(*, allow_dirty: bool) -> StagingCheckResult:
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return StagingCheckResult("git_clean", "WARN", f"unable to inspect git status: {exc.__class__.__name__}")
    if status and not allow_dirty:
        lines = len(status.splitlines())
        return StagingCheckResult("git_clean", "FAIL", f"working tree dirty ({lines} entries)")
    if status:
        return StagingCheckResult("git_clean", "WARN", "dirty working tree explicitly allowed")
    return StagingCheckResult("git_clean", "PASS", "working tree clean")


def check_python_version(*, minimum: tuple[int, int] = (3, 12)) -> StagingCheckResult:
    if sys.version_info >= minimum:
        return StagingCheckResult(
            "python_version",
            "PASS",
            f"python={sys.version_info.major}.{sys.version_info.minor}",
        )
    return StagingCheckResult(
        "python_version",
        "FAIL",
        f"python={sys.version_info.major}.{sys.version_info.minor} minimum={minimum[0]}.{minimum[1]}",
    )


def check_env_file_exists(env_path: Path) -> StagingCheckResult:
    if env_path.is_file():
        return StagingCheckResult("env_file", "PASS", f"path={env_path}")
    return StagingCheckResult("env_file", "FAIL", f"missing {env_path}")


def check_env_permissions(env_path: Path) -> StagingCheckResult:
    if not env_path.is_file():
        return StagingCheckResult("env_permissions", "FAIL", "env file missing")
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode & stat.S_IROTH:
        return StagingCheckResult("env_permissions", "FAIL", f"world-readable mode={oct(mode)}")
    if mode & stat.S_IWOTH:
        return StagingCheckResult("env_permissions", "FAIL", f"world-writable mode={oct(mode)}")
    if mode & stat.S_IRGRP:
        return StagingCheckResult("env_permissions", "WARN", f"group-readable mode={oct(mode)}")
    return StagingCheckResult("env_permissions", "PASS", f"mode={oct(mode)}")


def check_staging_env_values(env_values: dict[str, str], *, pilot_tenant: str = PILOT_TENANT_SLUG) -> list[StagingCheckResult]:
    results: list[StagingCheckResult] = []

    def _req(key: str, expected: str | None = None, *, equals: str | None = None) -> None:
        value = str(env_values.get(key, "")).strip()
        if not value:
            results.append(StagingCheckResult(f"env_{key.lower()}", "FAIL", f"{key} is unset"))
            return
        if equals is not None and value.lower() != equals.lower():
            results.append(StagingCheckResult(f"env_{key.lower()}", "FAIL", f"{key}={value} expected={equals}"))
            return
        if expected:
            results.append(StagingCheckResult(f"env_{key.lower()}", "PASS", f"{key}={expected}"))
        else:
            results.append(StagingCheckResult(f"env_{key.lower()}", "PASS", f"{key} set"))

    _req("LIVIA_ENVIRONMENT", "staging", equals="staging")
    _req("DJANGO_DEBUG", "False", equals="False")

    db_url = env_values.get("DATABASE_URL", "")
    if not db_url:
        results.append(StagingCheckResult("database_url", "FAIL", "DATABASE_URL unset"))
    elif "sqlite" in db_url.lower():
        results.append(StagingCheckResult("database_url", "FAIL", "SQLite forbidden in staging physical deploy"))
    else:
        parsed = urlparse(db_url)
        host = (parsed.hostname or "").lower()
        db_name = (parsed.path or "").lstrip("/")
        if host not in STAGING_ALLOWED_DB_HOSTS:
            results.append(StagingCheckResult("database_host", "FAIL", f"host={host} not local"))
        else:
            results.append(StagingCheckResult("database_host", "PASS", f"host={host or 'localhost'}"))
        if db_name != STAGING_EXPECTED_DB_NAME:
            results.append(
                StagingCheckResult(
                    "database_name",
                    "FAIL",
                    f"name={db_name or '(empty)'} expected={STAGING_EXPECTED_DB_NAME}",
                )
            )
        else:
            results.append(StagingCheckResult("database_name", "PASS", f"name={db_name}"))
        if parsed.password and is_placeholder_value(parsed.password):
            results.append(StagingCheckResult("database_password", "FAIL", "placeholder password in DATABASE_URL"))
        else:
            results.append(StagingCheckResult("database_url", "PASS", sanitize_database_url(db_url)))

    provider = str(env_values.get("LIVIA_RAG_EMBEDDING_PROVIDER", "")).strip().lower()
    if provider == "fake":
        results.append(StagingCheckResult("embedding_provider", "FAIL", "fake provider forbidden"))
    elif not provider:
        results.append(StagingCheckResult("embedding_provider", "FAIL", "provider unset"))
    else:
        results.append(StagingCheckResult("embedding_provider", "PASS", f"provider={provider}"))

    if str(env_values.get("LIVIA_ALLOW_FAKE_EMBEDDINGS", "")).strip().lower() in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("allow_fake_embeddings", "FAIL", "must be False"))
    else:
        results.append(StagingCheckResult("allow_fake_embeddings", "PASS", "False"))

    vector_backend = str(env_values.get("LIVIA_RAG_VECTOR_BACKEND", "")).strip().lower()
    if vector_backend not in STAGING_VECTOR_BACKENDS:
        results.append(StagingCheckResult("vector_backend", "FAIL", f"backend={vector_backend or '(unset)'}"))
    elif vector_backend == "pgvector":
        results.append(
            StagingCheckResult(
                "vector_backend",
                "WARN",
                "use postgres_pgvector in runtime; pgvector accepted as intent alias",
            )
        )
    else:
        results.append(StagingCheckResult("vector_backend", "PASS", f"backend={vector_backend}"))

    rag_allow = normalize_allowlist(env_values.get("LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST", ""))
    grounded_allow = normalize_allowlist(env_values.get("LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST", ""))
    if rag_allow != [pilot_tenant]:
        results.append(StagingCheckResult("rag_allowlist", "FAIL", f"allowlist={rag_allow or '(empty)'}"))
    else:
        results.append(StagingCheckResult("rag_allowlist", "PASS", f"tenant={pilot_tenant}"))
    if grounded_allow != [pilot_tenant]:
        results.append(StagingCheckResult("grounded_allowlist", "FAIL", f"allowlist={grounded_allow or '(empty)'}"))
    else:
        results.append(StagingCheckResult("grounded_allowlist", "PASS", f"tenant={pilot_tenant}"))

    if str(env_values.get("SMART360_LEAD_DISPATCH_DRY_RUN", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("smart360_dry_run", "FAIL", "SMART360_LEAD_DISPATCH_DRY_RUN must be True"))
    else:
        results.append(StagingCheckResult("smart360_dry_run", "PASS", "dry_run=True"))

    if str(env_values.get("SMART360_LEAD_DISPATCH_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("smart360_enabled", "WARN", "dispatch enabled — ensure dry-run remains True"))
    else:
        results.append(StagingCheckResult("smart360_enabled", "PASS", "enabled=False"))

    if str(env_values.get("LIVIA_WEBHOOKS_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("webhooks_enabled", "FAIL", "webhooks must stay disabled in staging"))
    elif str(env_values.get("LIVIA_WEBHOOKS_DRY_RUN", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("webhooks_dry_run", "FAIL", "LIVIA_WEBHOOKS_DRY_RUN must be True"))
    else:
        results.append(StagingCheckResult("webhooks", "PASS", "off/dry-run"))

    if str(env_values.get("LIVIA_HANDOFF_NOTIFICATIONS_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("handoff_enabled", "FAIL", "handoff notifications must stay disabled"))
    elif str(env_values.get("LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("handoff_dry_run", "FAIL", "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN must be True"))
    else:
        results.append(StagingCheckResult("handoff", "PASS", "off/dry-run"))

    if str(env_values.get("LIVIA_ALLOW_ORIGINLESS_PUBLIC_API", "")).strip().lower() in {"1", "true", "yes", "on"}:
        results.append(StagingCheckResult("originless_api", "FAIL", "originless public API forbidden in staging"))
    else:
        results.append(StagingCheckResult("originless_api", "PASS", "False"))

    sa_path = str(env_values.get("LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE", "")).strip()
    if not sa_path:
        results.append(StagingCheckResult("service_account_path", "FAIL", "LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE unset"))
    elif is_placeholder_value(sa_path):
        results.append(StagingCheckResult("service_account_path", "FAIL", "placeholder service account path"))
    elif not Path(sa_path).is_file():
        results.append(StagingCheckResult("service_account_file", "WARN", f"missing file at configured path"))
    else:
        results.append(StagingCheckResult("service_account_file", "PASS", "file present"))

    for key in SECRET_ENV_KEYS:
        value = str(env_values.get(key, "")).strip()
        if not value:
            results.append(StagingCheckResult(f"secret_{key.lower()}", "FAIL", f"{key} unset"))
        elif is_placeholder_value(value):
            results.append(StagingCheckResult(f"secret_{key.lower()}", "FAIL", f"{key} still placeholder"))
        else:
            results.append(StagingCheckResult(f"secret_{key.lower()}", "PASS", f"{key} configured"))

    return results


def check_bind_port_available(host: str, port: int) -> StagingCheckResult:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        return StagingCheckResult("bind_port", "FAIL", f"{host}:{port} unavailable ({exc.__class__.__name__})")
    finally:
        sock.close()
    return StagingCheckResult("bind_port", "PASS", f"{host}:{port} available")


def check_pgvector_extension() -> StagingCheckResult:
    if connection.vendor != "postgresql":
        return StagingCheckResult("pgvector_extension", "FAIL", f"vendor={connection.vendor}")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cursor.fetchone()
    except Exception as exc:
        return StagingCheckResult("pgvector_extension", "FAIL", exc.__class__.__name__)
    if not row:
        return StagingCheckResult("pgvector_extension", "FAIL", "extension missing")
    return StagingCheckResult("pgvector_extension", "PASS", f"extversion={row[0]}")


def check_pending_migrations() -> StagingCheckResult:
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes(), clean=True)
    if pending:
        return StagingCheckResult("pending_migrations", "FAIL", f"count={len(pending)}")
    return StagingCheckResult("pending_migrations", "PASS", "count=0")


def check_django_system() -> StagingCheckResult:
    from django.core.checks import run_checks

    errors = run_checks()
    if errors:
        return StagingCheckResult("django_check", "FAIL", f"errors={len(errors)}")
    return StagingCheckResult("django_check", "PASS", "ok")


def check_environment_readiness(*, tenant_slug: str) -> StagingCheckResult:
    try:
        call_command("environment_readiness", tenant=tenant_slug, verbosity=0)
    except SystemExit as exc:
        if int(getattr(exc, "code", 1) or 0) != 0:
            return StagingCheckResult("environment_readiness", "FAIL", "NOT_READY")
    except Exception as exc:
        return StagingCheckResult("environment_readiness", "FAIL", exc.__class__.__name__)
    return StagingCheckResult("environment_readiness", "PASS", "READY")


def check_database_readiness() -> StagingCheckResult:
    try:
        call_command("database_readiness", verbosity=0)
    except SystemExit as exc:
        if int(getattr(exc, "code", 1) or 0) != 0:
            return StagingCheckResult("database_readiness", "FAIL", "NOT READY")
    except Exception as exc:
        return StagingCheckResult("database_readiness", "FAIL", exc.__class__.__name__)
    return StagingCheckResult("database_readiness", "PASS", "READY")


def run_predeploy_checks(
    *,
    project_root: Path,
    env_path: Path | None = None,
    allow_dirty: bool = False,
    bind_host: str | None = None,
    bind_port: int | None = None,
    pilot_tenant: str = PILOT_TENANT_SLUG,
    skip_git: bool = False,
    django_checks: bool = True,
) -> StagingCheckReport:
    report = StagingCheckReport()
    env_path = env_path or (project_root / ".env")

    report.append(check_python_version())
    if not skip_git:
        report.append(check_git_branch())
        report.append(check_git_clean(allow_dirty=allow_dirty))

    report.append(check_env_file_exists(env_path))
    env_values = parse_env_file(env_path)
    if env_path.is_file():
        report.append(check_env_permissions(env_path))
        for item in check_staging_env_values(env_values, pilot_tenant=pilot_tenant):
            report.checks.append(item)

    host = bind_host or env_values.get("LIVIA_STAGING_BIND_HOST", "127.0.0.1")
    port_raw = bind_port or env_values.get("LIVIA_STAGING_BIND_PORT", "8012")
    try:
        port = int(str(port_raw).strip())
    except ValueError:
        report.add("bind_port", "FAIL", f"invalid port={port_raw!r}")
    else:
        report.append(check_bind_port_available(host, port))

    if not django_checks:
        return report

    try:
        connection.ensure_connection()
        report.add("database_connection", "PASS", f"vendor={connection.vendor}")
    except Exception as exc:
        report.add("database_connection", "FAIL", exc.__class__.__name__)
        return report

    report.append(check_pgvector_extension())
    report.append(check_pending_migrations())
    report.append(check_django_system())
    report.append(check_environment_readiness(tenant_slug=pilot_tenant))
    report.append(check_database_readiness())
    return report


def _git_commit(project_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def build_staging_deployment_report(*, tenant_slug: str = PILOT_TENANT_SLUG, project_root: Path | None = None) -> dict[str, Any]:
    project_root = project_root or Path(settings.BASE_DIR)
    config = connection.settings_dict
    engine = str(config.get("ENGINE", ""))
    db_name = str(config.get("NAME", ""))
    db_host = str(config.get("HOST", "") or "localhost")
    db_port = int(config.get("PORT") or 5432)

    pending_count = 0
    try:
        executor = MigrationExecutor(connection)
        pending_count = len(executor.migration_plan(executor.loader.graph.leaf_nodes(), clean=True))
    except Exception:
        pending_count = -1

    from config.environment_safety import inspect_environment_safety, summarize_environment_readiness
    from knowledge_base.rag.readiness import inspect_rag_vector_readiness
    from tenants.models import Tenant, TenantAllowedOrigin

    tenant = Tenant.objects.filter(slug=tenant_slug).first()
    origins = list(
        TenantAllowedOrigin.objects.filter(tenant=tenant, is_active=True).values_list("origin", flat=True)
    ) if tenant else []

    env_checks = inspect_environment_safety(tenant_slug=tenant_slug)
    env_status = summarize_environment_readiness(env_checks)

    vector_checks = []
    if connection.vendor == "postgresql":
        vector_checks = [
            {"ok": item.ok, "code": item.code, "detail": item.detail}
            for item in inspect_rag_vector_readiness()
        ]

    return {
        "environment": str(getattr(settings, "LIVIA_ENVIRONMENT", "")),
        "commit": _git_commit(project_root),
        "database": {
            "engine": engine,
            "vendor": connection.vendor,
            "name": db_name,
            "host": db_host,
            "port": db_port,
            "sanitized_url": sanitize_database_url(os.environ.get("DATABASE_URL", "")),
            "pending_migrations": pending_count,
            "pgvector_extension": check_pgvector_extension().status == "PASS",
        },
        "tenant": {
            "slug": tenant_slug,
            "exists": tenant is not None,
            "is_active": bool(tenant and tenant.is_active),
            "allowed_origins": origins,
        },
        "feature_gates": {
            "rag_allowlist": normalize_allowlist(getattr(settings, "LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST", "")),
            "grounded_allowlist": normalize_allowlist(
                getattr(settings, "LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST", "")
            ),
            "embedding_provider": getattr(settings, "LIVIA_RAG_EMBEDDING_PROVIDER", ""),
            "vector_backend": getattr(settings, "LIVIA_RAG_VECTOR_BACKEND", ""),
            "allow_fake_embeddings": bool(getattr(settings, "LIVIA_ALLOW_FAKE_EMBEDDINGS", False)),
        },
        "integrations": {
            "smart360_dispatch_enabled": bool(getattr(settings, "SMART360_LEAD_DISPATCH_ENABLED", False)),
            "smart360_dispatch_dry_run": bool(getattr(settings, "SMART360_LEAD_DISPATCH_DRY_RUN", True)),
            "webhooks_enabled": bool(getattr(settings, "LIVIA_WEBHOOKS_ENABLED", False)),
            "webhooks_dry_run": bool(getattr(settings, "LIVIA_WEBHOOKS_DRY_RUN", True)),
            "handoff_notifications_enabled": bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_ENABLED", False)),
            "handoff_notifications_dry_run": bool(getattr(settings, "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN", True)),
        },
        "readiness": {
            "environment_status": env_status,
            "environment_checks": [
                {"ok": item.ok, "code": item.code, "detail": item.detail, "level": item.level}
                for item in env_checks
            ],
            "rag_vector_readiness": vector_checks,
        },
    }


def redact_sensitive_text(text: str) -> str:
    redacted = str(text or "")
    redacted = re.sub(r"(://[^:/@]+:)([^@/]+)(@)", r"\1***\3", redacted)
    for marker in ("sk-", "Bearer ", "token="):
        if marker in redacted:
            redacted = redacted.split(marker, 1)[0] + marker + "***"
    return redacted
