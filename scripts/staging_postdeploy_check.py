#!/usr/bin/env python3
"""Read-only staging postdeploy HTTP checks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tenants.services.staging_deployment import PILOT_TENANT_SLUG  # noqa: E402
from tenants.services.staging_postdeploy import DEFAULT_ORIGIN, run_postdeploy_checks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Staging postdeploy HTTP checks (read-only)")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant", default=PILOT_TENANT_SLUG)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    parser.add_argument("--chat-smoke", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run_postdeploy_checks(
        base_url=args.base_url,
        tenant=args.tenant,
        origin=args.origin,
        verify_tls=not args.insecure,
        chat_smoke=args.chat_smoke,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "summary": report.summary,
                    "checks": [{"code": i.code, "status": i.status, "detail": i.detail} for i in report.checks],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for item in report.checks:
            print(f"{item.status.ljust(4)} {item.code}: {item.detail}")
        print(f"\nSUMMARY: {report.summary}")

    if report.summary == "FAIL":
        return 2
    if report.summary == "WARN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
