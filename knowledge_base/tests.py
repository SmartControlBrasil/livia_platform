from django.core.management import call_command
from django.test import TestCase

from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration, TenantRagDocumentChunk, TenantRagDriveFileManifest, TenantRagDriveTextStaging
from knowledge_base.rag.context_builder import build_knowledge_context
from knowledge_base.rag.retriever import retrieve_relevant_knowledge
from tenants.models import Tenant


class KnowledgeRetrievalTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smartcontrolbrasil.com.br",
        )
        self.other_tenant = Tenant.objects.create(
            name="Outro Tenant",
            slug="outro-tenant",
            domain="outro.example",
        )
        self.hygibot = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="HygiBot / robô de limpeza",
            slug="hygibot-robo-limpeza",
            content="HygiBot é uma solução de robótica de limpeza para grandes áreas e ambientes profissionais.",
            tags=["robotics", "xyron", "hygibot", "limpeza", "robo"],
        )
        self.mitsubishi = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Automação Mitsubishi",
            slug="automacao-mitsubishi",
            content="Automação industrial com CLPs, IHMs, inversores, servos e retrofit de máquinas.",
            tags=["automation", "mitsubishi", "clp", "ihm"],
        )
        self.maintenance = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Manutenção técnica para academias",
            slug="manutencao-academias",
            content="Triagem de manutenção para esteiras e equipamentos profissionais de academia.",
            tags=["maintenance", "manutencao", "academia", "esteira"],
        )
        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="Documento inativo",
            slug="documento-inativo",
            content="HygiBot e robô de limpeza não devem aparecer quando arquivado.",
            tags=["hygibot", "limpeza"],
            status=KnowledgeDocument.Status.ARCHIVED,
        )
        KnowledgeDocument.objects.create(
            tenant=self.other_tenant,
            title="HygiBot de outro tenant",
            slug="hygibot-outro-tenant",
            content="Conteúdo de outro tenant sobre robô de limpeza.",
            tags=["hygibot", "limpeza"],
        )

    def test_active_document_is_retrieved_for_correct_tenant(self):
        results = retrieve_relevant_knowledge(self.tenant, "robô de limpeza", service_area="robotics")

        self.assertTrue(any(result.title == self.hygibot.title for result in results))

    def test_inactive_document_is_not_retrieved(self):
        results = retrieve_relevant_knowledge(self.tenant, "documento inativo hygibot limpeza")

        self.assertFalse(any(result.title == "Documento inativo" for result in results))

    def test_other_tenant_document_is_not_retrieved(self):
        results = retrieve_relevant_knowledge(self.tenant, "HygiBot de outro tenant")

        self.assertFalse(any(result.title == "HygiBot de outro tenant" for result in results))

    def test_cleaning_robot_query_returns_hygibot_or_xyron(self):
        results = retrieve_relevant_knowledge(self.tenant, "Vocês têm robô de limpeza?", service_area="robotics")

        titles = [result.title for result in results]
        self.assertIn("HygiBot / robô de limpeza", titles)

    def test_clp_mitsubishi_query_returns_automation(self):
        results = retrieve_relevant_knowledge(self.tenant, "CLP Mitsubishi", service_area="automation")

        self.assertEqual(results[0].title, "Automação Mitsubishi")

    def test_gym_treadmill_query_returns_maintenance(self):
        results = retrieve_relevant_knowledge(self.tenant, "esteira academia", service_area="maintenance")

        self.assertEqual(results[0].title, "Manutenção técnica para academias")

    def test_empty_result_does_not_break(self):
        results = retrieve_relevant_knowledge(self.tenant, "assunto totalmente distante")

        self.assertEqual(results, [])

    def test_build_knowledge_context_returns_short_text(self):
        context = build_knowledge_context(self.tenant, "robô de limpeza", service_area="robotics")

        self.assertIn("Base de conhecimento encontrada", context)
        self.assertIn("HygiBot", context)
        self.assertLess(len(context), 900)

    def test_seed_demo_knowledge_is_idempotent(self):
        call_command("seed_demo_knowledge", verbosity=0)
        first_count = KnowledgeDocument.objects.filter(tenant__slug="smart-control-brasil").count()

        call_command("seed_demo_knowledge", verbosity=0)
        second_count = KnowledgeDocument.objects.filter(tenant__slug="smart-control-brasil").count()

        self.assertEqual(first_count, second_count)
        self.assertGreaterEqual(second_count, 7)

    def test_retriever_does_not_use_rag_staging_or_chunks(self):
        config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            sync_enabled=True,
        )
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=config,
            drive_file_id="doc-private",
            name="Documento Privado RAG",
            mime_type="application/vnd.google-apps.document",
            relative_path="Documento Privado RAG",
            normalized_text_sha256="f" * 64,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            normalized_text="conteudo privado rag em staging",
            normalized_text_sha256="f" * 64,
            normalized_text_char_count=31,
            normalized_text_byte_count=31,
            exported_at=self.hygibot.updated_at,
        )
        TenantRagDocumentChunk.objects.create(
            tenant=self.tenant,
            manifest=manifest,
            staging=staging,
            ordinal=0,
            chunk_text="conteudo privado rag em chunk",
            chunk_sha256="a" * 64,
            source_text_sha256="f" * 64,
            chunk_config_signature="b" * 64,
            char_count=28,
            byte_count=28,
            start_char=0,
            end_char=28,
        )

        results = retrieve_relevant_knowledge(self.tenant, "conteudo privado rag em chunk", service_area="robotics")

        self.assertFalse(any("Privado RAG" in result.title for result in results))
