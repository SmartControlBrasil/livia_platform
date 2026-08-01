#!/usr/bin/env python3
"""Fase 18 — soak com perfil staging (local/staging físico quando disponível)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE17 = ROOT / "scripts" / "phase17_staging_soak.py"

STAGING_DEFAULTS = {
    "LIVIA_ENVIRONMENT": "staging",
    "DJANGO_DEBUG": "True",
    "DJANGO_SECURE_SSL_REDIRECT": "False",
    "SMART360_LEAD_DISPATCH_DRY_RUN": "True",
    "SMART360_LEAD_DISPATCH_ENABLED": "False",
    "LIVIA_WEBHOOKS_DRY_RUN": "True",
    "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN": "True",
    "LIVIA_RAG_EMBEDDING_PROVIDER": "openai",
    "LIVIA_CHAT_RATE_LIMIT_ENABLED": "False",
}


def main() -> int:
    env = os.environ.copy()
    for key, value in STAGING_DEFAULTS.items():
        env.setdefault(key, value)
    if env.get("LIVIA_SOAK_BASE_URL", "").strip():
        target = ROOT / "scripts" / "phase19_http_soak.py"
    else:
        target = PHASE17
    result = subprocess.run(
        [sys.executable, str(target)],
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
