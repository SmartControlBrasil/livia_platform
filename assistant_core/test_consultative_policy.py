from django.test import SimpleTestCase

from assistant_core.consultative_policy import (
    CollectionTrigger,
    detect_collection_trigger,
    is_conceptual_price_question,
    is_explicit_collection_trigger,
    is_consultative_need_discovery,
)
from assistant_core.discovery import analyze_message


class ConsultativePolicyTests(SimpleTestCase):
    def test_commercial_interest_does_not_collect_without_explicit_trigger(self):
        for message in (
            "preciso de um site",
            "preciso de um sistema web",
            "preciso automatizar uma máquina",
            "quero saber sobre robótica",
            "tenho interesse em um robô",
            "gostaria de uma loja virtual",
            "vendo ferramentas",
            "trabalho com automação",
            "tenho uma clínica",
            "vocês fazem sites?",
            "quanto tempo demora?",
            "como funciona?",
            "quero",
            "preciso",
        ):
            discovery = analyze_message(message)
            self.assertFalse(discovery.should_collect_lead, message)
            self.assertFalse(is_explicit_collection_trigger(message), message)

    def test_need_and_interest_are_consultative_discovery(self):
        for message in (
            "preciso de um site",
            "preciso de um sistema web",
            "preciso automatizar uma máquina",
            "quero saber sobre robótica",
            "tenho interesse em um robô",
            "agora preciso de um site",
            "também quero automatizar minha empresa",
            "tenho outra dúvida sobre robôs",
            "quero",
            "preciso",
        ):
            discovery = analyze_message(message)
            self.assertTrue(is_consultative_need_discovery(discovery, message), message)
            self.assertFalse(discovery.should_collect_lead, message)

    def test_explicit_conversion_is_not_consultative_discovery(self):
        for message in (
            "quero comprar um robô",
            "quero um orçamento",
            "quero proposta",
            "quero falar com vendedor",
        ):
            discovery = analyze_message(message)
            self.assertFalse(is_consultative_need_discovery(discovery, message), message)

    def test_explicit_budget_and_human_triggers(self):
        self.assertEqual(detect_collection_trigger("quero um orçamento para essa loja"), CollectionTrigger.BUDGET)
        self.assertEqual(detect_collection_trigger("quero contratar"), CollectionTrigger.HIRE)
        self.assertEqual(detect_collection_trigger("quero falar com um especialista"), CollectionTrigger.HUMAN)
        self.assertTrue(analyze_message("quero um orçamento para meu site").should_collect_lead)

    def test_conceptual_price_is_not_collection(self):
        self.assertTrue(is_conceptual_price_question("quanto custa um site?"))
        self.assertFalse(is_explicit_collection_trigger("quanto custa um site?"))
        discovery = analyze_message("quanto custa uma loja virtual?")
        self.assertFalse(discovery.should_collect_lead)
        self.assertTrue(discovery.should_answer_contextually)

    def test_pending_inference_rejects_consultative_phrases(self):
        from assistant_core.qualification import infer_pending_field_values

        self.assertEqual(infer_pending_field_values("eu vendo produtos para serralheria", "name_or_company"), {})
        self.assertEqual(infer_pending_field_values("quero um orçamento para essa loja", "name_or_company"), {})
        self.assertEqual(infer_pending_field_values("Maria Silva", "name_or_company"), {"name": "Maria Silva"})
        self.assertEqual(infer_pending_field_values("Ferragens Silva", "name_or_company"), {"name": "Ferragens Silva"})
        self.assertEqual(
            infer_pending_field_values("empresa Ferragens Silva", "name_or_company"),
            {"company": "Ferragens Silva"},
        )