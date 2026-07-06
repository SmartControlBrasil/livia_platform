from django.test import TestCase
from unittest.mock import Mock

from conversations.models import Conversation
from leads.models import LeadDraft
from leads.services.crm_dispatch import CRMDispatchService
from leads.services import LeadCaptureService
from integrations.smart360.contracts import LeadIngestResponse
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

    def test_prompt_skips_name_when_already_informed(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Sou Maria.",
            history=[],
        )

        reply = self.service.build_next_prompt(result.lead_draft, result.missing_fields)

        self.assertIn("necessidade principal", reply.lower())
        self.assertNotIn("nome", reply.lower())

    def test_prompt_asks_next_missing_field(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Sou Maria da ACME. Preciso de automação industrial.",
            history=[],
        )

        reply = self.service.build_next_prompt(result.lead_draft, result.missing_fields)

        self.assertIn("telefone/WhatsApp ou e-mail", reply)
        self.assertNotIn("nome", reply.lower())

    def test_marks_qualified_with_minimum_data(self):
        result = self.service.capture_from_message(
            conversation=self.conversation,
            message="Sou Maria da ACME, meu telefone é 11999998888 e preciso de automação industrial.",
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
        self.assertIn("need_summary", result.missing_fields)
        self.assertIn("name_or_company", result.missing_fields)
        self.assertIn("phone_or_email", result.missing_fields)


class CRMDispatchServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Smart Control Brasil",
            slug="smart-control-brasil",
            domain="smart-control-brasil.example",
            is_active=True,
        )
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="session-crm",
            source_page="https://example.com/demo",
        )
        self.lead_draft = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            name="Maria",
            company="ACME",
            email="maria@exemplo.com",
            phone="11999998888",
            city="São Paulo",
            need_summary="Preciso de automação industrial.",
            status=LeadDraft.Status.QUALIFIED,
        )

    def test_build_payload(self):
        service = CRMDispatchService(client=Mock())

        payload = service.build_payload(self.lead_draft)

        self.assertEqual(payload.tenant_slug, "smart-control-brasil")
        self.assertEqual(payload.name, "Maria")
        self.assertEqual(payload.company, "ACME")
        self.assertEqual(payload.email, "maria@exemplo.com")
        self.assertEqual(payload.phone, "11999998888")
        self.assertEqual(payload.city, "São Paulo")
        self.assertEqual(payload.need_summary, "Preciso de automação industrial.")
        self.assertEqual(payload.source_page, "https://example.com/demo")
        self.assertEqual(payload.conversation_id, "session-crm")

    def test_dispatch_success_marks_sent_to_crm(self):
        client = Mock()
        client.dry_run = True
        client.ingest_lead.return_value = LeadIngestResponse(
            success=True,
            dry_run=True,
            message="ok",
            status_code=202,
            external_id="dry-run-smart-control-brasil-session-crm",
            data={},
        )
        service = CRMDispatchService(client=client)

        with self.assertLogs("leads.services.crm_dispatch", level="INFO") as logs:
            result = service.dispatch_if_qualified(self.lead_draft)

        self.assertTrue(result.attempted)
        self.assertTrue(result.success)
        self.assertEqual(self.lead_draft.status, LeadDraft.Status.SENT_TO_CRM)
        self.assertEqual(self.lead_draft.crm_external_id, "dry-run-smart-control-brasil-session-crm")
        self.assertIsNotNone(self.lead_draft.sent_to_crm_at)
        client.ingest_lead.assert_called_once()
        joined_logs = "\n".join(logs.output)
        self.assertIn("event=crm_dispatch_attempt", joined_logs)
        self.assertIn("event=crm_dispatch_success_dry_run", joined_logs)
        self.assertIn("lead_draft_id=", joined_logs)
        self.assertIn("tenant_slug=smart-control-brasil", joined_logs)

    def test_non_qualified_lead_is_not_sent(self):
        self.lead_draft.status = LeadDraft.Status.DRAFT
        self.lead_draft.save(update_fields=["status"])
        client = Mock()
        client.dry_run = True
        service = CRMDispatchService(client=client)

        with self.assertLogs("leads.services.crm_dispatch", level="INFO") as logs:
            result = service.dispatch_if_qualified(self.lead_draft)

        self.assertFalse(result.attempted)
        self.assertFalse(result.success)
        client.ingest_lead.assert_not_called()
        self.lead_draft.refresh_from_db()
        self.assertEqual(self.lead_draft.status, LeadDraft.Status.DRAFT)
        self.assertIn("event=crm_dispatch_ignored_not_qualified", "\n".join(logs.output))

    def test_already_sent_lead_is_not_resent(self):
        self.lead_draft.status = LeadDraft.Status.SENT_TO_CRM
        self.lead_draft.crm_external_id = "dry-run-existing"
        self.lead_draft.save(update_fields=["status", "crm_external_id"])
        client = Mock()
        client.dry_run = True
        service = CRMDispatchService(client=client)

        with self.assertLogs("leads.services.crm_dispatch", level="INFO") as logs:
            result = service.dispatch_if_qualified(self.lead_draft)

        self.assertFalse(result.attempted)
        self.assertTrue(result.success)
        client.ingest_lead.assert_not_called()
        self.assertIn("event=crm_dispatch_ignored_already_sent", "\n".join(logs.output))

    def test_failure_marks_failed(self):
        client = Mock()
        client.dry_run = True
        client.ingest_lead.return_value = LeadIngestResponse(
            success=False,
            dry_run=True,
            message="dry run error",
            status_code=500,
            external_id=None,
            data={},
        )
        service = CRMDispatchService(client=client)

        with self.assertLogs("leads.services.crm_dispatch", level="INFO") as logs:
            result = service.dispatch_if_qualified(self.lead_draft)

        self.assertTrue(result.attempted)
        self.assertFalse(result.success)
        self.lead_draft.refresh_from_db()
        self.assertEqual(self.lead_draft.status, LeadDraft.Status.FAILED)
        self.assertEqual(self.lead_draft.crm_error, "dry run error")
        self.assertIn("event=crm_dispatch_failure_dry_run", "\n".join(logs.output))
