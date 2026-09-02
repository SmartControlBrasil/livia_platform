import json
import uuid

from django.core.cache import cache
from django.test import TestCase

from assistant_core.conversation_turns import is_generic_fallback_reply, lead_has_name_deferred
from assistant_core.prompts.livia import DEFAULT_REPLY
from conversations.models import Conversation
from leads.models import LeadDraft
from tenants.models import Tenant


class MultiTurnCommercialConversationTests(TestCase):
    def setUp(self):
        cache.clear()
        original_post = self.client.post

        def post_with_request_id(path, *args, **kwargs):
            if path == "/api/chat/" and kwargs.get("content_type") == "application/json" and "data" in kwargs:
                try:
                    payload = json.loads(kwargs["data"])
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict) and "request_id" not in payload:
                    payload["request_id"] = str(uuid.uuid4())
                    kwargs["data"] = json.dumps(payload)
                    kwargs.setdefault("HTTP_X_LIVIA_REQUEST_ID", payload["request_id"])
            return original_post(path, *args, **kwargs)

        self.client.post = post_with_request_id
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )
        self.session_id = "multi-turn-ecommerce"

    def _chat(self, message: str, session_id: str | None = None) -> dict:
        response = self.client.post(
            "/api/chat/",
            data=json.dumps({
                "tenant": self.tenant.slug,
                "session_id": session_id or self.session_id,
                "message": message,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _assert_no_generic_fallback(self, reply: str):
        self.assertFalse(is_generic_fallback_reply(reply), reply)
        self.assertNotEqual(reply.strip(), DEFAULT_REPLY)

    def _assert_no_invented_commercials(self, reply: str):
        lowered = reply.lower()
        self.assertNotRegex(reply, r"\b\d+\s*(dias|semanas|meses)\b")
        self.assertNotIn("r$", lowered)
        self.assertNotIn("garantia de", lowered)
        self.assertNotIn("stripe", lowered)
        self.assertNotIn("shopify", lowered)

    def test_smart_control_ecommerce_conversation_keeps_context(self):
        turns = [
            "preciso de um site",
            "gostaria de uma loja virtual",
            "não quero falar meu nome ainda",
            "eu vendo produtos para serralheria",
            "vendo ferragens e ferramentas",
            "quanto tempo para elaborar uma loja completa?",
        ]
        replies = []
        for message in turns:
            payload = self._chat(message)
            replies.append(payload["reply"])
            self.assertEqual(payload["intent"], "commercial_interest")
            self._assert_no_generic_fallback(payload["reply"])
            self._assert_no_invented_commercials(payload["reply"])

        lead = LeadDraft.objects.get()
        need = lead.need_summary.lower()
        self.assertIn("site", need)
        self.assertTrue("loja" in need or "virtual" in need)
        self.assertTrue("serralheria" in need or "ferragens" in need or "ferramentas" in need)
        self.assertTrue(lead_has_name_deferred(lead))
        self.assertFalse(lead.name)
        self.assertIn("sem o nome", replies[2].lower())

        last = replies[-1].lower()
        self.assertTrue(any(token in last for token in ("prazo", "tempo", "estimativa")))
        self.assertNotIn("seu nome", last)

    def test_name_deferral_and_question_variants(self):
        self._chat("preciso de um site institucional", session_id="variants")
        self._chat("gostaria de uma loja virtual", session_id="variants")
        refusal = self._chat("prefiro não informar meu nome agora", session_id="variants")
        self.assertEqual(refusal["intent"], "commercial_interest")
        self._assert_no_generic_fallback(refusal["reply"])
        self.assertIn("nome", refusal["reply"].lower())

        products = self._chat("minha loja vende ferramentas", session_id="variants")
        self._assert_no_generic_fallback(products["reply"])
        self.assertIn("ferramentas", products["reply"].lower())

        hardware = self._chat("trabalho com ferragens", session_id="variants")
        self._assert_no_generic_fallback(hardware["reply"])
        self.assertTrue("ferrag" in hardware["reply"].lower() or "anotei" in hardware["reply"].lower())

        for question, expected in (
            ("quanto tempo demora?", "prazo"),
            ("qual o prazo?", "prazo"),
            ("vocês fazem pagamento online?", "pagamento"),
            ("tem cálculo de frete?", "frete"),
        ):
            payload = self._chat(question, session_id="variants")
            self.assertEqual(payload["intent"], "commercial_interest")
            self._assert_no_generic_fallback(payload["reply"])
            self._assert_no_invented_commercials(payload["reply"])
            self.assertIn(expected, payload["reply"].lower())

        conversation = Conversation.objects.get(session_id="variants")
        self.assertFalse(conversation.is_qualified)

    def test_other_segments_stay_generic(self):
        clinic = self._chat("preciso de um site para minha clínica", session_id="clinic")
        self.assertEqual(clinic["intent"], "commercial_interest")
        self._assert_no_generic_fallback(clinic["reply"])

        industry = self._chat("quero um sistema web para a indústria", session_id="industry")
        self.assertEqual(industry["intent"], "commercial_interest")

        clothes = self._chat("quero um ecommerce para loja de roupas", session_id="clothes")
        self.assertEqual(clothes["intent"], "commercial_interest")

        automation = self._chat("preciso de automação para a linha de produção", session_id="auto")
        self.assertEqual(automation["intent"], "commercial_interest")
        self._assert_no_generic_fallback(automation["reply"])
