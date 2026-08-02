from django.core.management.base import BaseCommand

from knowledge_base.rag.operational_work_queue import PRIORITY_LABELS, build_work_queue_summary
from operations_portal.work_queue_services import get_tenant_work_queue
from tenants.models import Tenant


class Command(BaseCommand):
    help = "Relatório da fila operacional tenant-scoped."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Slug do tenant")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options["tenant"]).first()
        if tenant is None:
            self.stderr.write(self.style.ERROR(f"Tenant não encontrado: {options['tenant']}"))
            return

        summary = build_work_queue_summary(tenant=tenant)
        page, _ = get_tenant_work_queue(tenant=tenant, page_number=1)
        items = page.object_list[:20]

        if options["json"]:
            import json

            payload = {
                "tenant": tenant.slug,
                "summary": summary,
                "top_items": [
                    {
                        "id": item["id"],
                        "priority": item["priority"],
                        "title": item["title"],
                        "assignee": item.get("assignee_username"),
                        "escalation_level": item.get("escalation_level"),
                    }
                    for item in items
                ],
            }
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        self.stdout.write(f"Fila operacional — tenant={tenant.slug}")
        self.stdout.write(f"P1 abertos: {summary['p1_open']}")
        self.stdout.write(f"P2 abertos: {summary['p2_open']}")
        self.stdout.write(f"Sem responsável: {summary['unassigned']}")
        self.stdout.write(f"ACK SLA vencido: {summary['ack_sla_breached']}")
        self.stdout.write(f"Resolução SLA vencido: {summary['resolution_sla_breached']}")
        self.stdout.write(f"Escalonados: {summary['escalated']}")
        self.stdout.write("Top da fila:")
        for item in items:
            label = PRIORITY_LABELS.get(item["priority"], item["priority"])
            self.stdout.write(f"  - [{label}] #{item['id']} {item['title']} ({item.get('assignee_username') or 'sem responsável'})")
