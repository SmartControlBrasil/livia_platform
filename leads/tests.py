from django.test import TestCase

from conversations.models import Conversation
from leads.models import LeadDraft
from leads.services import LeadCaptureService
from tenants.models import Tenant


class LeadCaptureServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="session-1",
            source_page="https://example.com/demo",
        )
        self.service = LeadCaptureService()

    def test_extracts_email_and_phone(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Quero orçamento. Meu email é maria@exemplo.com e meu WhatsApp é +55 (11) 99999-8888",
            history=[],
        )

        self.assertEqual(result.lead_draft.email, "maria@exemplo.com")
        self.assertTrue(result.lead_draft.phone)
        self.assertIn("orçamento", result.lead_draft.need_summary.lower())

    def test_get_or_create_lead_draft(self):
        lead_draft = self.service.get_or_create_lead_draft(self.conversation)

        self.assertEqual(LeadDraft.objects.count(), 1)
        self.assertEqual(lead_draft.conversation, self.conversation)
        self.assertEqual(lead_draft.tenant, self.tenant)

    def test_updates_existing_lead_draft(self):
        first = self.service.capture_from_message(
            conversation=self.conversation,
            message="Quero orçamento para um sistema de atendimento.",
            history=[],
        )
        second = self.service.capture_from_message(
            conversation=self.conversation,
            message="Meu nome é Maria e meu telefone é 11999998888",
            history=[{"role": "user", "content": "Quero orçamento para um sistema de atendimento."}],
        )

        self.assertEqual(first.lead_draft.pk, second.lead_draft.pk)
        self.assertEqual(second.lead_draft.name, "Maria")
        self.assertEqual(second.lead_draft.phone, "11999998888")
        self.assertIn("sistema de atendimento", second.lead_draft.need_summary.lower())

    def test_marks_qualified_with_minimum_data(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Sou Maria, meu e-mail é maria@exemplo.com e preciso de orçamento para automação industrial.",
            history=[],
        )

        self.assertTrue(result.is_qualified)
        self.assertEqual(result.lead_draft.status, LeadDraft.Status.QUALIFIED)

    def test_missing_fields_when_partial_data(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Quero orçamento.",
            history=[],
        )

        self.assertFalse(result.is_qualified)
        self.assertIn("nome", result.missing_fields)
        self.assertIn("telefone ou e-mail", result.missing_fields)
