from django.test import TestCase, override_settings

from assistant_core.conversation_turns import (
    TurnKind,
    classify_conversation_turn,
    detect_question_type,
    extract_enrichment_snippet,
    is_generic_fallback_reply,
    is_name_deferred,
    is_need_enrichment,
    merge_need_summaries,
)
from assistant_core.discovery import analyze_message
from assistant_core.prompts.livia import DEFAULT_REPLY
from conversations.models import Conversation
from tenants.models import Tenant


class ConversationTurnClassificationTests(TestCase):
    def test_name_deferral_variants(self):
        for message in (
            "não quero falar meu nome ainda",
            "prefiro não informar meu nome agora",
            "depois eu passo meu nome",
            "podemos continuar sem meu nome?",
        ):
            self.assertTrue(is_name_deferred(message), message)
            discovery = analyze_message(message)
            self.assertFalse(discovery.has_contact_data, message)

    def test_need_enrichment_is_generic(self):
        self.assertTrue(is_need_enrichment("eu vendo produtos para serralheria"))
        self.assertTrue(is_need_enrichment("vendo ferragens e ferramentas"))
        self.assertTrue(is_need_enrichment("minha loja vende ferramentas"))
        self.assertTrue(is_need_enrichment("trabalho com ferragens"))
        self.assertTrue(is_need_enrichment("atendo clínicas e consultórios"))
        self.assertFalse(is_need_enrichment("não quero falar meu nome ainda"))
        self.assertFalse(is_need_enrichment("quanto tempo demora?"))

    def test_direct_question_types(self):
        self.assertEqual(detect_question_type("quanto tempo para elaborar uma loja completa?"), "timeline")
        self.assertEqual(detect_question_type("quanto tempo demora?"), "timeline")
        self.assertEqual(detect_question_type("qual o prazo?"), "timeline")
        self.assertEqual(detect_question_type("vocês fazem pagamento online?"), "payment")
        self.assertEqual(detect_question_type("tem cálculo de frete?"), "shipping")
        self.assertEqual(detect_question_type("funciona no celular?"), "mobile")
        self.assertEqual(detect_question_type("dá para cadastrar meus produtos?"), "catalog")
        self.assertEqual(detect_question_type("vocês fazem manutenção depois?"), "maintenance")
        self.assertEqual(detect_question_type("como funciona?"), "how_it_works")
        self.assertEqual(detect_question_type("quanto custa?"), "price")

    def test_merge_need_summaries_accumulates(self):
        merged = merge_need_summaries("preciso de um site", "gostaria de uma loja virtual")
        self.assertIn("site", merged.lower())
        self.assertIn("loja virtual", merged.lower())
        again = merge_need_summaries(merged, "vendo ferragens e ferramentas")
        self.assertIn("ferragens", again.lower())

    def test_classify_uses_history_for_enrichment(self):
        tenant = Tenant.objects.create(name="T", slug="t", domain="t.example")
        conversation = Conversation.objects.create(tenant=tenant, session_id="ctx", lead_state="collect_name_company")
        discovery = analyze_message("vendo ferragens e ferramentas")
        turn = classify_conversation_turn(
            current_message="vendo ferragens e ferramentas",
            history=[{"role": "user", "content": "preciso de um site"}],
            conversation=conversation,
            discovery=discovery,
        )
        self.assertEqual(turn.kind, TurnKind.NEED_ENRICHMENT)
        self.assertIn("ferragens", extract_enrichment_snippet("vendo ferragens e ferramentas"))

    def test_generic_fallback_detector(self):
        self.assertTrue(is_generic_fallback_reply(DEFAULT_REPLY))
        self.assertFalse(is_generic_fallback_reply("O prazo depende do escopo."))
