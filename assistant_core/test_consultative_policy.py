from django.test import SimpleTestCase

from assistant_core.consultative_policy import (
    CollectionTrigger,
    detect_collection_trigger,
    is_conceptual_price_question,
    is_explicit_collection_trigger,
)
from assistant_core.discovery import analyze_message


class ConsultativePolicyTests(SimpleTestCase):
    def test_commercial_interest_does_not_collect_without_explicit_trigger(self):
        for message in (
            "preciso de um site",
            "gostaria de uma loja virtual",
            "vendo ferramentas",
            "trabalho com automação",
            "tenho uma clínica",
            "vocês fazem sites?",
            "quanto tempo demora?",
            "como funciona?",
        ):
            discovery = analyze_message(message)
            self.assertFalse(discovery.should_collect_lead, message)
            self.assertFalse(is_explicit_collection_trigger(message), message)

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
