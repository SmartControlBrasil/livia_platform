from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from knowledge_base.models import TenantRagOperationRequest
from knowledge_base.rag.operations import _lease_seconds, operations_gate_status
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Diagnóstico somente leitura das operações RAG (fila, stale, gates)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="", help="Slug opcional para filtrar por tenant.")
        parser.add_argument("--json", action="store_true", help="Emitir JSON sanitizado.")

    def handle(self, *args, **options):
        tenant_slug = str(options.get("tenant") or "").strip()
        tenant = None
        if tenant_slug:
            tenant = Tenant.objects.filter(slug=tenant_slug).first()
            if tenant is None:
                raise CommandError("Tenant not found.")

        now = timezone.now()
        gate = operations_gate_status()
        base_qs = TenantRagOperationRequest.objects.all()
        if tenant is not None:
            base_qs = base_qs.filter(tenant=tenant)

        pending = base_qs.filter(status=TenantRagOperationRequest.Status.PENDING).count()
        running = base_qs.filter(status=TenantRagOperationRequest.Status.RUNNING).count()
        stale = base_qs.filter(
            status=TenantRagOperationRequest.Status.RUNNING,
            lease_expires_at__lt=now,
        ).count()
        recent_failed = base_qs.filter(
            status=TenantRagOperationRequest.Status.FAILED,
        ).order_by("-finished_at", "-id")[:5]
        last_completed = (
            base_qs.filter(
                status__in=[
                    TenantRagOperationRequest.Status.SUCCEEDED,
                    TenantRagOperationRequest.Status.PARTIAL,
                ]
            )
            .order_by("-finished_at", "-id")
            .first()
        )

        payload = {
            "tenant": tenant.slug if tenant else None,
            "gates": {
                "enabled": gate.enabled,
                "dry_run": gate.dry_run,
                "reason": gate.reason,
            },
            "lease_seconds": _lease_seconds(),
            "counts": {
                "pending": pending,
                "running": running,
                "stale_running": stale,
            },
            "last_completed": None,
            "recent_failures": [],
        }
        if last_completed is not None:
            payload["last_completed"] = {
                "id": last_completed.pk,
                "operation": last_completed.operation,
                "status": last_completed.status,
                "finished_at": last_completed.finished_at.isoformat() if last_completed.finished_at else None,
                "dry_run": last_completed.dry_run,
            }
        for item in recent_failed:
            payload["recent_failures"].append(
                {
                    "id": item.pk,
                    "operation": item.operation,
                    "error_code": item.error_code or "-",
                    "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                    "dry_run": item.dry_run,
                }
            )

        if options.get("json"):
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self._write_human(payload)

        if stale > 0:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"Atenção: {stale} execução(ões) running com lease expirado. "
                    "Execute recover via process_tenant_rag_operations --recover-stale-only."
                )
            )

    def _write_human(self, payload: dict) -> None:
        tenant_label = payload["tenant"] or "all_tenants"
        self.stdout.write(f"tenant={tenant_label}")
        gates = payload["gates"]
        self.stdout.write(
            f"gates enabled={gates['enabled']} dry_run={gates['dry_run']} reason={gates['reason'] or '-'}"
        )
        self.stdout.write(f"lease_seconds={payload['lease_seconds']}")
        counts = payload["counts"]
        self.stdout.write(
            "counts "
            f"pending={counts['pending']} running={counts['running']} stale_running={counts['stale_running']}"
        )
        last = payload["last_completed"]
        if last:
            self.stdout.write(
                "last_completed "
                f"id={last['id']} operation={last['operation']} status={last['status']} "
                f"finished_at={last['finished_at']} dry_run={last['dry_run']}"
            )
        else:
            self.stdout.write("last_completed none")
        self.stdout.write("recent_failures:")
        if not payload["recent_failures"]:
            self.stdout.write("  none")
        for item in payload["recent_failures"]:
            self.stdout.write(
                f"  id={item['id']} operation={item['operation']} code={item['error_code']} "
                f"finished_at={item['finished_at']} dry_run={item['dry_run']}"
            )
