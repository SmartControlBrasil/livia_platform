"""Slot comercial não deve substituir active_knowledge_subject."""

from __future__ import annotations

import uuid

from django.test import TestCase, override_settings

from assistant_core.consultative_policy import mark_collection_active
from assistant_core.dialogue_memory import (
    DialogueMemory,
    build_collection_slot_context,
    build_contextual_retrieval_query,
    is_explicit_knowledge_subject_change,
    should_preserve_knowledge_subject,
    update_dialogue_memory_from_turn,
)
from conversations.models import Conversation
from knowledge_base.models import KnowledgeDocument, TenantRagConfiguration, TenantRagDriveFileManifest
from knowledge_base.rag.conversation_retrieval import retrieve_context
from knowledge_base.rag.embeddings import FakeEmbeddingProvider, load_embedding_config
from knowledge_base.rag.entity_catalog import resolve_knowledge_entity
from knowledge_base.rag.indexing import run_index_for_tenant
from knowledge_base.rag.sync import run_chunk_build_for_tenant
from knowledge_base.services.manual_rag import manual_drive_file_id, sync_manual_knowledge_document_to_rag
from leads.models import LeadDraft
from tenants.models import Tenant

XYRON_OVERVIEW = """
# Robótica de serviço Xyron

A Smart Control Brasil trabalha com robótica de serviço e IA da linha Xyron Robotics.

## Produtos oficiais no site (nomenclatura institucional)
- LIRO / Little Bot — robótica educacional interativa
- HygiBot / Dune Bot — limpeza profissional
- Orbit Bot / Patrol Bot — patrulhamento e segurança
"""

HYGIBOT_DEDICATED = """
# HygiBot / Dune Bot — robô de limpeza autônoma

Nome oficial: HygiBot / Dune Bot
Categoria: limpeza profissional

## Aplicação
Apoiar rotinas de limpeza em grandes áreas, com modos de lavar, varrer, aspirar e passar pano conforme ambiente e operação.
"""

ORBIT_DEDICATED = """
# Orbit Bot / Patrol Bot — patrulhamento e segurança

Nome oficial: Orbit Bot / Patrol Bot
Categoria: segurança patrimonial / patrulhamento
"""


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
class SlotAwareMemoryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="SCB Slot", slug=f"scb-slot-{uuid.uuid4().hex[:6]}")
        self.provider = FakeEmbeddingProvider()
        self.embedding_config = load_embedding_config()
        self.conversation = Conversation.objects.create(tenant=self.tenant, session_id="slot-session")
        self.lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            name="Marcelo Custodio",
            need_summary="robô de limpeza para galpão 3000m2 piso concreto",
        )
        mark_collection_active(self.lead, reason="explicit_quote")
        self.lead.refresh_from_db()
        self.memory = DialogueMemory(
            active_entity="Duno",
            active_domain="robotics",
            active_topic="cleaning_robot",
            active_application="cleaning_robotics",
            active_knowledge_subject={
                "canonical_name": "HygiBot / Dune Bot",
                "source_document_ids": [17],
                "confidence": 0.88,
                "match_method": "application_family",
            },
        )
        self.manifests: dict[str, int] = {}

    def _ingest(self, *, slug: str, title: str, content: str) -> int:
        document = KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title=title,
            slug=f"{slug}-{uuid.uuid4().hex[:6]}",
            content=content,
            status=KnowledgeDocument.Status.ACTIVE,
        )
        sync_manual_knowledge_document_to_rag(document=document)
        configuration = TenantRagConfiguration.objects.get(tenant=self.tenant)
        configuration.retrieval_enabled = True
        configuration.save(update_fields=["retrieval_enabled", "updated_at"])
        run_chunk_build_for_tenant(configuration=configuration)
        run_index_for_tenant(
            configuration=configuration,
            provider=self.provider,
            config=self.embedding_config,
            run_id=f"idx-{self.tenant.slug}-{document.pk}",
        )
        manifest = TenantRagDriveFileManifest.objects.get(
            tenant=self.tenant,
            drive_file_id=manual_drive_file_id(document),
        )
        self.manifests[slug] = manifest.id
        return manifest.id

    def _seed_catalog(self):
        overview_id = self._ingest(slug="overview", title="Xyron Visão Geral", content=XYRON_OVERVIEW)
        hygibot_id = self._ingest(slug="hygibot", title="Hygiibot Dune", content=HYGIBOT_DEDICATED)
        orbit_id = self._ingest(slug="orbit", title="Orbit Bot", content=ORBIT_DEDICATED)
        self.memory.active_knowledge_subject["source_document_ids"] = [hygibot_id]
        return hygibot_id, overview_id, orbit_id

    def _update(self, message: str, *, history=None):
        slot_context = build_collection_slot_context(
            conversation=self.conversation,
            lead_draft=self.lead,
            message=message,
        )
        self.memory = update_dialogue_memory_from_turn(
            memory=self.memory,
            current_message=message,
            history=history or [],
            tenant=self.tenant,
            slot_context=slot_context,
        )
        return slot_context

    def test_a_company_slot_preserves_hygibot_subject(self):
        hygibot_id, overview_id, _orbit_id = self._seed_catalog()
        before = dict(self.memory.active_knowledge_subject)
        slot = self._update("Smart Control Brasil")
        self.assertTrue(slot.collection_active)
        self.assertTrue(slot.is_slot_value)
        self.assertIn("HygiBot", self.memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], hygibot_id)
        self.assertNotEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], overview_id)
        self.assertEqual(before["canonical_name"], self.memory.active_knowledge_subject["canonical_name"])

    def test_b_email_slot_preserves_subject(self):
        self._seed_catalog()
        self._update("marcelo@example.com")
        self.assertIn("HygiBot", self.memory.active_knowledge_subject.get("canonical_name", ""))

    def test_c_phone_slot_preserves_subject(self):
        self._seed_catalog()
        self._update("11999999999")
        self.assertIn("HygiBot", self.memory.active_knowledge_subject.get("canonical_name", ""))

    def test_d_pronoun_after_slots_resolves_hygibot_rag(self):
        hygibot_id, _overview_id, _orbit_id = self._seed_catalog()
        self._update("Smart Control Brasil")
        self._update("marcelo.teste@example.com")
        self._update("11999999999")
        self._update("e esse robô também aspira ou só lava?")
        self.assertIn("HygiBot", self.memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], hygibot_id)
        _original, contextual = build_contextual_retrieval_query(
            current_message="e esse robô também aspira ou só lava?",
            memory=self.memory,
            history=[
                {"role": "user", "content": "Smart Control Brasil"},
                {"role": "user", "content": "11999999999"},
            ],
        )
        self.assertIn("HygiBot", contextual)
        self.assertNotIn("Smart Control Brasil", contextual)
        result = retrieve_context(
            tenant=self.tenant,
            query="e esse robô também aspira ou só lava?",
            contextual_query=contextual,
            active_subject=self.memory.active_knowledge_subject,
            active_application=self.memory.active_application,
            active_entity=self.memory.active_entity,
            active_domain=self.memory.active_domain,
            provider=self.provider,
            config=self.embedding_config,
        )
        self.assertTrue(result.chunks)
        self.assertEqual(result.document_ids_used[0], hygibot_id)
        blob = result.context_text.lower()
        self.assertTrue(any(token in blob for token in ("aspirar", "lavar", "varrer")))

    def test_e_explicit_orbit_change_during_collection(self):
        _hygibot_id, _overview_id, orbit_id = self._seed_catalog()
        self.assertTrue(is_explicit_knowledge_subject_change("antes disso quero saber sobre o Orbit"))
        self._update("antes disso quero saber sobre o Orbit")
        self.assertIn("Orbit", self.memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], orbit_id)

    def test_f_combined_company_and_orbit_change(self):
        _hygibot_id, _overview_id, orbit_id = self._seed_catalog()
        self._update("Minha empresa é Smart Control Brasil, mas quero saber do Orbit.")
        self.assertIn("Orbit", self.memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], orbit_id)

    def test_g_acme_logistica_not_knowledge_subject(self):
        hygibot_id, _overview_id, _orbit_id = self._seed_catalog()
        self._update("ACME Logística")
        self.assertIn("HygiBot", self.memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], hygibot_id)

    def test_h_without_collection_company_name_still_preserved_if_subject_strong(self):
        hygibot_id, overview_id, _orbit_id = self._seed_catalog()
        self.lead.qualification_data = {}
        self.lead.save(update_fields=["qualification_data", "updated_at"])
        resolution_before = dict(self.memory.active_knowledge_subject)
        self._update("Smart Control Brasil")
        self.assertEqual(
            self.memory.active_knowledge_subject.get("source_document_ids", [])[0],
            resolution_before["source_document_ids"][0],
        )
        self.assertNotEqual(self.memory.active_knowledge_subject.get("source_document_ids", [])[0], overview_id)

    def test_smoke_sequence_mocked(self):
        hygibot_id, _overview_id, _orbit_id = self._seed_catalog()
        memory = DialogueMemory()
        history: list[dict[str, str]] = []
        turns = [
            "preciso de um robo de limpeza para um galpão",
            "são 3000 m2 com piso de concreto",
            "quero um orçamento",
            "Marcelo Custodio",
            "Smart Control Brasil",
            "marcelo.teste@example.com",
            "11999999999",
            "e esse robô também aspira ou só lava?",
        ]
        mark_collection_active(self.lead, reason="explicit_quote")
        for idx, message in enumerate(turns):
            if idx >= 2:
                mark_collection_active(self.lead, reason="explicit_quote")
            slot_context = build_collection_slot_context(
                conversation=self.conversation,
                lead_draft=self.lead,
                message=message,
            )
            memory = update_dialogue_memory_from_turn(
                memory=memory,
                current_message=message,
                history=history,
                tenant=self.tenant,
                slot_context=slot_context,
                commercial_trigger=message == "quero um orçamento",
            )
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": "ok"})

        self.assertIn("HygiBot", memory.active_knowledge_subject.get("canonical_name", ""))
        self.assertEqual(memory.active_knowledge_subject.get("source_document_ids", [])[0], hygibot_id)
        _original, contextual = build_contextual_retrieval_query(
            current_message=turns[-1],
            memory=memory,
            history=history,
        )
        result = retrieve_context(
            tenant=self.tenant,
            query=turns[-1],
            contextual_query=contextual,
            active_subject=memory.active_knowledge_subject,
            active_application=memory.active_application,
            active_entity=memory.active_entity,
            active_domain=memory.active_domain,
            provider=self.provider,
            config=self.embedding_config,
        )
        self.assertEqual(result.document_ids_used[0], hygibot_id)
        self.assertTrue(any(token in result.context_text.lower() for token in ("aspirar", "lavar")))

    def test_entity_resolution_without_preserve_would_match_overview(self):
        hygibot_id, overview_id, _orbit_id = self._seed_catalog()
        resolution = resolve_knowledge_entity(
            tenant=self.tenant,
            message="Smart Control Brasil",
            active_subject=self.memory.active_knowledge_subject,
            active_application="cleaning_robotics",
            active_topic="cleaning_robot",
        )
        if resolution.subject:
            self.assertNotEqual(resolution.subject.get("source_document_ids", [None])[0], hygibot_id)

    def test_should_preserve_blocks_title_case_company(self):
        self.assertTrue(
            should_preserve_knowledge_subject(
                memory=self.memory,
                message="Smart Control Brasil",
                slot_context=build_collection_slot_context(
                    conversation=self.conversation,
                    lead_draft=self.lead,
                    message="Smart Control Brasil",
                ),
            )
        )
