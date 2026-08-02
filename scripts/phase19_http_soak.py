#!/usr/bin/env python3
"""Fase 19B — soak HTTP/HTTPS real contra host de staging físico."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("LIVIA_ALLOW_ORIGINLESS_PUBLIC_API", "False")
os.environ.setdefault("LIVIA_CHAT_RATE_LIMIT_ENABLED", "False")
django.setup()

from django.conf import settings  # noqa: E402

from phase17_staging_soak import GP_ORIGIN, GP_SLUG, run_soak  # noqa: E402
from soak_chat_backend import HttpSoakChatBackend  # noqa: E402

STAGING_DEFAULTS = {
    "LIVIA_ENVIRONMENT": "staging",
    "SMART360_LEAD_DISPATCH_DRY_RUN": "True",
    "SMART360_LEAD_DISPATCH_ENABLED": "False",
    "LIVIA_WEBHOOKS_DRY_RUN": "True",
    "LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN": "True",
    "LIVIA_RAG_EMBEDDING_PROVIDER": "openai",
}


def main() -> int:
    for key, value in STAGING_DEFAULTS.items():
        os.environ.setdefault(key, value)

    base_url = os.environ.get("LIVIA_SOAK_BASE_URL", "").strip()
    if not base_url:
        print("NOT EXECUTED: defina LIVIA_SOAK_BASE_URL (ex.: https://staging-livia.smartcontrolbrasil.com.br)")
        return 2

    origin = os.environ.get("LIVIA_SOAK_ORIGIN", GP_ORIGIN).strip()
    verify_tls = os.environ.get("LIVIA_SOAK_VERIFY_TLS", "true").lower() not in {"0", "false", "no"}
    timeout = float(os.environ.get("LIVIA_SOAK_TIMEOUT_SECONDS", "120"))

    import phase17_staging_soak as phase17_module  # noqa: E402

    phase17_module.GP_ORIGIN = origin

    print("=== FASE 19B SOAK (HTTP físico) ===")
    print(f"base_url: {base_url}")
    print(f"origin: {origin}")
    print(f"verify_tls: {verify_tls}")
    print(f"LIVIA_ENVIRONMENT: {getattr(settings, 'LIVIA_ENVIRONMENT', '')}")

    backend = HttpSoakChatBackend(base_url=base_url, timeout_seconds=timeout, verify_tls=verify_tls)
    report = run_soak(backend, mode="physical-http", db_inspection=False)

    print(f"\nInterações: {len(report.interactions)}")
    print(f"Latência ms: {report.latency_ms}")
    grounded = sum(1 for row in report.interactions if row.ai_mode == "grounded")
    print(f"grounded_used: {grounded}/{len(report.interactions)}")

    if report.failures:
        print("\nFALHAS:")
        for item in report.failures:
            print(f"  - {item}")
    else:
        print("\nFalhas críticas: 0")

    out_path = ROOT / "docs" / "phase19b_http_soak_results.json"
    out_path.write_text(
        json.dumps(
            {
                "summary": {
                    "mode": report.mode,
                    "base_url": base_url,
                    "origin": origin,
                    "tenant": GP_SLUG,
                    "interactions": len(report.interactions),
                    "failures": report.failures,
                    "latency_ms": report.latency_ms,
                    "grounded": grounded,
                },
                "interactions": [
                    {
                        "case_id": row.case_id,
                        "http_status": row.http_status,
                        "ai_mode": row.ai_mode,
                        "retrieval": row.retrieval,
                        "latency_ms": row.latency_ms,
                        "notes": row.notes,
                    }
                    for row in report.interactions
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nJSON: {out_path}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
