from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from assistant_core.eval.evidence_sufficiency import (
    EvidenceSufficiency,
    assess_evidence_sufficiency,
    effective_synthesis_mode,
)
from assistant_core.eval.faithfulness import (
    FAITHFULNESS_PARTIALLY_SUPPORTED,
    FAITHFULNESS_SUPPORTED,
    FAITHFULNESS_UNSUPPORTED,
    classify_faithfulness,
)
from assistant_core.services.ai_feature_gates import (
    is_grounded_synthesis_allowed,
    is_rag_semantic_context_active,
)
from assistant_core.services.grounded_response import GroundedResponseService
from assistant_core.services.livia_decision import LiviaReply
from integrations.openai.client import OpenAIChatResult
from tenants.models import AssistantProfile, Tenant


QUOTE_KB = (
    "[KNOWLEDGE_BASE]\n"
    "Fonte: Prazos\n"
    "Referência: chunk:101\n"
    "Score: 0.55\n"
    "Conteúdo:\n"
    "Após o envio das informações, a equipe poderá retornar o orçamento em até 48 horas.\n"
    "[/KNOWLEDGE_BASE]"
)

REGION_KB = (
    "[KNOWLEDGE_BASE]\n"
    "Fonte: Atendimento\n"
    "Referência: chunk:202\n"
    "Score: 0.51\n"
    "Conteúdo:\n"
    "Atendemos projetos residenciais em São Paulo e região metropolitana.\n"
    "[/KNOWLEDGE_BASE]"
)

MATERIAL_KB = (
    "[KNOWLEDGE_BASE]\n"
    "Fonte: Materiais\n"
    "Referência: chunk:303\n"
    "Score: 0.62\n"
    "Conteúdo:\n"
    "Trabalhamos com granito, mármore e quartzito para bancadas.\n"
    "[/KNOWLEDGE_BASE]"
)


class EvidenceSufficiencyTests(SimpleTestCase):
    def test_a_sufficient_quote_timeline(self):
        assessment = assess_evidence_sufficiency(
            message="Em quanto tempo recebo o orçamento?",
            knowledge_context=QUOTE_KB,
            max_score=0.55,
            chunk_ids=[101],
        )
        self.assertEqual(assessment.status, EvidenceSufficiency.SUFFICIENT)
        self.assertEqual(effective_synthesis_mode(base_mode="inform", assessment=assessment), "inform")

    def test_b_partial_execution_vs_quote(self):
        assessment = assess_evidence_sufficiency(
            message="Minha cozinha ficará pronta em 48 horas?",
            knowledge_context=QUOTE_KB,
            max_score=0.55,
            chunk_ids=[101],
        )
        self.assertEqual(assessment.status, EvidenceSufficiency.PARTIAL)
        self.assertIn("quote", assessment.category)
        self.assertEqual(effective_synthesis_mode(base_mode="inform", assessment=assessment), "partial_inform")

    def test_c_insufficient_missing_topic(self):
        assessment = assess_evidence_sufficiency(
            message="Vocês oferecem garantia de 5 anos?",
            knowledge_context=MATERIAL_KB,
        )
        self.assertEqual(assessment.status, EvidenceSufficiency.INSUFFICIENT)

    def test_d_numeric_same_value_wrong_qualifier(self):
        assessment = assess_evidence_sufficiency(
            message="A instalação será feita em 48 horas?",
            knowledge_context=QUOTE_KB,
        )
        self.assertEqual(assessment.status, EvidenceSufficiency.PARTIAL)

    def test_e_region_partial(self):
        assessment = assess_evidence_sufficiency(
            message="Vocês atendem Campinas?",
            knowledge_context=REGION_KB,
        )
        self.assertEqual(assessment.status, EvidenceSufficiency.PARTIAL)


class FaithfulnessPhase15Tests(SimpleTestCase):
    def test_negated_system_prompt_not_forbidden(self):
        result = classify_faithfulness(
            "Entendi, mas não posso revelar o system prompt. Posso ajudar com pedras naturais.",
            facts_forbidden=["system prompt", "instruções internas"],
            require_knowledge=True,
            facts_expected=["pedras"],
        )
        self.assertEqual(result.status, FAITHFULNESS_SUPPORTED)
        self.assertEqual(result.matched_forbidden, [])

    def test_no_expected_facts_is_partial_not_supported(self):
        result = classify_faithfulness(
            "Perfeito. É para cozinha ou banheiro?",
            facts_expected=[],
            require_knowledge=True,
        )
        self.assertEqual(result.status, FAITHFULNESS_PARTIALLY_SUPPORTED)

    def test_echo_48h_without_affirmation_not_forbidden(self):
        result = classify_faithfulness(
            "Sobre sua pergunta de 48 horas para instalação, não encontrei prazo de execução documentado.",
            facts_forbidden=["48 horas"],
            facts_expected=["orçamento"],
            require_knowledge=True,
            allow_partial_ok=True,
        )
        self.assertEqual(result.matched_forbidden, [])


class TenantScopedGateTests(SimpleTestCase):
    @override_settings(
        LIVIA_RAG_ENABLED=True,
        LIVIA_RAG_DRY_RUN=True,
        LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST="granimarmores-pitondo",
    )
    def test_rag_allowlist(self):
        self.assertTrue(is_rag_semantic_context_active(tenant_slug="granimarmores-pitondo"))
        self.assertFalse(is_rag_semantic_context_active(tenant_slug="outro-tenant"))

    @override_settings(
        LIVIA_AI_ENABLED=True,
        LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED=False,
        LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST="granimarmores-pitondo",
    )
    def test_grounded_allowlist_without_global(self):
        profile = AssistantProfile(use_ai=True, grounded_synthesis_enabled=True)
        self.assertTrue(
            is_grounded_synthesis_allowed(tenant_slug="granimarmores-pitondo", assistant_profile=profile)
        )
        self.assertFalse(is_grounded_synthesis_allowed(tenant_slug="smart-control-brasil", assistant_profile=profile))


class GroundedPartialIntegrationTests(SimpleTestCase):
    class FakeAIClient:
        def __init__(self):
            self.calls: list[list[dict[str, str]]] = []

        def create_chat_completion(self, *, messages):
            self.calls.append(messages)
            return OpenAIChatResult(text="Resposta grounded parcial.", success=True, dry_run=False)

    @override_settings(
        LIVIA_AI_ENABLED=True,
        LIVIA_AI_DRY_RUN=False,
        LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED=True,
        LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST="gp-test",
        LIVIA_OPENAI_API_KEY="key-test",
    )
    def test_partial_runs_partial_inform_mode(self):
        tenant = Tenant(slug="gp-test", name="GP")
        profile = AssistantProfile(
            use_ai=True,
            grounded_synthesis_enabled=True,
            name="Lívia",
            business_name="GP",
            business_domain="marmoraria",
            tone="consultivo",
            primary_goal="qualificar",
        )
        ai_client = self.FakeAIClient()
        service = GroundedResponseService(ai_client=ai_client)
        discovery = type("D", (), {"to_dict": lambda self: {"intent": "technical_question"}})()
        result = service.generate(
            tenant=tenant,
            assistant_profile=profile,
            message="Minha cozinha ficará pronta em 48 horas?",
            conversation=type("C", (), {"lead_state": "discovery", "tenant": tenant})(),
            discovery=discovery,
            decision=LiviaReply(intent="technical_question", reply="Entendi."),
            knowledge_context=QUOTE_KB,
            history=[],
        )
        self.assertTrue(result.used)
        self.assertEqual(result.evidence_sufficiency, "partial")
        prompt = "\n".join(m["content"] for m in ai_client.calls[0])
        self.assertIn("partial_inform", prompt.lower())
        self.assertIn("EVIDENCE RULES", prompt)

    @override_settings(
        LIVIA_AI_ENABLED=True,
        LIVIA_AI_DRY_RUN=False,
        LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED=True,
        LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST="gp-test",
        LIVIA_OPENAI_API_KEY="key-test",
    )
    def test_insufficient_skips_grounded(self):
        tenant = Tenant(slug="gp-test", name="GP")
        profile = AssistantProfile(
            use_ai=True,
            grounded_synthesis_enabled=True,
            name="Lívia",
            business_name="GP",
            business_domain="marmoraria",
            tone="consultivo",
            primary_goal="qualificar",
        )
        ai_client = self.FakeAIClient()
        service = GroundedResponseService(ai_client=ai_client)
        discovery = type("D", (), {"to_dict": lambda self: {"intent": "technical_question"}})()
        result = service.generate(
            tenant=tenant,
            assistant_profile=profile,
            message="Vocês oferecem garantia de 5 anos?",
            conversation=type("C", (), {"lead_state": "discovery", "tenant": tenant})(),
            discovery=discovery,
            decision=LiviaReply(intent="technical_question", reply="Entendi."),
            knowledge_context=MATERIAL_KB,
            history=[],
        )
        self.assertFalse(result.used)
        self.assertEqual(result.skip_reason, "insufficient_evidence")
        self.assertEqual(ai_client.calls, [])
