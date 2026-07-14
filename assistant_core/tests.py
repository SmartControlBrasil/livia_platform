import json

from django.conf import settings
from django.test import TestCase, override_settings

from conversations.models import Conversation, HandoffRequest, Message
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
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )

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
