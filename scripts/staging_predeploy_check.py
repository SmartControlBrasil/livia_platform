#!/usr/bin/env python3
"""Read-only staging predeploy gate — não destrutivo."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from tenants.services.staging_deployment import (  # noqa: E402
    PILOT_TENANT_SLUG,
    run_predeploy_checks,
)


def _print_report(report) -> None:
    for item in report.checks:
        prefix = item.status.ljust(4)
        print(f"{prefix} {item.code}: {item.detail}")
    print("")
    print(f"SUMMARY: {report.summary_status()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Staging predeploy read-only checks")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-git", action="store_true")
    parser.add_argument("--tenant", default=PILOT_TENANT_SLUG)
    parser.add_argument("--skip-django", action="store_true", help="Skip DB/migrations/readiness checks")
    args = parser.parse_args()

    report = run_predeploy_checks(
        project_root=ROOT,
        env_path=Path(args.env_file),
        allow_dirty=args.allow_dirty,
        pilot_tenant=args.tenant,
        skip_git=args.skip_git,
        django_checks=not args.skip_django,
    )
    _print_report(report)

    if report.has_failures:
        return 2
    if report.has_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
