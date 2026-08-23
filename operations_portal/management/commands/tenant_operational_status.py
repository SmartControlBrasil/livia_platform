import json

from django.core.management.base import BaseCommand, CommandError

from operations_portal.operational_readiness import TenantOperationalReadinessService
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Mostra o readiness operacional consolidado por tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", dest="tenant_slug", help="Slug do tenant.")
        parser.add_argument("--all", action="store_true", dest="all_tenants", help="Lista todos os tenants.")
        parser.add_argument("--json", action="store_true", dest="as_json", help="Emite JSON sem detalhes sensíveis.")

    def handle(self, *args, **options):
        tenant_slug = options.get("tenant_slug")
        all_tenants = options.get("all_tenants")
        if bool(tenant_slug) == bool(all_tenants):
            raise CommandError("Informe exatamente uma opção: --tenant <slug> ou --all.")
        queryset = Tenant.objects.all().order_by("slug")
        if tenant_slug:
            queryset = queryset.filter(slug=tenant_slug)
            if not queryset.exists():
                raise CommandError(f"Tenant não encontrado: {tenant_slug}")
        service = TenantOperationalReadinessService()
        statuses = [service.for_tenant(tenant) for tenant in queryset]
        if options.get("as_json"):
            self.stdout.write(json.dumps([status.as_dict() for status in statuses], ensure_ascii=False, indent=2))
            return
        for status in statuses:
            self.stdout.write(
                f"{status.tenant.slug}: {status.status} "
                f"site={status.site.status} knowledge={status.knowledge.status} "
                f"commercial={status.commercial.status} integrations={status.integrations.status} "
                f"outbox={status.outbox.status} chat={status.chat.status} warnings={len(status.warnings)}"
            )
