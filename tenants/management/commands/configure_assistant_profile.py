from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from audit.models import ACTION_ASSISTANT_PROFILE_UPDATED
from audit.services import audit_model_snapshot, changed_fields, record_audit_event
from tenants.models import AssistantProfile, Tenant


class Command(BaseCommand):
    help = "Configura perfil conversacional do tenant (identidade e grounded synthesis)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--assistant-name", default="")
        parser.add_argument("--business-name", default="")
        parser.add_argument("--business-domain", default="")
        parser.add_argument("--short-description", default="")
        parser.add_argument("--tone", default="")
        parser.add_argument("--enable-grounded-synthesis", action="store_true")
        parser.add_argument("--disable-grounded-synthesis", action="store_true")
        parser.add_argument("--enable-ai", action="store_true")
        parser.add_argument("--disable-ai", action="store_true")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"]).strip()).first()
        if tenant is None:
            raise CommandError("Tenant not found.")
        profile = AssistantProfile.objects.filter(tenant=tenant).first()
        if profile is None:
            raise CommandError("Assistant profile not found for tenant.")

        if options["enable_grounded_synthesis"] and options["disable_grounded_synthesis"]:
            raise CommandError("Use only one of --enable-grounded-synthesis or --disable-grounded-synthesis.")
        if options["enable_ai"] and options["disable_ai"]:
            raise CommandError("Use only one of --enable-ai or --disable-ai.")

        before = audit_model_snapshot(
            profile,
            fields=[
                "name",
                "business_name",
                "business_domain",
                "short_description",
                "tone",
                "use_ai",
                "grounded_synthesis_enabled",
            ],
        )

        if options["assistant_name"]:
            profile.name = str(options["assistant_name"]).strip()
        if options["business_name"]:
            profile.business_name = str(options["business_name"]).strip()
        if options["business_domain"]:
            profile.business_domain = str(options["business_domain"]).strip()
        if options["short_description"]:
            profile.short_description = str(options["short_description"]).strip()
        if options["tone"]:
            profile.tone = str(options["tone"]).strip()
        if options["enable_grounded_synthesis"]:
            profile.grounded_synthesis_enabled = True
        elif options["disable_grounded_synthesis"]:
            profile.grounded_synthesis_enabled = False
        if options["enable_ai"]:
            profile.use_ai = True
        elif options["disable_ai"]:
            profile.use_ai = False

        profile.save()
        after = audit_model_snapshot(
            profile,
            fields=list(before.keys()),
        )
        changes = changed_fields(before, after)
        record_audit_event(
            action=ACTION_ASSISTANT_PROFILE_UPDATED,
            tenant=tenant,
            object_type="tenants.assistantprofile",
            object_id=str(profile.pk),
            object_repr=f"{tenant.slug} / {profile.name}",
            before_data=changes["before"],
            after_data=changes["after"],
            metadata={"source": "management_command.configure_assistant_profile"},
        )

        self.stdout.write(self.style.SUCCESS("Assistant profile updated."))
        self.stdout.write(f"tenant={tenant.slug}")
        self.stdout.write(f"assistant_name={profile.name}")
        self.stdout.write(f"business_name={profile.effective_business_name}")
        self.stdout.write(f"business_domain={profile.business_domain}")
        self.stdout.write(f"use_ai={profile.use_ai}")
        self.stdout.write(f"grounded_synthesis_enabled={profile.grounded_synthesis_enabled}")
