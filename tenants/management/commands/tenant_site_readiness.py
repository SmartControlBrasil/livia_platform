import json

from django.core.management.base import BaseCommand, CommandError

from tenants.models import Tenant
from tenants.services.install_package import TenantInstallPackageService
from tenants.services.site_readiness import (
    SITE_READINESS_NOT_READY,
    SITE_READINESS_READY,
    SITE_READINESS_WARNING,
    site_readiness_has_blocking_errors,
)


class Command(BaseCommand):
    help = "Mostra a prontidão de instalação do widget em site para um tenant (somente leitura)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug público do tenant.")
        parser.add_argument("--json", action="store_true", help="Saída estruturada em JSON.")

    def handle(self, *args, **options):
        tenant_slug = str(options["tenant"] or "").strip()
        if not tenant_slug:
            raise CommandError("Informe --tenant=<slug>.")

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError(f"Tenant não encontrado: {tenant_slug}")

        package = TenantInstallPackageService().build_for_tenant(tenant)
        readiness = package.readiness
        if readiness is None:
            raise CommandError("Readiness indisponível.")

        if options["json"]:
            payload = {
                "tenant": tenant.slug,
                "name": tenant.name,
                "assistant_name": package.assistant_name,
                "readiness": readiness.to_dict(),
                "allowed_origins": package.allowed_origins,
                "snippet": package.snippet,
                "widget_src": package.widget_src,
                "api_url": package.api_url,
                "warnings": package.warnings,
            }
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"Tenant: {tenant.slug} ({tenant.name})")
            self.stdout.write(f"Assistente: {package.assistant_name}")
            self.stdout.write(f"Readiness: {readiness.overall_status}")
            self.stdout.write("")
            self.stdout.write("Checklist:")
            for check in readiness.checks:
                self.stdout.write(f"- [{check.status}] {check.code}: {check.message}")
                if check.action:
                    self.stdout.write(f"  Ação: {check.action}")
            self.stdout.write("")
            self.stdout.write("Origins autorizadas:")
            if package.allowed_origins:
                for origin in package.allowed_origins:
                    self.stdout.write(f"- {origin}")
            else:
                self.stdout.write("- (nenhuma origin ativa)")
            if package.warnings:
                self.stdout.write("")
                self.stdout.write("Alertas:")
                for warning in package.warnings:
                    self.stdout.write(f"- {warning}")
            self.stdout.write("")
            self.stdout.write("Snippet oficial:")
            self.stdout.write(package.snippet)

        if site_readiness_has_blocking_errors(readiness):
            raise CommandError(f"Tenant {tenant.slug} não está pronto para instalação ({SITE_READINESS_NOT_READY}).")

        if readiness.overall_status == SITE_READINESS_WARNING:
            self.stdout.write(self.style.WARNING(f"Tenant {tenant.slug} pronto com avisos ({SITE_READINESS_WARNING})."))
            return

        if not options["json"]:
            self.stdout.write(self.style.SUCCESS(f"Tenant {tenant.slug} pronto ({SITE_READINESS_READY})."))
