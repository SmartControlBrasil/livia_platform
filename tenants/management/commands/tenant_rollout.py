import json

from django.core.management.base import BaseCommand, CommandError

from tenants.models import Tenant
from tenants.services.rollout import TenantRolloutService, TenantRolloutSpec


class Command(BaseCommand):
    help = "Planeja rollout de instalação por tenant sem deploy remoto."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")
        parser.add_argument("--origin", required=True, help="Origin alvo exata, sem path/query/fragment.")
        parser.add_argument("--environment", choices=["staging", "production"], default="staging")
        parser.add_argument("--dry-run", action="store_true", default=True, help="Mantém o rollout como planejamento sem alteração remota.")
        parser.add_argument("--apply", action="store_true", help="Registra planejamento interno/audit; não faz deploy remoto.")
        parser.add_argument("--allow-widget-disabled", action="store_true")
        parser.add_argument("--allow-knowledge-warning", action="store_true")
        parser.add_argument("--smoke-local", action="store_true", help="Executa smoke local seguro via Django test client com rollback.")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"] or "").strip()).first()
        if tenant is None:
            raise CommandError(f"Tenant não encontrado: {options['tenant']}")
        spec = TenantRolloutSpec(
            tenant=tenant,
            target_origin=options["origin"],
            environment=options["environment"],
            dry_run=True,
            allow_widget_disabled=bool(options.get("allow_widget_disabled")),
            allow_knowledge_warning=bool(options.get("allow_knowledge_warning")),
        )
        try:
            result = TenantRolloutService().build(
                spec,
                run_smoke=bool(options.get("smoke_local")),
                record_audit=bool(options.get("apply") or options.get("smoke_local")),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options.get("as_json"):
            self.stdout.write(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"Tenant .............. {result.tenant.slug}")
            self.stdout.write(f"Environment ......... {result.environment}")
            self.stdout.write(f"Operational ......... {result.operational_status}")
            self.stdout.write(f"Origin .............. {'VALID' if result.origin_valid else 'INVALID'}")
            self.stdout.write(f"Install package ..... {result.install_package_status}")
            self.stdout.write(f"Side effects ........ {'SAFE' if result.side_effects_safe else 'BLOCKED'}")
            self.stdout.write(f"Smoke plan .......... {'READY' if result.smoke_plan else 'MISSING'}")
            if result.smoke_result:
                self.stdout.write(f"Smoke local ......... {'PASS' if result.smoke_result.ok else 'FAIL'}")
            self.stdout.write(f"Rollout ............. {result.status}")
            if result.blocking_checks:
                self.stdout.write("")
                self.stdout.write("Bloqueios:")
                for check in result.blocking_checks:
                    self.stdout.write(f"- {check.code}: {check.detail}")
            self.stdout.write("")
            self.stdout.write("Snippet oficial:")
            self.stdout.write(result.install_plan.snippet)
        if result.status in {"BLOCKED", "FAILED"}:
            raise CommandError(f"Rollout {result.status}: {tenant.slug}")
