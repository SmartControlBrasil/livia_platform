"""Testes de entailment para claims técnicos pós-LLM."""

from __future__ import annotations

from django.test import SimpleTestCase

from assistant_core.dialogue_memory import DialogueMemory
from assistant_core.eval.capability_entailment import (
    assess_capability_entailment,
    build_grounded_limitation_reply,
)
from assistant_core.services.response_quality_gate import apply_response_quality_gate


def _kb(content: str) -> str:
    return f"[KNOWLEDGE_BASE]\nConteúdo:\n{content}\n[/KNOWLEDGE_BASE]"


class CapabilityEntailmentTests(SimpleTestCase):
    def test_case_a_conditional_kb_blocks_positive_claim(self):
        kb = _kb("A escolha depende do fluxo de pessoas, horários e obstáculos.")
        question = "ele pode trabalhar com pessoas circulando?"
        llm = "Sim, ele pode operar com pessoas circulando."
        result = assess_capability_entailment(reply=llm, knowledge_context=kb, current_message=question)
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "people_circulation")

        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message=question,
            memory=DialogueMemory(active_entity="HygiBot Dune"),
            llm_primary=True,
        )
        self.assertTrue(diag.get("capability_claim_blocked"))
        self.assertIn("nao confirma", repaired.lower().replace("ã", "a"))
        self.assertNotIn("pode operar com pessoas circulando", repaired.lower())

    def test_case_b_direct_kb_allows_positive_claim(self):
        kb = _kb("O equipamento foi projetado para operar em ambientes com circulação de pessoas.")
        question = "ele pode trabalhar com pessoas circulando?"
        llm = "Sim, pode operar com pessoas circulando."
        result = assess_capability_entailment(reply=llm, knowledge_context=kb, current_message=question)
        self.assertFalse(result.unsupported)

        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message=question,
            llm_primary=True,
        )
        self.assertFalse(diag.get("capability_claim_blocked"))
        self.assertIn("pode operar", repaired.lower())

    def test_case_c_evaluate_obstacles_does_not_imply_overcome(self):
        kb = _kb("A operação deve avaliar obstáculos conforme o layout.")
        llm = "Ele supera obstáculos automaticamente."
        result = assess_capability_entailment(reply=llm, knowledge_context=kb, current_message="e os obstáculos?")
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "obstacle_handling")

    def test_case_d_autonomy_in_kb_allows_claim(self):
        kb = _kb("Autonomia de até 8 horas por ciclo de operação.")
        llm = "Possui autonomia de até 8 horas."
        result = assess_capability_entailment(reply=llm, knowledge_context=kb, current_message="qual a autonomia?")
        self.assertFalse(result.unsupported)

    def test_case_e_autonomy_without_kb_blocks_claim(self):
        kb = _kb("Robô de limpeza para grandes áreas.")
        llm = "Possui autonomia de 8 horas."
        result = assess_capability_entailment(reply=llm, knowledge_context=kb, current_message="qual a autonomia?")
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "autonomy_duration")

    def test_limitation_reply_uses_kb_snippet(self):
        kb = _kb("A escolha depende do fluxo de pessoas, horários e obstáculos.")
        text = build_grounded_limitation_reply(
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
            active_entity="HygiBot Dune",
        )
        self.assertIn("fluxo de pessoas", text.lower())
        self.assertIn("nao confirma", text.lower().replace("ã", "a"))

    def test_natural_consultative_reply_without_capability_claim_passes(self):
        kb = _kb("Apoia rotinas de limpeza em grandes áreas.")
        llm = "Para um galpão, faz sentido entender o piso e a área útil antes de avançar."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="um galpão",
        )
        self.assertFalse(result.unsupported)

        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message="um galpão",
            llm_primary=True,
        )
        self.assertFalse(diag.get("capability_claim_blocked"))
        self.assertIn("galpão", repaired.lower())

    def test_consultative_evaluate_language_allowed(self):
        kb = _kb("A escolha depende do fluxo de pessoas, horários e obstáculos.")
        llm = "Precisamos avaliar o fluxo de pessoas e os horários de operação no ambiente."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
        )
        self.assertFalse(result.unsupported)

    def test_autonomy_wrong_number_blocked(self):
        kb = _kb("Autonomia de até 8 horas por ciclo de operação.")
        llm = "Possui autonomia de 10 horas."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="qual a autonomia?",
        )
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "autonomy_duration")

    def test_autonomy_guaranteed_qualifier_blocked(self):
        kb = _kb("Autonomia de até 8 horas por ciclo de operação.")
        llm = "Opera por 8 horas garantidas em condições normais."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="qual a autonomia?",
        )
        self.assertTrue(result.unsupported)
        self.assertEqual(result.reason, "qualifier_mismatch")

    def test_smoke_turn4_conditional_kb_blocks_hygibot_claim(self):
        kb = _kb(
            "A escolha depende de tipo de piso, fluxo de pessoas, "
            "horários, obstáculos e responsáveis operacionais."
        )
        question = "ele consegue trabalhar com pessoas circulando?"
        llm = (
            "O HygiBot Dune pode operar em ambientes com circulação de pessoas, "
            "desde que o layout permita."
        )
        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message=question,
            memory=DialogueMemory(active_entity="HygiBot Dune"),
            llm_primary=True,
        )
        self.assertTrue(diag.get("capability_claim_blocked"))
        self.assertIn("nao confirma", repaired.lower().replace("ã", "a"))
        self.assertIn("fluxo de pessoas", repaired.lower())
        self.assertNotIn("pode operar", repaired.lower())
        for internal_term in ("entailment", "capability_claim", "knowledge_context", "rag", "policy"):
            self.assertNotIn(internal_term, repaired.lower())

    def test_direct_support_smoke_not_blocked(self):
        kb = _kb("O equipamento foi projetado para operar em ambientes com circulação de pessoas.")
        llm = "Ele pode operar em ambientes com circulação de pessoas."
        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
            llm_primary=True,
        )
        self.assertFalse(diag.get("capability_claim_blocked"))
        self.assertIn("circulação de pessoas", repaired.lower())
