from __future__ import annotations

from django.test import TestCase, override_settings

from assistant_core.dialogue_memory import (
    DialogueMemory,
    build_contextual_retrieval_query,
    update_dialogue_memory_from_turn,
)
from assistant_core.services.deterministic_synthesis import synthesize_deterministic_reply
from assistant_core.services.response_quality_gate import apply_response_quality_gate
from knowledge_base.models import KnowledgeDocument, RagRetrievalEvent, TenantRagConfiguration
from knowledge_base.rag.entity_catalog import entity_catalog_for_tenant, resolve_knowledge_entity
from knowledge_base.rag.embeddings import FakeEmbeddingProvider, load_embedding_config
from knowledge_base.rag.indexing import run_index_for_tenant
from knowledge_base.rag.conversation_retrieval import retrieve_context
from knowledge_base.rag.sync import run_chunk_build_for_tenant
from knowledge_base.services.manual_rag import sync_manual_knowledge_document_to_rag
from knowledge_base.testing.rag_dimensions import RagTestDimensionMixin
from tenants.models import Tenant


@override_settings(
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=False,
    LIVIA_RAG_INDEXING_ENABLED=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_EMBEDDING_MODEL="fake-embed-v1",
    LIVIA_RAG_EMBEDDING_BATCH_SIZE=4,
    LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS=5,
    LIVIA_RAG_EMBEDDING_MAX_RETRIES=0,
    LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS=0,
    LIVIA_RAG_MIN_SIMILARITY_SCORE=0.10,
    LIVIA_RAG_MAX_RETRIEVED_CHUNKS=3,
    LIVIA_RAG_MAX_CONTEXT_CHARS=1200,
    LIVIA_RAG_MAX_CHUNKS_PER_MANIFEST=2,
)
class MetadataDrivenEntityResolutionTests(RagTestDimensionMixin, TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.other = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.provider = FakeEmbeddingProvider()
        self.embedding_config = load_embedding_config()
        self.memory = DialogueMemory()

    def _ingest_manual(self, *, tenant, title: str, content: str):
        document = KnowledgeDocument.objects.create(
            tenant=tenant,
            title=title,
            slug=title.lower().replace(" ", "-"),
            content=content,
            status=KnowledgeDocument.Status.ACTIVE,
        )
        sync_manual_knowledge_document_to_rag(document=document)
        configuration = TenantRagConfiguration.objects.get(tenant=tenant)
        configuration.retrieval_enabled = True
        configuration.save(update_fields=["retrieval_enabled", "updated_at"])
        run_chunk_build_for_tenant(configuration=configuration)
        run_index_for_tenant(
            configuration=configuration,
            provider=self.provider,
            config=self.embedding_config,
            run_id=f"idx-{tenant.slug}-{document.pk}",
        )
        return document

    def _seed_catalog(self):
        self._ingest_manual(
            tenant=self.tenant,
            title="NEXUS R7 Manual",
            content=(
                "NEXUS R7\n"
                "Robô móvel para inspeção interna.\n\n"
                "## Autonomia\n"
                "Autonomia nominal: 7 horas.\n\n"
                "## Alimentação\n"
                "Alimentação da estação de carga: 220 V.\n\n"
                "## Peso\n"
                "Peso operacional: 82 kg."
            ),
        )
        self._ingest_manual(
            tenant=self.tenant,
            title="NEXUS R8 Manual",
            content=(
                "NEXUS R8\n"
                "Robô móvel para inspeção interna.\n\n"
                "## Autonomia\n"
                "Autonomia nominal: 10 horas.\n\n"
                "## Alimentação\n"
                "Alimentação da estação de carga: 380 V.\n\n"
                "## Peso\n"
                "Peso operacional: 95 kg."
            ),
        )

    def _turn(self, message: str):
        self.memory = update_dialogue_memory_from_turn(
            memory=self.memory,
            current_message=message,
            history=[],
            tenant=self.tenant,
        )
        if self.memory.notes.get("entity_ambiguity_options"):
            reply, _ = apply_response_quality_gate(reply="", current_message=message, memory=self.memory)
            return reply, None
        _original, contextual = build_contextual_retrieval_query(
            current_message=message,
            memory=self.memory,
            history=[],
        )
        result = retrieve_context(
            tenant=self.tenant,
            query=message,
            contextual_query=contextual,
            active_subject=self.memory.active_knowledge_subject,
            provider=self.provider,
            config=self.embedding_config,
        )
        reply = synthesize_deterministic_reply(
            result.context_text,
            current_message=message,
            active_domain=self.memory.active_domain,
            active_application=self.memory.active_application,
        )
        return reply, result

    def test_catalog_is_derived_from_ingested_document_metadata(self):
        self._seed_catalog()
        names = {entity.canonical_name for entity in entity_catalog_for_tenant(self.tenant)}

        self.assertIn("NEXUS R7", names)
        self.assertIn("NEXUS R8", names)

    def test_initial_question_resolves_entity_from_rag_metadata(self):
        self._seed_catalog()
        reply, result = self._turn("o que é o Nexus R7?")

        self.assertEqual(self.memory.active_knowledge_subject["canonical_name"], "NEXUS R7")
        self.assertTrue(result.chunks)
        self.assertIn("robô móvel", reply.lower())
        self.assertIn("NEXUS R7", result.context_text)

    def test_followup_uses_active_subject_without_registry(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        reply, result = self._turn("e a autonomia?")

        self.assertEqual(self.memory.active_knowledge_subject["canonical_name"], "NEXUS R7")
        self.assertIn("7 horas", reply)
        self.assertIn("NEXUS R7", result.context_text)
        self.assertNotIn("10 horas", reply)

    def test_pronominal_followup_uses_active_subject(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        reply, result = self._turn("ele usa qual tensão na estação?")

        self.assertIn("220 V", reply)
        self.assertIn("NEXUS R7", result.context_text)

    def test_missing_requirement_does_not_infer(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        reply, _result = self._turn("ele possui certificação IP67?")

        self.assertIn("não encontrei", reply.lower())
        self.assertIn("IP67", reply)

    def test_subject_switches_to_other_documented_entity(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        self._turn("e o Nexus R8?")
        reply, result = self._turn("quanto dura a bateria dele?")

        self.assertEqual(self.memory.active_knowledge_subject["canonical_name"], "NEXUS R8")
        self.assertIn("10 horas", reply)
        self.assertIn("NEXUS R8", result.context_text)
        self.assertNotIn("7 horas", reply)

    def test_ambiguous_partial_name_asks_for_model(self):
        self._seed_catalog()
        reply, result = self._turn("quanto dura a bateria do Nexus?")

        self.assertIsNone(result)
        self.assertIn("qual modelo", reply.lower())
        self.assertIn("NEXUS R7", reply)
        self.assertIn("NEXUS R8", reply)

    def test_active_subject_prioritizes_own_document_over_other_product(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        reply, result = self._turn("quanto pesa?")

        self.assertIn("82 kg", reply)
        self.assertIn("NEXUS R7", result.context_text)
        self.assertNotIn("95 kg", reply)

    def test_entity_catalog_is_tenant_scoped(self):
        self._seed_catalog()
        self._ingest_manual(
            tenant=self.other,
            title="NEXUS R9 Manual",
            content="NEXUS R9\nRobô de outro tenant. Autonomia nominal: 99 horas.",
        )

        own_names = {entity.canonical_name for entity in entity_catalog_for_tenant(self.tenant)}
        other_names = {entity.canonical_name for entity in entity_catalog_for_tenant(self.other)}
        resolution = resolve_knowledge_entity(tenant=self.tenant, message="me fale do Nexus R9")

        self.assertNotIn("NEXUS R9", own_names)
        self.assertIn("NEXUS R9", other_names)
        self.assertIsNone(resolution.subject)

    def test_retrieval_event_records_subject_metadata_without_content(self):
        self._seed_catalog()
        self._turn("me fale do Nexus R7")
        self._turn("e a autonomia?")

        event = RagRetrievalEvent.objects.filter(tenant=self.tenant).latest("created_at")
        metadata = event.retrieval_metadata
        self.assertEqual(metadata["active_subject"]["canonical_name"], "NEXUS R7")
        self.assertTrue(metadata["document_ids_used"])
        self.assertIn(metadata["retrieval_method"], {"semantic", "semantic_metadata"})
        self.assertNotIn("query", metadata)
