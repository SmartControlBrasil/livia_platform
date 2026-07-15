from django.core.management.base import BaseCommand, CommandError

from tenants.services.onboarding import TenantOnboardingService


class Command(BaseCommand):
    help = "Cria ou atualiza um tenant, profile da Lívia, knowledge inicial e snippet do widget."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--domain", required=True)
        parser.add_argument("--assistant-name", default="Lívia")
        parser.add_argument("--initial-message", default="Olá! Sou a Lívia. Como posso te ajudar?")
        parser.add_argument("--primary-goal", default="qualificar leads")
        parser.add_argument("--tone", default="consultivo, claro e profissional")
        parser.add_argument("--use-ai", action="store_true")
        parser.add_argument("--seed-knowledge", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            result = TenantOnboardingService().onboard(
                slug=options["slug"],
                name=options["name"],
                domain=options["domain"],
                assistant_name=options["assistant_name"],
                initial_message=options["initial_message"],
                primary_goal=options["primary_goal"],
                tone=options["tone"],
                use_ai=options["use_ai"],
                seed_knowledge=options["seed_knowledge"],
                dry_run=options["dry_run"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        mode = "DRY RUN - nenhuma alteração gravada" if options["dry_run"] else "Onboarding gravado"
        self.stdout.write(self.style.SUCCESS(mode))
        self.stdout.write(f"Tenant: {result.tenant.slug} ({'criado' if result.created_tenant else 'atualizado'})")
        self.stdout.write(f"AssistantProfile: {result.assistant_profile.name} ({'criado' if result.created_profile else 'atualizado'})")
        self.stdout.write(f"Knowledge criada: {result.created_knowledge_count}")
        self.stdout.write(f"Allowed origin: {result.allowed_origin}")

        if result.warnings:
            self.stdout.write("Alertas:")
            for warning in result.warnings:
                self.stdout.write(f"- {warning}")

        self.stdout.write("Snippet do widget:")
        self.stdout.write(result.widget_snippet)
