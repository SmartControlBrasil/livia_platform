from __future__ import annotations

from django.test import SimpleTestCase, TestCase, override_settings
from django.core import mail

from assistant_core.prompts.livia import DEFAULT_REPLY
from assistant_core.services.deterministic_synthesis import (
    is_generic_fallback_reply,
    prefer_contextual_reply_over_fallback,
    synthesize_deterministic_reply,
)
from conversations.models import Conversation, HandoffRequest, Message
from leads.services.handoff_notification import HandoffNotificationService
from tenants.models import AssistantProfile, Tenant


class DeterministicSynthesisTests(SimpleTestCase):
    def test_strips_bom_titles_and_metadata(self):
        context = """
[KNOWLEDGE_BASE]
Fonte: doc-1
Score: 0.9
Conteúdo: \ufeff# Bancadas de cozinha, pias e cooktops
## Fatos confirmados pelo site Pitondo
A Granimármores Pitondo desenvolve bancadas de cozinha sob medida em granito e mármore, com recortes para cooktop.
[/KNOWLEDGE_BASE]
"""
        reply = synthesize_deterministic_reply(context, base_reply=DEFAULT_REPLY)
        lowered = reply.lower()
        self.assertNotIn("fonte:", lowered)
        self.assertNotIn("score:", lowered)
        self.assertNotIn("\ufeff", reply)
        self.assertFalse(is_generic_fallback_reply(reply))
        self.assertIn("bancada", lowered)

    def test_generic_fallback_replaced_when_context_exists(self):
        reply = prefer_contextual_reply_over_fallback(
            knowledge_context="",
            need_summary="quero bancada para cozinha com cooktop",
            current_message="qual material",
        )
        self.assertFalse(is_generic_fallback_reply(reply))
        self.assertNotEqual(reply, DEFAULT_REPLY)


@override_settings(
    LIVIA_HANDOFF_NOTIFICATIONS_ENABLED=True,
    LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Lívia <noreply@example.com>",
)
class HandoffNotificationRealSendTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Pitondo", slug="granimarmores-pitondo")
        AssistantProfile.objects.create(
            tenant=self.tenant,
            name="Lívia",
            notification_email="contato@granimarmorespitondo.com.br",
            human_handoff_enabled=True,
        )
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="handoff-mail",
            source_page="https://www.granimarmorespitondo.com.br/",
        )
        Message.objects.create(conversation=self.conversation, role=Message.Role.USER, content="quero falar com alguém")
        Message.objects.create(conversation=self.conversation, role=Message.Role.ASSISTANT, content="Claro, vou registrar.")
        self.handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            reason=HandoffRequest.Reason.EXPLICIT_REQUEST,
            visitor_name="Maria Teste",
            visitor_phone="11999990000",
            summary="Cliente pediu atendimento humano.",
        )

    def test_sends_real_email_to_tenant_notification_address(self):
        result = HandoffNotificationService().notify(self.handoff)
        self.assertTrue(result.success)
        self.assertFalse(result.dry_run)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["contato@granimarmorespitondo.com.br"])
        body = mail.outbox[0].body.lower()
        self.assertIn("granimarmores-pitondo", body)
        self.assertIn("maria teste", body)
        self.assertNotIn("system prompt", body)

    def test_retry_does_not_send_second_email(self):
        first = HandoffNotificationService().notify(self.handoff)
        second = HandoffNotificationService().notify(self.handoff)
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(second.skipped)
        self.assertEqual(len(mail.outbox), 1)
