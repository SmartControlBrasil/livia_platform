from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

POSTGRES_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
SQLITE_SCHEMES = {"sqlite", "sqlite3"}


def build_database_config(
    *,
    debug: bool,
    base_dir: Path,
    database_url: str = "",
    conn_max_age: int = 60,
    running_tests: bool = False,
    allow_external_test_database_url: bool = False,
) -> dict:
    raw_url = str(database_url or "").strip()
    allow_local_sqlite = debug or running_tests
    if not raw_url:
        if allow_local_sqlite:
            return _sqlite_config(base_dir)
        raise ImproperlyConfigured("DATABASE_URL is required when DEBUG=False. Production must not fall back to SQLite.")

    scheme = _database_scheme(raw_url)
    if running_tests:
        _validate_test_database_url(raw_url, allow_external_test_database_url)
    if scheme not in POSTGRES_SCHEMES | SQLITE_SCHEMES:
        raise ImproperlyConfigured("DATABASE_URL is invalid or uses an unsupported database scheme.")
    if not allow_local_sqlite and scheme in SQLITE_SCHEMES:
        raise ImproperlyConfigured("DATABASE_URL must point to PostgreSQL when DEBUG=False.")

    try:
        parsed = dj_database_url.parse(raw_url, conn_max_age=conn_max_age, conn_health_checks=True)
    except Exception as exc:  # pragma: no cover - defensive against parser changes
        raise ImproperlyConfigured("DATABASE_URL is invalid or unsupported.") from None

    engine = str(parsed.get("ENGINE", ""))
    if scheme in POSTGRES_SCHEMES and "postgresql" not in engine:
        raise ImproperlyConfigured("DATABASE_URL did not resolve to a PostgreSQL backend.")
    if not allow_local_sqlite and "postgresql" not in engine:
        raise ImproperlyConfigured("DATABASE_URL must point to PostgreSQL when DEBUG=False.")

    parsed["CONN_MAX_AGE"] = conn_max_age
    parsed["CONN_HEALTH_CHECKS"] = True
    if "postgresql" in engine and parsed.get("NAME"):
        parsed.setdefault("TEST", {})["NAME"] = f"test_{parsed['NAME']}"
    return {"default": parsed}


def parse_database_conn_max_age(value, *, default: int = 60) -> int:
    raw_value = str(value if value is not None else "").strip()
    if raw_value == "":
        return default
    try:
        conn_max_age = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("DATABASE_CONN_MAX_AGE must be a non-negative integer.") from None
    if conn_max_age < 0:
        raise ImproperlyConfigured("DATABASE_CONN_MAX_AGE must be a non-negative integer.")
    return conn_max_age


def is_running_tests(argv, env_value: str = "") -> bool:
    normalized = str(env_value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return len(argv) > 1 and argv[1] == "test"


def _sqlite_config(base_dir: Path) -> dict:
    return {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": base_dir / "db.sqlite3",
        }
    }


def _database_scheme(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
    except ValueError as exc:
        raise ImproperlyConfigured("DATABASE_URL is invalid or unsupported.") from None
    return (parsed.scheme or "").lower()


def _validate_test_database_url(raw_url: str, allow_external: bool) -> None:
    if allow_external:
        return
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ImproperlyConfigured("Refusing to run tests against a non-local DATABASE_URL. Use a local PostgreSQL test database.")
