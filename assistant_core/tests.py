import json

from django.test import TestCase

from conversations.models import Conversation, Message
from assistant_core.services import LiviaDecisionService
from leads.models import LeadDraft
from tenants.models import Tenant


class LiviaDecisionServiceTests(TestCase):
    def setUp(self):
        self.service = LiviaDecisionService()

    def test_greeting(self):
        decision = self.service.generate_reply([], "Olá, tudo bem?")

        self.assertEqual(decision.intent, "greeting")
        self.assertIn("Olá! Sou a Lívia", decision.reply)

    def test_budget_request(self):
        decision = self.service.generate_reply([], "Quero orçamento para um sistema.")

        self.assertEqual(decision.intent, "budget")
        self.assertIn("necessidade principal", decision.reply)

    def test_technical_message(self):
        decision = self.service.generate_reply([], "Estou com um erro no painel e não funciona")

        self.assertEqual(decision.intent, "technical")
        self.assertIn("pré-análise", decision.reply)

    def test_contact_capture(self):
        decision = self.service.generate_reply([], "Meu nome é Maria, meu email é maria@exemplo.com")

        self.assertEqual(decision.intent, "contact")
        self.assertIn("atendimento", decision.reply.lower())


class ChatApiTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )

    def test_chat_api_creates_conversation_and_messages(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-123",
            "message": "Olá, quero saber mais.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(response.json()["intent"], "greeting")

        conversation = Conversation.objects.get()
        self.assertEqual(conversation.tenant, self.tenant)
        self.assertEqual(conversation.session_id, "session-123")
        self.assertEqual(conversation.source_page, "https://example.com/pagina")

        user_message = Message.objects.filter(role=Message.Role.USER).get()
        assistant_message = Message.objects.filter(role=Message.Role.ASSISTANT).get()
        self.assertEqual(user_message.content, "Olá, quero saber mais.")
        self.assertIn("Olá! Sou a Lívia", assistant_message.content)

    def test_chat_api_creates_lead_draft_on_budget_request(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-456",
            "message": "Quero orçamento para um sistema de atendimento.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 1)

        lead_draft = LeadDraft.objects.get()
        self.assertEqual(lead_draft.conversation.session_id, "session-456")
        self.assertIn("orçamento", lead_draft.need_summary.lower())
        self.assertEqual(lead_draft.status, LeadDraft.Status.DRAFT)
        self.assertIn("nome", response.json()["reply"].lower())

    def test_chat_api_confirms_when_lead_is_qualified(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-457",
            "message": "Sou Maria da ACME, meu telefone é 11999998888 e preciso de automação industrial.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 1)
        lead_draft = LeadDraft.objects.get()
        self.assertEqual(lead_draft.status, LeadDraft.Status.QUALIFIED)
        self.assertIn("encaminhar", response.json()["reply"].lower())

    def test_chat_api_does_not_create_lead_draft_on_greeting(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-789",
            "message": "Olá!",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(response.json()["intent"], "greeting")
