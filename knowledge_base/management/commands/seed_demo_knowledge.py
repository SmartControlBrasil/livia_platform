from django.core.management.base import BaseCommand

from knowledge_base.models import KnowledgeDocument
from tenants.models import Tenant


DEMO_DOCUMENTS = (
    {
        "slug": "smart-control-brasil",
        "title": "Smart Control Brasil",
        "content": "A Smart Control Brasil atua com engenharia aplicada, automação industrial, robótica, dados, sistemas web e agentes de IA. O atendimento deve começar pelo diagnóstico do ambiente e da necessidade real antes de sugerir solução.",
        "tags": ["smart-control", "engenharia", "diagnostico"],
    },
    {
        "slug": "automacao-mitsubishi",
        "title": "Automação Mitsubishi",
        "content": "A Smart Control trabalha com automação industrial envolvendo CLPs, IHMs, inversores, servos, painéis, retrofit e integração de máquinas. Mitsubishi é tratado como frente técnica de automação, sempre com análise da aplicação antes de proposta.",
        "tags": ["automation", "mitsubishi", "clp", "ihm", "inversor", "servo", "retrofit"],
    },
    {
        "slug": "xyron-robotics",
        "title": "Xyron Robotics",
        "content": "Xyron Robotics reúne soluções de robótica para ambientes profissionais, educação, recepção, segurança, limpeza e atendimento. A indicação correta depende do ambiente, fluxo de pessoas, operação e objetivo do projeto.",
        "tags": ["robotics", "xyron", "robo", "robotica"],
    },
    {
        "slug": "hygibot-robo-limpeza",
        "title": "HygiBot / robô de limpeza",
        "content": "HygiBot é tratado como solução de robótica de limpeza para grandes áreas e ambientes profissionais. A conversa deve levantar tipo de piso, área aproximada, rotina de limpeza e contexto operacional antes de avançar para proposta.",
        "tags": ["robotics", "xyron", "hygibot", "limpeza", "robo"],
    },
    {
        "slug": "manutencao-academias",
        "title": "Manutenção técnica para academias",
        "content": "A Smart Control pode orientar demandas de manutenção técnica em equipamentos de academia, como esteiras e bikes. A triagem deve perguntar se o equipamento é residencial ou profissional, qual sintoma ocorre e se há urgência operacional.",
        "tags": ["maintenance", "manutencao", "academia", "esteira", "equipamento"],
    },
    {
        "slug": "sistemas-web-agentes-ia",
        "title": "Sistemas web e agentes de IA",
        "content": "A frente de software envolve sistemas web, dashboards, integrações, portais e agentes de IA para atendimento ou operação. A Lívia deve entender o processo atual, usuários envolvidos, integrações desejadas e objetivo de negócio.",
        "tags": ["software_web", "software", "sistema", "dashboard", "ia", "agente"],
    },
    {
        "slug": "livia-atlas",
        "title": "Lívia e Atlas",
        "content": "Lívia é a assistente consultiva para atendimento e qualificação inicial. Atlas é tratado como agente de apoio mais técnico ou operacional em fluxos internos. Ambos devem responder com segurança e sem inventar preço, prazo ou garantia.",
        "tags": ["software_web", "livia", "atlas", "agente", "ia"],
    },
)


class Command(BaseCommand):
    help = "Cria documentos demonstrativos de knowledge base para o tenant Smart Control Brasil."

    def handle(self, *args, **options):
        tenant, _ = Tenant.objects.update_or_create(
            slug="smart-control-brasil",
            defaults={
                "name": "Smart Control Brasil",
                "domain": "smartcontrolbrasil.com.br",
                "is_active": True,
            },
        )
        created_count = 0
        updated_count = 0
        for document in DEMO_DOCUMENTS:
            _, created = KnowledgeDocument.objects.update_or_create(
                tenant=tenant,
                slug=document["slug"],
                defaults={
                    "title": document["title"],
                    "content": document["content"],
                    "source_type": "seed",
                    "source_url": "",
                    "tags": document["tags"],
                    "status": KnowledgeDocument.Status.ACTIVE,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Knowledge demo sincronizada para {tenant.slug}. Criados: {created_count}. Atualizados: {updated_count}."
            )
        )
