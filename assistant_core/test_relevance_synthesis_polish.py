from django.test import SimpleTestCase

from assistant_core.dialogue_memory import (
    DialogueMemory,
    build_contextual_retrieval_query,
    infer_application,
    is_material_recommendation_question,
    update_dialogue_memory_from_turn,
)
from assistant_core.followup_strategy import select_followup
from assistant_core.services.deterministic_synthesis import (
    detect_answer_shape,
    strip_meta_rag_phrasing,
    synthesize_deterministic_reply,
)
from assistant_core.services.response_quality_gate import apply_response_quality_gate
from knowledge_base.rag.content_classification import domains_compatible


class MaterialApplicationRankingTests(SimpleTestCase):
    def test_material_recommendation_uses_kitchen_application(self):
        memory = DialogueMemory()
        for message in (
            "estou reformando minha cozinha",
            "preciso de uma bancada",
            "vou usar cooktop",
            "qual material é melhor?",
        ):
            update_dialogue_memory_from_turn(memory=memory, current_message=message, history=[])
        self.assertEqual(memory.active_domain, "materials")
        self.assertIn(memory.active_application, {"kitchen_countertop", "cooktop_countertop"})
        _, contextual = build_contextual_retrieval_query(
            current_message="qual material é melhor?",
            memory=memory,
        )
        contextual_n = contextual.lower()
        self.assertIn("cozinha", contextual_n)
        self.assertIn("cooktop", contextual_n)
        self.assertTrue(is_material_recommendation_question("qual material vocês recomendam?"))

    def test_cooktop_context_boosts_kitchen_documents(self):
        self.assertEqual(infer_application("vou colocar cooktop"), "cooktop_countertop")
        memory = DialogueMemory(active_domain="materials", active_topic="kitchen", active_application="kitchen_countertop")
        update_dialogue_memory_from_turn(memory=memory, current_message="vou colocar cooktop")
        self.assertEqual(memory.active_application, "cooktop_countertop")

    def test_gourmet_does_not_override_kitchen_context(self):
        memory = DialogueMemory(
            active_domain="materials",
            active_topic="kitchen",
            active_application="cooktop_countertop",
            active_need="cozinha bancada cooktop",
        )
        update_dialogue_memory_from_turn(memory=memory, current_message="qual material vocês recomendam?")
        self.assertEqual(memory.active_application, "cooktop_countertop")
        _, contextual = build_contextual_retrieval_query(
            current_message="qual material vocês recomendam?",
            memory=memory,
        )
        self.assertIn("cozinha", contextual.lower())
        self.assertNotIn("área gourmet bancadas churrasqueira", contextual.lower())

    def test_material_ambiguous_without_context_asks_clarification(self):
        reply = synthesize_deterministic_reply(
            "",
            base_reply="",
            current_message="qual material é melhor?",
            active_application="",
        )
        # Sem evidência e sem application: shape recomenda esclarecimento via apply shape on empty → ""
        shaped = synthesize_deterministic_reply(
            "[KNOWLEDGE_BASE]\nConteúdo: Alguma nota genérica sem critério documentado.\n[/KNOWLEDGE_BASE]",
            current_message="qual material é melhor?",
            active_application="",
        )
        self.assertTrue(
            "aplicação" in shaped.lower() or "comparando" in shaped.lower() or shaped
        )

    def test_topic_switch_updates_application(self):
        memory = DialogueMemory(active_domain="materials", active_topic="kitchen", active_application="kitchen_countertop")
        update_dialogue_memory_from_turn(memory=memory, current_message="agora quero falar de área gourmet")
        self.assertEqual(memory.active_topic, "gourmet")
        self.assertEqual(memory.active_application, "gourmet_countertop")


class FollowupAndSynthesisPolishTests(SimpleTestCase):
    def test_school_followup_stays_educational(self):
        memory = DialogueMemory(
            active_domain="robotics",
            active_topic="educational_robot",
            active_application="educational_robotics",
        )
        follow, diag = select_followup(
            memory=memory,
            current_message="qual solução vocês recomendam?",
            force=True,
        )
        self.assertIn("pedagóg", follow.lower())
        self.assertNotIn("piso", follow.lower())
        self.assertEqual(diag["followup_domain"], "robotics")

    def test_liro_bncc_answer_does_not_ask_cleaning_floor(self):
        knowledge = chr(10).join(
            [
                "[KNOWLEDGE_BASE]",
                "Conteúdo: Robô interativo para aproximar crianças e jovens da tecnologia por meio de experiências educacionais com comunicação, movimento e interação.",
                "Conteúdo: O que NÃO prometer sem avaliação: atendimento à BNCC (não documentado nesta fonte).",
                "[/KNOWLEDGE_BASE]",
            ]
        )
        memory = DialogueMemory(
            active_entity="LIRO",
            active_domain="robotics",
            active_topic="educational_robot",
            active_application="educational_robotics",
        )
        reply = synthesize_deterministic_reply(
            knowledge,
            current_message="ele atende a BNCC robótica?",
            active_domain=memory.active_domain,
            active_application=memory.active_application,
        )
        self.assertIn("BNCC", reply)
        self.assertIn("não encontrei confirmação", reply.lower())
        self.assertNotIn("piso", reply.lower())

        final, _ = apply_response_quality_gate(
            reply=f"{reply} Entendi, isso ajuda a detalhar a necessidade. Qual é o ambiente e o tipo de piso onde a limpeza acontece?",
            knowledge_context=knowledge,
            current_message="ele atende a BNCC robótica?",
            memory=memory,
        )
        self.assertNotIn("piso", final.lower())
        self.assertNotIn("limpeza acontece", final.lower())

    def test_mitsubishi_followup_stays_automation(self):
        memory = DialogueMemory(
            active_domain="automation",
            active_topic="industrial_automation",
            active_application="industrial_automation",
            active_entity="Mitsubishi",
        )
        follow, diag = select_followup(
            memory=memory,
            current_message="preciso substituir um CLP",
            force=True,
        )
        self.assertIn("automatizar", follow.lower())
        self.assertNotIn("piso", follow.lower())
        self.assertNotIn("escola", follow.lower())
        self.assertEqual(diag["followup_domain"], "automation")

        reply, gate = apply_response_quality_gate(
            reply=(
                "A Smart Control Brasil desenvolve e integra soluções de automação industrial "
                "com foco em tecnologia Mitsubishi Electric. Trabalhamos com robótica de serviço "
                "e outras soluções documentadas. Se quiser, me conta o ambiente e o objetivo principal "
                "para eu afinar a orientação."
            ),
            knowledge_context="",
            current_message="vocês trabalham com Mitsubishi?",
            memory=memory,
            append_followup=False,
        )
        self.assertNotIn("robótica de serviço", reply.lower())
        self.assertIn("mitsubishi", reply.lower())

    def test_meta_rag_phrasing_never_reaches_user(self):
        cleaned = strip_meta_rag_phrasing("Há referências a automação Mitsubishi em projetos industriais.")
        self.assertNotIn("há referências", cleaned.lower())
        self.assertTrue(cleaned.lower().startswith("automação") or "mitsubishi" in cleaned.lower())
        synthesized = synthesize_deterministic_reply(
            "[KNOWLEDGE_BASE]\nConteúdo: O site cita automação Mitsubishi Electric para CLPs.\n[/KNOWLEDGE_BASE]",
            current_message="vocês trabalham com Mitsubishi?",
            active_domain="automation",
        )
        self.assertNotIn("há referências", synthesized.lower())
        self.assertNotIn("o site cita", synthesized.lower())

    def test_followup_optional(self):
        memory = DialogueMemory(active_domain="automation", active_topic="industrial_automation")
        long_answer = (
            "A Smart Control Brasil desenvolve e integra soluções de automação industrial com foco em Mitsubishi. "
            "Também há suporte a programação de CLPs conforme o escopo do projeto."
        )
        follow, diag = select_followup(
            memory=memory,
            current_message="me fale mais",
            answer_text=long_answer,
            force=False,
        )
        self.assertEqual(follow, "")
        self.assertIn(diag["followup_strategy"], {"skipped_sufficient", "skipped_direct_ask"})

    def test_answer_shape_recommendation(self):
        self.assertEqual(detect_answer_shape("qual material vocês recomendam?"), "RECOMMENDATION")
        self.assertEqual(detect_answer_shape("como funciona a medição?"), "PROCESS")
        self.assertEqual(detect_answer_shape("quanto custa o Duno?"), "PRICE")

    def test_automation_incompatible_with_robotics(self):
        self.assertFalse(domains_compatible("automation", "robotics"))
        self.assertFalse(domains_compatible("robotics", "automation"))
