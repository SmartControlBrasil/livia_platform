import json
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from conversations.models import Conversation, HandoffRequest, Message
from integrations.openai.client import OpenAIChatResult
from assistant_core.state import LeadState
from assistant_core.services import LiviaDecisionService
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant


class LiviaDecisionServiceTests(TestCase):
    def setUp(self):
        self.service = LiviaDecisionService()

    def test_greeting(self):
        decision = self.service.generate_reply([], "Olá, tudo bem?")

        self.assertEqual(decision.intent, "greeting")
        self.assertIn("Olá! Sou a Lívia", decision.reply)

    def test_budget_request(self):
        decision = self.service.generate_reply([], "Quero orçamento para um sistema.")

        self.assertEqual(decision.intent, "quote_request")
        self.assertIn("necessidade principal", decision.reply)

    def test_technical_message(self):
        decision = self.service.generate_reply([], "Estou com um erro no painel e não funciona")

        self.assertEqual(decision.intent, "technical_question")
        self.assertIn("pré-análise", decision.reply)

    def test_contact_capture(self):
        decision = self.service.generate_reply([], "Meu nome é Maria, meu email é maria@exemplo.com")

        self.assertEqual(decision.intent, "contact_data")
        self.assertIn("atendimento", decision.reply.lower())

    def test_support_request(self):
        decision = self.service.generate_reply([], "Preciso de suporte no sistema")

        self.assertEqual(decision.intent, "support_request")
        self.assertIn("caso", decision.reply.lower())


class ChatApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )

    def test_chat_api_inactive_tenant_does_not_process_message(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "inactive-session",
            "message": "Olá, quero atendimento.",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reply"], "Este atendimento não está disponível no momento.")
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(HandoffRequest.objects.count(), 0)

    def test_chat_api_missing_tenant_returns_safe_response(self):
        payload = {
            "tenant": "tenant-inexistente",
            "session_id": "missing-tenant-session",
            "message": "Olá",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["reply"], "Este atendimento não está disponível no momento.")
        self.assertEqual(Conversation.objects.count(), 0)

    def test_chat_api_rejects_empty_message(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "empty-message-session",
            "message": "   ",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "message is required.")
        self.assertEqual(Conversation.objects.count(), 0)

    @override_settings(LIVIA_MAX_MESSAGE_LENGTH=12)
    def test_chat_api_rejects_long_message(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "long-message-session",
            "message": "Mensagem longa demais",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "message_too_long")
        self.assertIn("muito longa", response.json()["reply"])
        self.assertEqual(Conversation.objects.count(), 0)

    @override_settings(LIVIA_CHAT_RATE_LIMIT_ENABLED=True, LIVIA_CHAT_RATE_LIMIT_REQUESTS=1, LIVIA_CHAT_RATE_LIMIT_WINDOW_SECONDS=300)
    def test_chat_api_rate_limit_blocks_above_limit(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "rate-limit-session",
            "message": "Olá!",
        }

        first_response = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json", REMOTE_ADDR="10.0.0.1")
        second_response = self.client.post("/api/chat/", data=json.dumps({**payload, "session_id": "rate-limit-session-2"}), content_type="application/json", REMOTE_ADDR="10.0.0.1")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(second_response.json()["error"], "rate_limited")

    @override_settings(LIVIA_CHAT_RATE_LIMIT_ENABLED=True, LIVIA_CHAT_RATE_LIMIT_REQUESTS=1, LIVIA_CHAT_RATE_LIMIT_WINDOW_SECONDS=300)
    def test_chat_api_rate_limit_uses_tenant_and_ip(self):
        other_tenant = Tenant.objects.create(name="Outro Tenant", slug="outro-tenant", domain="outro.example")
        payload = {"tenant": self.tenant.slug, "session_id": "tenant-ip-1", "message": "Olá!"}
        other_payload = {"tenant": other_tenant.slug, "session_id": "tenant-ip-2", "message": "Olá!"}

        first_response = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json", REMOTE_ADDR="10.0.0.2")
        other_tenant_response = self.client.post("/api/chat/", data=json.dumps(other_payload), content_type="application/json", REMOTE_ADDR="10.0.0.2")
        other_ip_response = self.client.post("/api/chat/", data=json.dumps({**payload, "session_id": "tenant-ip-3"}), content_type="application/json", REMOTE_ADDR="10.0.0.3")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(other_tenant_response.status_code, 200)
        self.assertEqual(other_ip_response.status_code, 200)

    def test_chat_api_blocks_many_links_as_spam_without_lead_or_handoff(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "spam-links-session",
            "message": "Veja http://a.example www.b.example https://c.example para free money",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "spam_blocked")
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(HandoffRequest.objects.count(), 0)

    def test_chat_api_allows_normal_portuguese_message(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "normal-portuguese-session",
            "message": "Olá, preciso de orçamento para automação industrial.",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Conversation.objects.filter(session_id="normal-portuguese-session").exists())

    def test_chat_api_accepts_session_key_for_valid_tenant(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_key": "session-key-123",
            "message": "Olá!",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tenant"], self.tenant.slug)
        self.assertEqual(response.json()["session_key"], "session-key-123")
        self.assertTrue(Conversation.objects.filter(session_id="session-key-123").exists())

    @override_settings(DEBUG=True, LIVIA_ALLOWED_WIDGET_ORIGINS=[])
    def test_chat_api_options_allows_debug_localhost_origin(self):
        response = self.client.options(
            "/api/chat/",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type, authorization",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertIn("POST", response["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", response["Access-Control-Allow-Headers"])
        self.assertIn("Authorization", response["Access-Control-Allow-Headers"])

    def test_chat_api_uses_active_assistant_profile(self):
        AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia Pitondo",
            initial_message="Olá! Sou a Lívia da Pitondo. Como posso ajudar?",
            tone="consultivo e direto",
            primary_goal="qualificar oportunidades",
            is_active=True,
        )
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-profile",
            "message": "Olá!",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["assistant_name"], "Lívia Pitondo")
        self.assertEqual(response.json()["reply"], "Olá! Sou a Lívia da Pitondo. Como posso ajudar?")
        self.assertEqual(response.json()["initial_message"], "Olá! Sou a Lívia da Pitondo. Como posso ajudar?")


    def test_chat_api_human_request_returns_whatsapp_handoff_payload(self):
        AssistantProfile.objects.create(
            tenant=self.tenant,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="+55 (11) 51968-525",
            handoff_whatsapp_label="Falar com um especialista",
            handoff_whatsapp_message="Olá, vim pelo atendimento da Lívia.",
        )
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "whatsapp-handoff-session",
            "message": "quero falar com uma pessoa",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        handoff = HandoffRequest.objects.get(conversation__session_id="whatsapp-handoff-session")
        self.assertEqual(handoff.reason, HandoffRequest.Reason.EXPLICIT_REQUEST)
        self.assertEqual(data["reply"], "Claro. Use o botão do WhatsApp que apareceu na tela para falar com nossa equipe.")
        self.assertEqual(data["human_handoff"]["handoff_id"], handoff.pk)
        self.assertEqual(data["human_handoff"]["channel"], "whatsapp")
        self.assertEqual(data["human_handoff"]["url"], "https://wa.me/551151968525?text=Ol%C3%A1%2C+vim+pelo+atendimento+da+L%C3%ADvia.")
        payload_text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("token", payload_text.lower())
        self.assertNotIn("secret", payload_text.lower())
        self.assertNotIn("quero falar com uma pessoa", payload_text)

    def test_chat_api_human_request_reuses_existing_handoff(self):
        AssistantProfile.objects.create(
            tenant=self.tenant,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="551151968525",
        )
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "reuse-handoff-session",
            "message": "falar com atendente",
        }

        first = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")
        second = self.client.post("/api/chat/", data=json.dumps({**payload, "message": "me passa para um especialista"}), content_type="application/json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(HandoffRequest.objects.filter(conversation__session_id="reuse-handoff-session").count(), 1)
        self.assertEqual(first.json()["human_handoff"]["handoff_id"], second.json()["human_handoff"]["handoff_id"])

    def test_chat_api_normal_message_does_not_return_human_handoff(self):
        AssistantProfile.objects.create(
            tenant=self.tenant,
            human_handoff_enabled=True,
            human_handoff_channel="whatsapp",
            handoff_whatsapp_number="551151968525",
        )
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "normal-no-handoff",
            "message": "Quero orçamento para automação",
        }

        response = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("human_handoff", response.json())

    def test_chat_api_human_request_without_valid_whatsapp_keeps_contact_flow(self):
        AssistantProfile.objects.create(tenant=self.tenant, human_handoff_enabled=False)
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "disabled-whatsapp-handoff",
            "message": "quero atendimento humano",
        }

        response = self.client.post("/api/chat/", data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["human_handoff"], {"active": False})
        self.assertIn("atendimento humano", response.json()["reply"].lower())

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
        self.assertEqual(response.json()["intent"], "quote_request")

        lead_draft = LeadDraft.objects.get()
        self.assertEqual(lead_draft.conversation.session_id, "session-456")
        self.assertIn("orçamento", lead_draft.need_summary.lower())
        self.assertEqual(lead_draft.status, LeadDraft.Status.DRAFT)
        self.assertIn("nome", response.json()["reply"].lower())

    def test_chat_api_vague_budget_asks_area_before_contact(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-discovery-budget",
            "message": "Quero orçamento",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertIn("automação", response.json()["reply"].lower())
        self.assertIn("robótica", response.json()["reply"].lower())
        conversation = Conversation.objects.get(session_id="session-discovery-budget")
        self.assertEqual(conversation.lead_state, LeadState.COLLECT_NEED)

    def test_chat_api_budget_for_clp_identifies_automation_and_collects(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-clp",
            "message": "Preciso de orçamento para CLP Mitsubishi",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "quote_request")
        self.assertEqual(LeadDraft.objects.count(), 1)
        lead_draft = LeadDraft.objects.get()
        self.assertIn("clp", lead_draft.need_summary.lower())
        self.assertIn("nome", response.json()["reply"].lower())
        conversation = Conversation.objects.get(session_id="session-clp")
        self.assertEqual(conversation.lead_state, LeadState.COLLECT_NAME_COMPANY)

    def test_chat_api_robotics_interest_asks_environment(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-robotics",
            "message": "Vocês têm robô de limpeza?",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "commercial_interest")
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertIn("academia", response.json()["reply"].lower())
        self.assertIn("hospital", response.json()["reply"].lower())

    def test_chat_api_maintenance_interest_asks_technical_context(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-maintenance",
            "message": "Preciso arrumar uma esteira",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertIn("esteira", response.json()["reply"].lower())
        self.assertIn("problema", response.json()["reply"].lower())

    def test_chat_api_software_web_interest_is_identified(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-software-web",
            "message": "Quero um site com IA",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "commercial_interest")
        self.assertEqual(LeadDraft.objects.count(), 1)
        lead_draft = LeadDraft.objects.get()
        self.assertIn("site", lead_draft.need_summary.lower())

    def test_chat_api_creates_lead_draft_on_commercial_interest(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-457",
            "message": "Quero desenvolver um sistema para minha empresa.",
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
        self.assertEqual(response.json()["intent"], "commercial_interest")
        self.assertEqual(lead_draft.status, LeadDraft.Status.DRAFT)
        self.assertIn("nome", response.json()["reply"].lower())

    def test_chat_api_transitions_to_collect_name_company_on_commercial_intent(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-state-1",
            "message": "Quero orçamento para um sistema de atendimento ao cliente.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(session_id="session-state-1")
        self.assertEqual(conversation.lead_state, LeadState.COLLECT_NAME_COMPANY)
        self.assertFalse(conversation.is_qualified)

    def test_chat_api_phone_without_need_creates_draft_and_asks_need(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-phone-only",
            "message": "11999999999",
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
        self.assertEqual(lead_draft.phone, "11999999999")
        self.assertIn("necessidade", response.json()["reply"].lower())

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_chat_api_creates_lead_draft_when_contact_and_need_are_together(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-458",
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
        self.assertEqual(response.json()["intent"], "contact_data")
        lead_draft = LeadDraft.objects.get()
        self.assertEqual(lead_draft.status, LeadDraft.Status.SENT_TO_CRM)
        self.assertTrue(lead_draft.crm_external_id.startswith("dry-run-smart-control-brasil-"))
        self.assertIsNotNone(lead_draft.sent_to_crm_at)
        self.assertIn("encaminhar", response.json()["reply"].lower())

    def test_chat_api_does_not_create_lead_draft_on_technical_question(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-459",
            "message": "Estou com um erro no painel e não funciona.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(response.json()["intent"], "technical_question")

    def test_chat_api_does_not_create_lead_draft_on_support_request(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-460",
            "message": "Preciso de suporte no sistema.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(response.json()["intent"], "support_request")

    def test_chat_api_login_question_is_support_and_does_not_create_lead(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-login-support",
            "message": "Como faço login?",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "support_request")
        self.assertEqual(LeadDraft.objects.count(), 0)

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

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_chat_api_does_not_dispatch_duplicate_for_already_qualified_lead(self):
        first_payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-no-duplicate",
            "message": "Sou Maria da ACME, meu telefone é 11999998888 e quero orçamento para automação industrial de atendimento.",
            "source_page": "https://example.com/pagina",
        }
        first_response = self.client.post(
            "/api/chat/",
            data=json.dumps(first_payload),
            content_type="application/json",
        )
        self.assertEqual(first_response.status_code, 200)
        lead_draft = LeadDraft.objects.get()
        first_external_id = lead_draft.crm_external_id
        self.assertEqual(lead_draft.status, LeadDraft.Status.SENT_TO_CRM)

        second_payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-no-duplicate",
            "message": "Quero orçamento de novo",
            "source_page": "https://example.com/pagina",
        }
        second_response = self.client.post(
            "/api/chat/",
            data=json.dumps(second_payload),
            content_type="application/json",
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 1)
        lead_draft.refresh_from_db()
        self.assertEqual(lead_draft.crm_external_id, first_external_id)
        self.assertIn("já encaminhei", second_response.json()["reply"].lower())

    def test_chat_api_does_not_force_lead_on_ambiguous_message(self):
        payload = {
            "tenant": self.tenant.slug,
            "session_id": "session-790",
            "message": "Tenho uma dúvida rápida sobre o sistema.",
            "source_page": "https://example.com/pagina",
        }

        response = self.client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeadDraft.objects.count(), 0)
        self.assertEqual(response.json()["intent"], "unknown")


class LiviaDecisionKnowledgeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smartcontrolbrasil.com.br",
        )
        self.service = LiviaDecisionService()

    def test_livia_decision_uses_knowledge_when_available(self):
        from knowledge_base.models import KnowledgeDocument

        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="HygiBot / robô de limpeza",
            slug="hygibot-robo-limpeza",
            content="HygiBot é uma solução de robótica de limpeza para grandes áreas e ambientes profissionais.",
            tags=["robotics", "xyron", "hygibot", "limpeza", "robo"],
        )
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="knowledge-session")

        decision = self.service.generate_reply([], "Vocês têm robô de limpeza?", conversation=conversation)

        self.assertEqual(decision.intent, "commercial_interest")
        self.assertIn("HygiBot", decision.reply)
        self.assertIn("ambientes profissionais", decision.reply)
        self.assertIn("ambiente", decision.reply.lower())

    def test_livia_decision_keeps_current_reply_without_knowledge(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="no-knowledge-session")

        decision = self.service.generate_reply([], "Vocês têm robô de limpeza?", conversation=conversation)

        self.assertEqual(decision.intent, "commercial_interest")
        self.assertNotIn("HygiBot", decision.reply)
        self.assertIn("academia", decision.reply.lower())
        self.assertIn("hospital", decision.reply.lower())


class LiviaHandoffWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smartcontrolbrasil.com.br",
        )
        self.service = LiviaDecisionService()

    def test_explicit_human_request_creates_handoff(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="handoff-human")

        decision = self.service.generate_reply([], "quero falar com um vendedor", conversation=conversation)

        handoff = HandoffRequest.objects.get(conversation=conversation)
        self.assertEqual(handoff.reason, HandoffRequest.Reason.EXPLICIT_REQUEST)
        self.assertEqual(handoff.status, HandoffRequest.Status.PENDING)
        self.assertIn("atendimento humano", decision.reply.lower())

    def test_call_me_request_with_phone_creates_handoff_with_contact(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="handoff-call")

        decision = self.service.generate_reply([], "me liga, sou Marcelo, telefone 11999999999", conversation=conversation)

        handoff = HandoffRequest.objects.get(conversation=conversation)
        self.assertEqual(handoff.visitor_name, "Marcelo")
        self.assertEqual(handoff.visitor_phone, "11999999999")
        self.assertIn("Marcelo", decision.reply)
        self.assertIn("Registrei o pedido de contato", decision.reply)

    @override_settings(SMART360_LEAD_DISPATCH_ENABLED=False, SMART360_LEAD_DISPATCH_DRY_RUN=True)
    def test_qualified_lead_creates_handoff_without_duplicate(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="handoff-qualified")
        message = "Sou Maria da ACME, meu telefone é 11999998888 e preciso de automação industrial."

        self.service.generate_reply([], message, conversation=conversation)
        self.service.generate_reply([], message, conversation=conversation)

        self.assertEqual(HandoffRequest.objects.filter(conversation=conversation).count(), 1)
        handoff = HandoffRequest.objects.get(conversation=conversation)
        self.assertEqual(handoff.reason, HandoffRequest.Reason.QUALIFIED_LEAD)
        self.assertIn("automação", handoff.summary.lower())

    def test_urgent_technical_conversation_creates_high_priority_handoff(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="handoff-urgent")

        self.service.generate_reply([], "CLP parou e a máquina está parada urgente", conversation=conversation)

        handoff = HandoffRequest.objects.get(conversation=conversation)
        self.assertEqual(handoff.priority, HandoffRequest.Priority.HIGH)
        self.assertIn(handoff.reason, {
            HandoffRequest.Reason.EMERGENCY_OR_URGENT,
            HandoffRequest.Reason.TECHNICAL_COMPLEXITY,
        })

    def test_simple_support_request_does_not_create_urgent_handoff(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="handoff-support-simple")

        self.service.generate_reply([], "Preciso de suporte no sistema", conversation=conversation)

        self.assertFalse(HandoffRequest.objects.filter(conversation=conversation).exists())

    def test_handoff_summary_and_source_page_are_filled(self):
        conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="handoff-source",
            source_page="https://example.com/origem",
        )

        self.service.generate_reply([], "quero falar com alguém sobre robô de limpeza", conversation=conversation)

        handoff = HandoffRequest.objects.get(conversation=conversation)
        self.assertEqual(handoff.source_page, "https://example.com/origem")
        self.assertIn("Resumo da Lívia", handoff.summary)
        self.assertIn("Última mensagem", handoff.summary)

    def test_default_handoff_notification_settings_are_safe(self):
        self.assertFalse(settings.LIVIA_HANDOFF_NOTIFICATIONS_ENABLED)
        self.assertTrue(settings.LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN)
        self.assertEqual(settings.LIVIA_HANDOFF_NOTIFICATION_EMAIL, "contato@smartcontrolbrasil.com.br")


class FakeAIClient:
    def __init__(self, result=None, exc=None):
        self.result = result or OpenAIChatResult(text="", success=False, dry_run=True)
        self.exc = exc
        self.calls = []

    def create_chat_completion(self, *, messages):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return self.result


class LiviaOptionalAIResponseTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil-ai",
            domain="smartcontrolbrasil.com.br",
        )
        self.profile = AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia Smart",
            initial_message="Olá! Sou a Lívia Smart.",
            tone="consultivo e direto",
            primary_goal="qualificar oportunidades técnicas",
            use_ai=True,
            is_active=True,
        )

    def test_ai_disabled_by_default_keeps_deterministic_reply(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-default-off")
        ai_client = FakeAIClient(OpenAIChatResult(text="Resposta por IA", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        self.assertEqual(ai_client.calls, [])
        self.assertIn("nome", decision.reply.lower())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=True)
    def test_ai_dry_run_keeps_deterministic_reply(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-dry-run")
        service = LiviaDecisionService()

        with patch("integrations.openai.client.requests.post") as post_mock:
            decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        post_mock.assert_not_called()
        self.assertIn("nome", decision.reply.lower())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="")
    def test_without_api_key_keeps_fallback(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-no-key")
        service = LiviaDecisionService()

        with patch("integrations.openai.client.requests.post") as post_mock:
            decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        post_mock.assert_not_called()
        self.assertIn("nome", decision.reply.lower())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_client_timeout_keeps_fallback(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-timeout")
        service = LiviaDecisionService(ai_client=FakeAIClient(exc=TimeoutError("timeout")))

        decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        self.assertIn("nome", decision.reply.lower())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_valid_ai_response_replaces_only_reply_text(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-valid")
        ai_client = FakeAIClient(OpenAIChatResult(text="Resposta mais natural da IA.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        self.assertEqual(decision.intent, "quote_request")
        self.assertEqual(decision.reply, "Resposta mais natural da IA.")
        self.assertEqual(LeadDraft.objects.count(), 1)

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_lead_state_is_not_changed_by_ai(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-state")
        ai_client = FakeAIClient(OpenAIChatResult(text="Claro, me conte a área principal.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        service.generate_reply([], "Quero orçamento", conversation=conversation, assistant_profile=self.profile)

        conversation.refresh_from_db()
        self.assertEqual(conversation.lead_state, LeadState.COLLECT_NEED)

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_handoff_does_not_depend_on_ai(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-handoff")
        ai_client = FakeAIClient(OpenAIChatResult(text="Vou te conectar com o time.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        decision = service.generate_reply([], "quero falar com um vendedor", conversation=conversation, assistant_profile=self.profile)

        self.assertEqual(decision.reply, "Vou te conectar com o time.")
        self.assertTrue(HandoffRequest.objects.filter(conversation=conversation).exists())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_knowledge_context_and_profile_enter_prompt(self):
        from knowledge_base.models import KnowledgeDocument

        KnowledgeDocument.objects.create(
            tenant=self.tenant,
            title="HygiBot",
            slug="hygibot-ai",
            content="HygiBot atende limpeza profissional em grandes áreas.",
            tags=["hygibot", "limpeza"],
        )
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-prompt")
        ai_client = FakeAIClient(OpenAIChatResult(text="HygiBot pode ajudar nesse cenário.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        service.generate_reply([], "Vocês têm robô de limpeza HygiBot?", conversation=conversation, assistant_profile=self.profile)

        prompt_text = "\n".join(message["content"] for message in ai_client.calls[0])
        self.assertIn("HygiBot", prompt_text)
        self.assertIn("consultivo e direto", prompt_text)
        self.assertIn("qualificar oportunidades técnicas", prompt_text)

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_profile_use_ai_false_blocks_ai_even_when_global_enabled(self):
        self.profile.use_ai = False
        self.profile.save(update_fields=["use_ai"])
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-profile-off")
        ai_client = FakeAIClient(OpenAIChatResult(text="Resposta por IA", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        self.assertEqual(ai_client.calls, [])
        self.assertIn("nome", decision.reply.lower())

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="key-test")
    def test_profile_use_ai_true_allows_ai_attempt(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-profile-on")
        ai_client = FakeAIClient(OpenAIChatResult(text="Texto refinado.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        decision = service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        self.assertEqual(len(ai_client.calls), 1)
        self.assertEqual(decision.reply, "Texto refinado.")

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="secret-key-123")
    def test_prompt_does_not_contain_api_key(self):
        conversation = Conversation.objects.create(tenant=self.tenant, session_id="ai-no-secret")
        ai_client = FakeAIClient(OpenAIChatResult(text="Texto refinado.", success=True, dry_run=False))
        service = LiviaDecisionService(ai_client=ai_client)

        service.generate_reply([], "Quero orçamento para um sistema", conversation=conversation, assistant_profile=self.profile)

        prompt_text = "\n".join(message["content"] for message in ai_client.calls[0])
        self.assertNotIn("secret-key-123", prompt_text)
