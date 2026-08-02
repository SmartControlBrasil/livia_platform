#!/usr/bin/env bash
# Validação PostgreSQL local isolada — Fase 17 (sem secrets, sem bancos externos).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${LIVIA_PG_COMPOSE_FILE:-docker-compose.postgres.yml}"
DATABASE_URL="${DATABASE_URL:-postgresql://livia_local:livia_local_password@127.0.0.1:55432/livia_platform?sslmode=disable}"
PYTHON="${ROOT}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "ABORT: .venv/bin/python não encontrado."
  exit 1
fi

host="$( "$PYTHON" - <<'PY'
from urllib.parse import urlparse
import os
parsed = urlparse(os.environ["DATABASE_URL"])
print(parsed.hostname or "")
PY
)"
if [[ "$host" != "127.0.0.1" && "$host" != "localhost" && "$host" != "::1" ]]; then
  echo "ABORT: DATABASE_URL não aponta para host local ($host)."
  exit 1
fi

echo "== Ambiente PostgreSQL (sem senha) =="
DATABASE_URL="$DATABASE_URL" "$PYTHON" - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("SELECT version()")
    print("version:", cursor.fetchone()[0][:100])
    cursor.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
    row = cursor.fetchone()
    print("pgvector:", row[0] if row else "NOT INSTALLED")
    cursor.execute("SHOW timezone")
    print("timezone:", cursor.fetchone()[0])
cfg = connection.settings_dict
print("host:", cfg.get("HOST"))
print("port:", cfg.get("PORT"))
print("database:", cfg.get("NAME"))
print("user:", cfg.get("USER"))
print("engine:", cfg.get("ENGINE"))
print("test_database:", cfg.get("TEST", {}).get("NAME"))
PY

echo
echo "== Subindo PostgreSQL local (se necessário) =="
docker compose -f "$COMPOSE_FILE" up -d

echo
echo "== Migrations =="
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py makemigrations --check --dry-run
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py migrate --noinput

echo
echo "== Readiness =="
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py check
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py database_readiness || true
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py environment_readiness || true

echo
echo "== Suíte PostgreSQL =="
DATABASE_URL="$DATABASE_URL" "$PYTHON" manage.py test --keepdb --verbosity=1 2>&1 | tee /tmp/livia_pg_test_summary.txt
rg "^(Ran|OK|FAILED)" /tmp/livia_pg_test_summary.txt | tail -1

echo
echo "== Regressão SQLite =="
"$PYTHON" manage.py test --verbosity=0 2>&1 | rg "^(Ran|OK|FAILED)" | tail -1

echo
echo "Validação PostgreSQL concluída."
