"""Testes de entailment para claims técnicos pós-LLM."""

from __future__ import annotations

from django.test import SimpleTestCase

from assistant_core.dialogue_memory import DialogueMemory
from assistant_core.eval.capability_entailment import (
    assess_capability_entailment,
    build_grounded_limitation_reply,
    sanitize_knowledge_snippet,
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

    def test_a_apoiar_limpeza_with_conditional_kb_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        llm = "Pode apoiar a limpeza em ambientes com circulação de pessoas."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
        )
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "people_circulation")

    def test_b_claim_with_trailing_disclaimer_still_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        llm = "Ele pode operar com pessoas circulando, mas isso depende de avaliação do fluxo."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
        )
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "people_circulation")

    def test_c_apoiar_with_direct_kb_allowed(self):
        kb = _kb("O equipamento foi projetado para operar em ambientes com circulação de pessoas.")
        llm = "Pode apoiar a limpeza com pessoas circulando."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
        )
        self.assertFalse(result.unsupported)

    def test_d_evaluation_language_allowed(self):
        kb = _kb("O fluxo de pessoas deve ser avaliado conforme o ambiente.")
        llm = "A avaliação ajuda a definir a operação."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="e as pessoas circulando?",
        )
        self.assertFalse(result.unsupported)

    def test_e_safety_claim_without_kb_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas e horários.")
        llm = "Isso garante uma operação segura."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
        )
        self.assertTrue(result.unsupported)
        self.assertEqual(result.topic, "safety_efficiency")

    def test_f_safety_claim_with_kb_allowed(self):
        kb = _kb("Possui sistemas documentados para operação segura em áreas ocupadas.")
        llm = "Foi projetado para operação segura nesse contexto."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="isso é seguro com pessoas no local?",
        )
        self.assertFalse(result.unsupported)

    def test_smoke_exact_staging_response_blocked(self):
        kb = _kb(
            "A escolha depende de tipo de piso, fluxo de pessoas, "
            "horários, obstáculos e responsáveis operacionais."
        )
        question = "ele consegue trabalhar com pessoas circulando?"
        llm = (
            "O Hygibot Dune pode apoiar a limpeza em ambientes com circulação "
            "de pessoas, mas a adequação para trabalhar com pessoas circulando "
            "depende de uma avaliação do fluxo, dos horários e dos obstáculos "
            "presentes no local. Essa análise é importante para garantir a "
            "segurança e a eficiência da operação do robô no seu galpão."
        )
        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message=question,
            memory=DialogueMemory(active_entity="Hygibot Dune"),
            llm_primary=True,
        )
        self.assertTrue(diag.get("capability_claim_blocked"))
        self.assertNotIn("pode apoiar", repaired.lower())
        self.assertNotIn("circulação de pessoas", repaired.lower().replace("ã", "a"))
        self.assertNotIn("garantir a seguranca", repaired.lower().replace("ã", "a"))
        self.assertNotIn("eficiencia", repaired.lower().replace("ê", "e"))
        self.assertIn("nao confirma", repaired.lower().replace("ã", "a"))
        self.assertIn("fluxo de pessoas", repaired.lower())

    def test_false_positives_consultative_phrases_allowed(self):
        kb = _kb("A escolha depende do fluxo de pessoas, horários e obstáculos.")
        question = "ele consegue trabalhar com pessoas circulando?"
        allowed = (
            "Precisamos avaliar a circulação de pessoas.",
            "O fluxo de pessoas é um dos fatores da avaliação.",
            "É necessário verificar os horários de operação.",
            "A documentação cita o fluxo de pessoas como condicionante.",
            "A avaliação ajuda a definir a operação.",
            "Avaliar para melhorar a segurança é importante.",
            "Buscar eficiência operacional faz parte do processo.",
        )
        for llm in allowed:
            with self.subTest(llm=llm):
                result = assess_capability_entailment(
                    reply=llm,
                    knowledge_context=kb,
                    current_message=question,
                )
                self.assertFalse(result.unsupported, msg=llm)

    def test_false_negatives_structural_variations_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        question = "ele consegue trabalhar com pessoas circulando?"
        blocked = (
            ("Ele dá conta de trabalhar com gente passando no galpão.", "people_circulation"),
            ("Pode fazer a limpeza com pessoas no local.", "people_circulation"),
            ("É adequado para ambientes ocupados.", "people_circulation"),
            ("Funciona com circulação de pessoas.", "people_circulation"),
            ("Consegue realizar a limpeza enquanto há pessoas no ambiente.", "people_circulation"),
            ("É seguro operar nesse cenário com circulação.", "safety_efficiency"),
            ("Pode operar com pessoas circulando, mas precisa avaliar o fluxo.", "people_circulation"),
        )
        for llm, topic in blocked:
            with self.subTest(llm=llm):
                result = assess_capability_entailment(
                    reply=llm,
                    knowledge_context=kb,
                    current_message=question,
                )
                self.assertTrue(result.unsupported, msg=llm)
                self.assertEqual(result.topic, topic, msg=llm)

    def test_contextual_turn_a_galpao_not_blocked(self):
        kb = _kb("A escolha depende de tipo de piso, fluxo de pessoas, horários e obstáculos.")
        llm = "Entendi. Precisamos considerar piso e fluxo de pessoas."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="um galpão",
        )
        self.assertFalse(result.unsupported)
        _, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message="um galpão",
            memory=DialogueMemory(active_entity="Hygibot Dune"),
            llm_primary=True,
        )
        self.assertFalse(diag.get("capability_claim_blocked"))

    def test_contextual_turn_b_area_piso_not_blocked(self):
        kb = _kb("A escolha depende de tipo de piso, fluxo de pessoas, horários e obstáculos.")
        llm = "Vale avaliar fluxo de pessoas e obstáculos."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="3000 m2, piso de concreto",
        )
        self.assertFalse(result.unsupported)

    def test_contextual_turn_c_people_fact_not_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        llm = "Entendi. O fluxo de pessoas será um fator da avaliação."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="tem muita gente circulando",
        )
        self.assertFalse(result.unsupported)

    def test_contextual_llm_with_soft_modal_not_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        llm = "Para um galpão, a limpeza pode ser planejada considerando o fluxo de pessoas."
        result = assess_capability_entailment(
            reply=llm,
            knowledge_context=kb,
            current_message="um galpão",
        )
        self.assertFalse(result.unsupported)

    def test_capability_turn_d_still_blocks(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        llm = "Sim, pode apoiar a limpeza com pessoas circulando."
        repaired, diag = apply_response_quality_gate(
            reply=llm,
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
            memory=DialogueMemory(active_entity="Hygibot Dune"),
            llm_primary=True,
        )
        self.assertTrue(diag.get("capability_claim_blocked"))
        for leak in ("#", "Tags:", "curated", "Nome oficial:", "Categoria:", "source", "document_id", "RAG"):
            self.assertNotIn(leak.lower(), repaired.lower())
        self.assertIn("fluxo de pessoas", repaired.lower())

    def test_sanitize_knowledge_snippet_removes_metadata(self):
        raw = (
            "# Hygibot Dune\n"
            "Tags: smart-control, curated\n"
            "Nome oficial: HygiBot / Dune Bot\n"
            "Categoria: limpeza profissional\n"
            "A escolha depende de tipo de piso, fluxo de pessoas, horários e obstáculos."
        )
        cleaned = sanitize_knowledge_snippet(raw)
        self.assertNotIn("#", cleaned)
        self.assertNotIn("Tags:", cleaned)
        self.assertNotIn("curated", cleaned.lower())
        self.assertIn("fluxo de pessoas", cleaned.lower())

    def test_fallback_when_no_clean_snippet_uses_generic_reply(self):
        kb = _kb(
            "# Produto X\n"
            "Tags: smart-control, curated\n"
            "Nome oficial: Produto X\n"
            "Categoria: robótica\n"
            "source: internal\n"
            "document_id: 17"
        )
        repaired = build_grounded_limitation_reply(
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
            active_entity="Produto X",
        )
        self.assertIn("confirma", repaired.lower().replace("ã", "a"))
        self.assertNotIn("ela informa que", repaired.lower())
        for leak in ("#", "tags:", "curated", "nome oficial:", "categoria:", "source", "document_id", "rag"):
            self.assertNotIn(leak, repaired.lower())

    def test_scope_contextual_messages_not_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas, horários e obstáculos.")
        cases = (
            ("um galpão", "Entendi. Precisamos considerar piso e fluxo de pessoas."),
            ("3000 m2, piso de concreto", "Vale avaliar fluxo de pessoas e obstáculos."),
            ("tem muita gente circulando", "Entendi. O fluxo de pessoas será um fator da avaliação."),
            ("trabalhamos durante a noite", "Os horários de operação precisam entrar na avaliação."),
            ("há obstáculos", "Vale mapear os obstáculos antes de definir a operação."),
        )
        for message, llm in cases:
            with self.subTest(message=message):
                result = assess_capability_entailment(
                    reply=llm,
                    knowledge_context=kb,
                    current_message=message,
                )
                self.assertFalse(result.unsupported, msg=message)

    def test_soft_modals_allowed_vs_capability_claims_blocked(self):
        kb = _kb("A escolha depende do fluxo de pessoas.")
        allowed = (
            "Isso pode ser avaliado conforme o ambiente.",
            "A operação pode ser planejada considerando o fluxo.",
            "Vale considerar o fluxo de pessoas na definição.",
            "Precisamos avaliar o fluxo de pessoas.",
        )
        for llm in allowed:
            with self.subTest(llm=llm):
                result = assess_capability_entailment(
                    reply=llm,
                    knowledge_context=kb,
                    current_message="um galpão",
                )
                self.assertFalse(result.unsupported, msg=llm)

        blocked = (
            "Ele pode operar com pessoas circulando.",
            "Consegue trabalhar com fluxo de pessoas.",
            "Suporta operação com pessoas no local.",
            "É adequado para ambientes ocupados.",
            "É seguro operar com pessoas no local.",
        )
        question = "ele consegue trabalhar com pessoas circulando?"
        for llm in blocked:
            with self.subTest(llm=llm):
                result = assess_capability_entailment(
                    reply=llm,
                    knowledge_context=kb,
                    current_message=question,
                )
                self.assertTrue(result.unsupported, msg=llm)

    def test_repaired_reply_uses_clean_conditional_not_headings(self):
        kb = _kb(
            "# Hygibot Dune\n"
            "Tags: smart-control, curated\n"
            "Nome oficial: HygiBot / Dune Bot\n"
            "Categoria: limpeza profissional\n"
            "A escolha depende de tipo de piso, fluxo de pessoas, horários, obstáculos e responsáveis operacionais."
        )
        repaired = build_grounded_limitation_reply(
            knowledge_context=kb,
            current_message="ele consegue trabalhar com pessoas circulando?",
            active_entity="Hygibot Dune",
        )
        self.assertNotIn("#", repaired)
        self.assertNotIn("Tags:", repaired)
        self.assertNotIn("curated", repaired.lower())
        self.assertIn("fluxo de pessoas", repaired.lower())

    def test_smoke_four_turn_sequence(self):
        kb = _kb(
            "# Hygibot Dune\n"
            "Tags: smart-control, curated\n"
            "A escolha depende de tipo de piso, fluxo de pessoas, "
            "horários, obstáculos e responsáveis operacionais."
        )
        memory = DialogueMemory(active_entity="Hygibot Dune")
        turns = (
            (
                "preciso de um robo de limpeza",
                "Posso te ajudar a entender opções de robôs de limpeza para o seu cenário.",
                False,
            ),
            (
                "um galpão",
                "Entendi. Para um galpão, precisamos considerar piso, área e fluxo de pessoas.",
                False,
            ),
            (
                "3000 m2, piso de concreto",
                "Com 3000 m² e piso de concreto, vale avaliar fluxo, obstáculos e rotina de limpeza.",
                False,
            ),
            (
                "ele consegue trabalhar com pessoas circulando?",
                "O Hygibot Dune pode apoiar a limpeza em ambientes com circulação de pessoas.",
                True,
            ),
        )
        for message, llm, should_block in turns:
            with self.subTest(message=message):
                _, diag = apply_response_quality_gate(
                    reply=llm,
                    knowledge_context=kb,
                    current_message=message,
                    memory=memory,
                    llm_primary=True,
                )
                blocked = bool(diag.get("capability_claim_blocked"))
                self.assertEqual(blocked, should_block, msg=message)
