"""Consulta informativa continua após lead/handoff qualificado."""

from __future__ import annotations

import json
import uuid

from django.test import TestCase, override_settings

from assistant_core.services import LiviaDecisionService
from assistant_core.state import LeadState, should_block_dialogue_for_locked_lead
from conversations.models import Conversation, HandoffRequest, Message
from integrations.models import OutboxEvent
from leads.models import LeadDraft
from tenants.models import AssistantProfile, Tenant


LOCKED_REPLY = "já encaminhei seus dados"


class ConsultativeAfterHandoffTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil-handoff",
            domain="scb-handoff.example",
            is_active=True,
        )
        AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia",
            business_domain="robótica e automação",
            short_description="Consultoria comercial e técnica.",
        )
        self.service = LiviaDecisionService()
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="qualified-session",
            lead_state=LeadState.QUALIFIED,
            is_qualified=True,
        )
        self.lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=LeadDraft.Status.QUALIFIED,
            need_summary="Robô Little Bot para escola",
            name="Maria",
            phone="11999998888",
        )

    def _history(self, *user_messages: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for msg in user_messages:
            items.append({"role": "user", "content": msg})
            items.append({"role": "assistant", "content": "ok"})
        return items

    def test_informational_question_does_not_trigger_lock(self):
        discovery = __import__("assistant_core.discovery", fromlist=["analyze_message"]).analyze_message(
            "quero saber mais sobre o Little bot"
        )
        self.assertFalse(
            should_block_dialogue_for_locked_lead(
                self.conversation,
                "quero saber mais sobre o Little bot",
                discovery,
            )
        )


    def test_need_statement_after_qualified_lead_is_consultative(self):
        discovery = __import__("assistant_core.discovery", fromlist=["analyze_message"]).analyze_message(
            "preciso de um site"
        )
        self.assertFalse(
            should_block_dialogue_for_locked_lead(
                self.conversation,
                "preciso de um site",
                discovery,
            )
        )

    def test_new_need_after_qualified_lead_does_not_create_handoff(self):
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "A Smart Control Brasil desenvolve sites institucionais, catálogos e sistemas web.\n"
            "[/KNOWLEDGE_BASE]"
        )
        initial_leads = LeadDraft.objects.filter(conversation=self.conversation).count()
        initial_handoffs = HandoffRequest.objects.filter(conversation=self.conversation).count()
        decision = self.service.generate_reply(
            [],
            "preciso de um site",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())
        self.assertIsNone(decision.handoff_request_id)
        self.assertEqual(LeadDraft.objects.filter(conversation=self.conversation).count(), initial_leads)
        self.assertEqual(HandoffRequest.objects.filter(conversation=self.conversation).count(), initial_handoffs)

    def test_existing_handoff_does_not_duplicate_for_natural_new_need(self):
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            lead_draft=self.lead,
            reason=HandoffRequest.Reason.QUALIFIED_LEAD,
            status=HandoffRequest.Status.PENDING,
            visitor_name="Maria",
            visitor_phone="11999998888",
        )
        initial_handoffs = HandoffRequest.objects.filter(conversation=self.conversation).count()
        decision = self.service.generate_reply(
            [],
            "agora preciso automatizar uma máquina",
            conversation=self.conversation,
            knowledge_context="[KNOWLEDGE_BASE]\nConteúdo:\nAutomação de máquinas industriais.\n[/KNOWLEDGE_BASE]",
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())
        self.assertEqual(HandoffRequest.objects.filter(conversation=self.conversation).count(), initial_handoffs)

    def test_explicit_quote_on_qualified_lead_still_blocks_duplicate(self):
        discovery = __import__("assistant_core.discovery", fromlist=["analyze_message"]).analyze_message(
            "Quero orçamento de novo"
        )
        self.assertTrue(
            should_block_dialogue_for_locked_lead(
                self.conversation,
                "Quero orçamento de novo",
                discovery,
            )
        )

    def test_turn1_little_bot_not_locked_reply(self):
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "Little Bot é um robô educacional para programação e STEM.\n"
            "[/KNOWLEDGE_BASE]"
        )
        initial_outbox = OutboxEvent.objects.filter(
            event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
            aggregate_id=str(self.lead.pk),
        ).count()
        decision = self.service.generate_reply(
            [],
            "quero saber mais sobre o Little bot",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())
        self.assertEqual(
            OutboxEvent.objects.filter(
                event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
                aggregate_id=str(self.lead.pk),
            ).count(),
            initial_outbox,
        )

    def test_turn2_more_information_not_locked_reply(self):
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "Little Bot oferece atividades práticas de robótica educacional.\n"
            "[/KNOWLEDGE_BASE]"
        )
        history = self._history("quero saber mais sobre o Little bot")
        decision = self.service.generate_reply(
            history,
            "me de mais informações",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())

    def test_autonomy_after_quote_still_consultative(self):
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "Autonomia de até 8 horas por ciclo.\n"
            "[/KNOWLEDGE_BASE]"
        )
        decision = self.service.generate_reply(
            self._history("quero um orçamento"),
            "qual a autonomia?",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())

    def test_existing_handoff_does_not_block_informational_reply(self):
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            lead_draft=self.lead,
            reason=HandoffRequest.Reason.EXPLICIT_REQUEST,
            status=HandoffRequest.Status.PENDING,
            visitor_name="Maria",
            visitor_phone="11999998888",
        )
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "Little Bot suporta atividades em sala de aula.\n"
            "[/KNOWLEDGE_BASE]"
        )
        decision = self.service.generate_reply(
            [],
            "como ele funciona?",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())

    def test_informational_does_not_create_duplicate_lead(self):
        initial_count = LeadDraft.objects.count()
        self.service.generate_reply(
            [],
            "quero saber mais sobre o Little bot",
            conversation=self.conversation,
            knowledge_context="[KNOWLEDGE_BASE]\nConteúdo:\nLittle Bot educacional.\n[/KNOWLEDGE_BASE]",
        )
        self.assertEqual(LeadDraft.objects.count(), initial_count)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
        LIVIA_AI_ENABLED=False,
    )
    def test_chat_api_two_turn_little_bot_sequence(self):
        from django.test import Client

        client = Client()
        session_id = f"little-bot-{uuid.uuid4().hex[:8]}"

        def post(message: str):
            payload = {
                "tenant": self.tenant.slug,
                "session_id": session_id,
                "message": message,
                "request_id": str(uuid.uuid4()),
            }
            return client.post(
                "/api/chat/",
                data=json.dumps(payload),
                content_type="application/json",
            )

        conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id=session_id,
            lead_state=LeadState.QUALIFIED,
            is_qualified=True,
        )
        LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=conversation,
            status=LeadDraft.Status.QUALIFIED,
            need_summary="Lead anterior",
            name="João",
            phone="11988887777",
        )

        first = post("quero saber mais sobre o Little bot")
        self.assertEqual(first.status_code, 200)
        self.assertNotIn(LOCKED_REPLY, first.json()["reply"].lower())

        second = post("me de mais informações")
        self.assertEqual(second.status_code, 200)
        self.assertNotIn(LOCKED_REPLY, second.json()["reply"].lower())
        self.assertEqual(LeadDraft.objects.filter(conversation=conversation).count(), 1)

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_explicit_quote_still_gets_locked_on_duplicate(self):
        from django.test import Client

        client = Client()
        payload = {
            "tenant": self.tenant.slug,
            "session_id": self.conversation.session_id,
            "message": "Quero orçamento de novo",
            "request_id": str(uuid.uuid4()),
        }
        response = client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(LOCKED_REPLY, response.json()["reply"].lower())
        self.assertEqual(OutboxEvent.objects.filter(aggregate_id=str(self.lead.pk)).count(), 0)

    def test_tenant_isolation(self):
        other = Tenant.objects.create(
            name="Outro Tenant",
            slug="outro-tenant-handoff",
            domain="outro.example",
            is_active=True,
        )
        other_conv = Conversation.objects.create(
            tenant=other,
            session_id="other-session",
            lead_state=LeadState.DISCOVERY,
        )
        decision = self.service.generate_reply(
            [],
            "quero saber mais sobre o Little bot",
            conversation=other_conv,
            knowledge_context="[KNOWLEDGE_BASE]\nConteúdo:\nProduto X.\n[/KNOWLEDGE_BASE]",
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())

    def test_fresh_informational_does_not_create_lead_or_handoff(self):
        fresh = Conversation.objects.create(
            tenant=self.tenant,
            session_id="fresh-discovery",
            lead_state=LeadState.DISCOVERY,
        )
        kb = "[KNOWLEDGE_BASE]\nConteúdo:\nDuno é um robô de limpeza.\n[/KNOWLEDGE_BASE]"
        decision = self.service.generate_reply(
            [],
            "quero saber mais sobre o Duno",
            conversation=fresh,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())
        self.assertFalse(LeadDraft.objects.filter(conversation=fresh).exists())
        self.assertFalse(HandoffRequest.objects.filter(conversation=fresh).exists())
        self.assertIn("duno", decision.reply.lower())

    def test_pronominal_followup_keeps_consultative(self):
        kb = (
            "[KNOWLEDGE_BASE]\nConteúdo:\n"
            "Little Bot é robô educacional.\n"
            "Autonomia de até 8 horas.\n"
            "Precisa de internet para algumas atividades.\n"
            "[/KNOWLEDGE_BASE]"
        )
        history = self._history("quero saber mais sobre o Little Bot", "como ele funciona?")
        decision = self.service.generate_reply(
            history,
            "e a autonomia?",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())

    def test_rag_content_used_after_qualified_lead(self):
        kb = "[KNOWLEDGE_BASE]\nConteúdo:\nLittle Bot é voltado para STEM e programação.\n[/KNOWLEDGE_BASE]"
        decision = self.service.generate_reply(
            [],
            "quero saber mais sobre o Little bot",
            conversation=self.conversation,
            knowledge_context=kb,
        )
        self.assertNotIn(LOCKED_REPLY, decision.reply.lower())
        self.assertTrue(
            "stem" in decision.reply.lower() or "programação" in decision.reply.lower() or "little bot" in decision.reply.lower()
        )

    def test_no_duplicate_handoff_on_consultative_turn(self):
        HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            lead_draft=self.lead,
            reason=HandoffRequest.Reason.QUALIFIED_LEAD,
            status=HandoffRequest.Status.PENDING,
            visitor_name="Maria",
            visitor_phone="11999998888",
        )
        initial_handoffs = HandoffRequest.objects.filter(conversation=self.conversation).count()
        initial_outbox = OutboxEvent.objects.filter(
            event_type=OutboxEvent.EventType.HANDOFF_CREATED,
            aggregate_id=str(HandoffRequest.objects.get(conversation=self.conversation).pk),
        ).count()
        self.service.generate_reply(
            [],
            "me de mais informações",
            conversation=self.conversation,
            knowledge_context="[KNOWLEDGE_BASE]\nConteúdo:\nLittle Bot educacional.\n[/KNOWLEDGE_BASE]",
        )
        self.assertEqual(HandoffRequest.objects.filter(conversation=self.conversation).count(), initial_handoffs)
        self.assertEqual(
            OutboxEvent.objects.filter(
                event_type=OutboxEvent.EventType.HANDOFF_CREATED,
                aggregate_id=str(HandoffRequest.objects.get(conversation=self.conversation).pk),
            ).count(),
            initial_outbox,
        )

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_explicit_quote_still_starts_commercial_on_fresh_session(self):
        from django.test import Client

        client = Client()
        session_id = f"quote-{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant": self.tenant.slug,
            "session_id": session_id,
            "message": "quero um orçamento para robô de limpeza",
            "request_id": str(uuid.uuid4()),
        }
        response = client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(LOCKED_REPLY, response.json()["reply"].lower())
        self.assertTrue(LeadDraft.objects.filter(conversation__session_id=session_id).exists())

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_human_handoff_still_works(self):
        from django.test import Client

        client = Client()
        session_id = f"human-{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant": self.tenant.slug,
            "session_id": session_id,
            "message": "quero falar com alguém",
            "request_id": str(uuid.uuid4()),
        }
        response = client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(LOCKED_REPLY, response.json()["reply"].lower())
        self.assertTrue(
            HandoffRequest.objects.filter(conversation__session_id=session_id).exists()
            or "contato" in response.json()["reply"].lower()
            or "humano" in response.json()["reply"].lower()
        )

    @override_settings(
        SMART360_LEAD_DISPATCH_ENABLED=False,
        SMART360_LEAD_DISPATCH_DRY_RUN=True,
    )
    def test_new_session_does_not_inherit_qualified_state(self):
        from django.test import Client

        client = Client()
        old_session = "old-qualified-session"
        Conversation.objects.create(
            tenant=self.tenant,
            session_id=old_session,
            lead_state=LeadState.QUALIFIED,
            is_qualified=True,
        )
        new_session = f"new-{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant": self.tenant.slug,
            "session_id": new_session,
            "message": "quero saber mais sobre o Orbit",
            "request_id": str(uuid.uuid4()),
        }
        response = client.post(
            "/api/chat/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(LOCKED_REPLY, response.json()["reply"].lower())
        new_conv = Conversation.objects.get(session_id=new_session)
        self.assertFalse(new_conv.is_qualified)
