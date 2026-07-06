from django.test import SimpleTestCase

from integrations.smart360.client import Smart360GrowthClient
from integrations.smart360.contracts import LeadIngestPayload


class LeadIngestContractsTests(SimpleTestCase):
    def test_payload_defaults_and_serialization(self):
        payload = LeadIngestPayload(
            tenant_slug="smart-control-brasil",
            name="Maria",
            company="ACME",
            email="maria@example.com",
            phone="+5511999999999",
            city="São Paulo",
            need_summary="Quero melhorar o atendimento",
            source_page="https://example.com/landing",
            conversation_id="conv-123",
        )

        self.assertEqual(payload.tenant_slug, "smart-control-brasil")
        self.assertEqual(
            payload.to_dict(),
            {
                "tenant_slug": "smart-control-brasil",
                "name": "Maria",
                "company": "ACME",
                "email": "maria@example.com",
                "phone": "+5511999999999",
                "city": "São Paulo",
                "need_summary": "Quero melhorar o atendimento",
                "source_page": "https://example.com/landing",
                "conversation_id": "conv-123",
            },
        )


class Smart360GrowthClientTests(SimpleTestCase):
    def test_ingest_lead_dry_run_returns_mock_response(self):
        client = Smart360GrowthClient(
            base_url="https://smart360.example",
            token="token-123",
            dry_run=True,
        )
        payload = LeadIngestPayload(
            tenant_slug="smart-control-brasil",
            name="Maria",
            company="ACME",
            email="maria@example.com",
            phone="+5511999999999",
            city="São Paulo",
            need_summary="Quero melhorar o atendimento",
            source_page="https://example.com/landing",
            conversation_id="conv-123",
        )

        response = client.ingest_lead(payload)

        self.assertTrue(response.success)
        self.assertTrue(response.dry_run)
        self.assertEqual(response.status_code, 202)
        self.assertIn("dry_run ativo", response.message)
        self.assertEqual(response.data["payload"]["tenant_slug"], "smart-control-brasil")
        self.assertEqual(
            response.data["endpoint"],
            "https://smart360.example/api/smart360/leads/ingest/",
        )
