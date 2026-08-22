from django.core.management.base import BaseCommand

from tenants.models import AssistantProfile, Tenant


INITIAL_TENANTS = (
    {
        "name": "Smart Control Brasil",
        "slug": "smart-control-brasil",
        "domain": "smartcontrolbrasil.com.br",
        "assistant_name": "Lívia",
        "initial_message": "Olá! Sou a Lívia da Smart Control Brasil. Como posso ajudar?",
        "tone": "consultivo, claro e profissional",
        "primary_goal": "qualificar oportunidades comerciais e técnicas",
        "business_domain": "engenharia aplicada, automação industrial, robótica, manutenção técnica, sistemas web e agentes de IA",
        "short_description": "Atendimento consultivo para entender processos, máquinas, operações técnicas e projetos digitais antes de direcionar a solução.",
    },
    {
        "name": "Granimármores Pitondo",
        "slug": "granimarmores-pitondo",
        "domain": "granimarmorespitondo.com.br",
        "assistant_name": "Lívia",
        "initial_message": "Olá! Sou a Lívia da Granimármores Pitondo. Como posso ajudar?",
        "tone": "consultivo, cordial e objetivo",
        "primary_goal": "qualificar solicitações de atendimento e orçamento",
        "business_domain": "marmoraria, pedras naturais, bancadas, pias, cubas, cooktops, banheiros, escadas e áreas gourmet",
        "short_description": "Atendimento consultivo para avaliar projetos sob medida, solicitar medidas, fotos, planta e detalhes do ambiente antes do orçamento.",
    },
)


class Command(BaseCommand):
    help = "Cria ou atualiza tenants iniciais e seus perfis da Lívia."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        for item in INITIAL_TENANTS:
            tenant, created = Tenant.objects.update_or_create(
                slug=item["slug"],
                defaults={
                    "name": item["name"],
                    "domain": item["domain"],
                    "is_active": True,
                },
            )
            AssistantProfile.objects.update_or_create(
                tenant=tenant,
                defaults={
                    "name": item["assistant_name"],
                    "initial_message": item["initial_message"],
                    "tone": item["tone"],
                    "primary_goal": item["primary_goal"],
                    "business_domain": item.get("business_domain", ""),
                    "short_description": item.get("short_description", ""),
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Tenants iniciais sincronizados. Criados: {created_count}. Atualizados: {updated_count}."
            )
        )
