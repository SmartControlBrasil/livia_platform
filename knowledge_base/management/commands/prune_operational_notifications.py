from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from knowledge_base.models import TenantOperationalNotification


class Command(BaseCommand):
    help = "Remove notificações operacionais antigas (retenção configurável)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", dest="tenant_slug", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        read_days = int(getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_RETENTION_DAYS", 90) or 90)
        failed_days = int(getattr(settings, "LIVIA_OPERATIONAL_NOTIFICATION_FAILED_RETENTION_DAYS", 180) or 180)
        tenant_slug = str(options.get("tenant_slug") or "").strip()

        read_cutoff = now - timezone.timedelta(days=max(read_days, 1))
        failed_cutoff = now - timezone.timedelta(days=max(failed_days, 1))

        read_qs = TenantOperationalNotification.objects.filter(
            status=TenantOperationalNotification.Status.READ,
            read_at__lt=read_cutoff,
        )
        failed_qs = TenantOperationalNotification.objects.filter(
            status=TenantOperationalNotification.Status.FAILED,
            failed_at__lt=failed_cutoff,
        )
        if tenant_slug:
            read_qs = read_qs.filter(tenant__slug=tenant_slug)
            failed_qs = failed_qs.filter(tenant__slug=tenant_slug)

        read_count = read_qs.count()
        failed_count = failed_qs.count()
        if options.get("dry_run"):
            self.stdout.write(f"dry-run read={read_count} failed={failed_count}")
            return
        deleted = 0
        deleted += read_qs.delete()[0]
        deleted += failed_qs.delete()[0]
        self.stdout.write(f"deleted={deleted}")
