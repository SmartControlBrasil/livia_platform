from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.management.base import BaseCommand, CommandError

from tenants.models import Tenant
from tenants.services.onboarding import TenantOnboardingService


class Command(BaseCommand):
    help = "Cria ou atualiza um tenant, profile da Lívia, knowledge inicial e snippet do widget."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--domain", required=True)
        parser.add_argument("--assistant-name", default="")
        parser.add_argument("--initial-message", default="")
        parser.add_argument("--primary-goal", default="")
        parser.add_argument("--tone", default="")
        parser.add_argument("--use-ai", action="store_true")
        parser.add_argument("--widget-title", default=None)
        parser.add_argument("--launcher-label", default="")
        parser.add_argument("--primary-color", default="")
        parser.add_argument("--position", default="")
        parser.add_argument("--placeholder-text", default="")
        parser.add_argument("--disable-widget", action="store_true")
        parser.add_argument("--widget-enabled", action="store_true")
        parser.add_argument("--seed-knowledge", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica alterações no banco. Sem --apply, use --dry-run para simulação.",
        )
        parser.add_argument(
            "--allow-update-existing",
            action="store_true",
            help="Permite atualizar tenant existente quando usado com --apply.",
        )
        parser.add_argument("--allowed-origin", action="append", dest="allowed_origins", default=[])

    def handle(self, *args, **options):
        if options["dry_run"] and options["apply"]:
            raise CommandError("Use apenas um modo: --dry-run ou --apply.")
        if not options["dry_run"] and not options["apply"]:
            raise CommandError("Modo explícito obrigatório: use --dry-run para simular ou --apply para gravar.")
        if options["disable_widget"] and options["widget_enabled"]:
            raise CommandError("Use apenas uma opção de widget: --disable-widget ou --widget-enabled.")

        existing_tenant = Tenant.objects.filter(slug=options["slug"]).first()
        if existing_tenant is not None and options["apply"] and not options["allow_update_existing"]:
            raise CommandError(
                "Tenant já existe. Use --allow-update-existing para permitir atualização explícita."
            )

        existing_profile = None
        if existing_tenant is not None:
            try:
                existing_profile = existing_tenant.assistant_profile
            except ObjectDoesNotExist:
                existing_profile = None

        assistant_name = options["assistant_name"] or (existing_profile.name if existing_profile else "Lívia")
        initial_message = options["initial_message"] or (
            existing_profile.initial_message if existing_profile else "Olá! Sou a Lívia. Como posso te ajudar?"
        )
        primary_goal = options["primary_goal"] or (
            existing_profile.primary_goal if existing_profile else "qualificar leads"
        )
        tone = options["tone"] or (
            existing_profile.tone if existing_profile else "consultivo, claro e profissional"
        )
        widget_title = options["widget_title"]
        if widget_title is None:
            widget_title = existing_profile.widget_title if existing_profile else ""
        launcher_label = options["launcher_label"] or (
            existing_profile.launcher_label if existing_profile else "Fale com a Lívia"
        )
        primary_color = options["primary_color"] or (
            existing_profile.primary_color if existing_profile else "#2563eb"
        )
        position = options["position"] or (
            existing_profile.position if existing_profile else "bottom_right"
        )
        placeholder_text = options["placeholder_text"] or (
            existing_profile.placeholder_text if existing_profile else "Digite sua mensagem..."
        )
        use_ai = options["use_ai"] or (existing_profile.use_ai if existing_profile else False)
        if options["disable_widget"]:
            widget_enabled = False
        elif options["widget_enabled"]:
            widget_enabled = True
        elif existing_profile is not None:
            widget_enabled = existing_profile.is_widget_enabled
        else:
            widget_enabled = True

        try:
            result = TenantOnboardingService().onboard(
                slug=options["slug"],
                name=options["name"],
                domain=options["domain"],
                assistant_name=assistant_name,
                initial_message=initial_message,
                primary_goal=primary_goal,
                tone=tone,
                use_ai=use_ai,
                widget_title=widget_title,
                launcher_label=launcher_label,
                primary_color=primary_color,
                position=position,
                placeholder_text=placeholder_text,
                widget_enabled=widget_enabled,
                seed_knowledge=options["seed_knowledge"],
                dry_run=options["dry_run"],
                allowed_origins=options["allowed_origins"],
            )
        except (ValueError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        mode = "DRY RUN - nenhuma alteração gravada" if options["dry_run"] else "Onboarding aplicado"
        self.stdout.write(self.style.SUCCESS(mode))
        self.stdout.write(f"Tenant: {result.tenant.slug} ({'criado' if result.created_tenant else 'mantido/atualizado'})")
        self.stdout.write(
            f"AssistantProfile: {result.assistant_profile.name} ({'criado' if result.created_profile else 'mantido/atualizado'})"
        )
        self.stdout.write(f"Knowledge criada: {result.created_knowledge_count}")
        self.stdout.write(f"Allowed origin: {result.allowed_origin}")
        if result.allowed_origins:
            self.stdout.write("Origins autorizadas:")
            for origin in result.allowed_origins:
                self.stdout.write(f"- {origin}")
        self.stdout.write(f"Widget: {'ativo' if result.assistant_profile.is_widget_enabled else 'inativo'}")

        if result.warnings:
            self.stdout.write("Alertas:")
            for warning in result.warnings:
                self.stdout.write(f"- {warning}")

        self.stdout.write("Snippet do widget:")
        self.stdout.write(result.widget_snippet)
