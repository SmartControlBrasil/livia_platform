from django.core.management.base import BaseCommand, CommandError

from knowledge_base.services.lifecycle import KnowledgeLifecycleService
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Mostra readiness/lifecycle de conhecimento para um tenant."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant.")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=str(options["tenant"] or "").strip()).first()
        if tenant is None:
            raise CommandError("Tenant not found.")
        readiness = KnowledgeLifecycleService().readiness(tenant=tenant)
        self.stdout.write(f"tenant={tenant.slug}")
        self.stdout.write(f"status={readiness.status}")
        self.stdout.write(f"documents_total={readiness.documents_total}")
        self.stdout.write(f"documents_enabled={readiness.documents_enabled}")
        self.stdout.write(f"documents_indexed={readiness.documents_indexed}")
        self.stdout.write(f"documents_stale={readiness.documents_stale}")
        self.stdout.write(f"documents_failed={readiness.documents_failed}")
        self.stdout.write(f"documents_disabled={readiness.documents_disabled}")
        self.stdout.write(f"chunks_active={readiness.chunks_active}")
        self.stdout.write(f"embeddings_active={readiness.embeddings_active}")
        self.stdout.write(f"detail={readiness.detail}")
