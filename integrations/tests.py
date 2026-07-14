from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from integrations.smart360 import client as smart360_client
from integrations.smart360.client import Smart360GrowthClient
from integrations.smart360.contracts import LeadIngestPayload
from integrations.openai.client import OpenAIChatClient


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
                "notes": "",
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
        self.assertIn("notes", response.data["payload"])
        self.assertEqual(
            response.data["endpoint"],
            "https://smart360.example/api/v1/growth/leads/ingest/",
        )

    @override_settings(
        SMART360_BASE_URL="https://smart360.example",
        SMART360_M2M_TOKEN="token-123",
        SMART360_LEAD_DISPATCH_ENABLED=True,
        SMART360_LEAD_DISPATCH_DRY_RUN=False,
    )
    def test_ingest_lead_real_mode_posts_to_correct_url(self):
        client = Smart360GrowthClient(
            base_url="https://smart360.example",
            token="token-123",
            dry_run=False,
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

        response_mock = Mock()
        response_mock.ok = True
        response_mock.status_code = 201
        response_mock.json.return_value = {
            "success": True,
            "lead_id": 42,
            "created": True,
            "message": "ok",
            "external_id": "lead-42",
        }

        with patch("integrations.smart360.client.requests.post", return_value=response_mock) as post_mock:
            response = client.ingest_lead(payload)

        post_mock.assert_called_once_with(
            "https://smart360.example/api/v1/growth/leads/ingest/",
            json=payload.to_dict(),
            headers={
                "Authorization": "Bearer token-123",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        self.assertTrue(response.success)
        self.assertFalse(response.dry_run)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.external_id, "lead-42")

    def test_ingest_lead_http_error_returns_failure(self):
        client = Smart360GrowthClient(
            base_url="https://smart360.example",
            token="token-123",
            dry_run=False,
        )
        payload = LeadIngestPayload(tenant_slug="smart-control-brasil")

        response_mock = Mock()
        response_mock.ok = False
        response_mock.status_code = 400
        response_mock.json.return_value = {"detail": "payload inválido"}
        response_mock.text = '{"detail":"payload inválido"}'

        with patch("integrations.smart360.client.requests.post", return_value=response_mock):
            response = client.ingest_lead(payload)

        self.assertFalse(response.success)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.message, "payload inválido")

    def test_ingest_lead_invalid_json_returns_failure(self):
        client = Smart360GrowthClient(
            base_url="https://smart360.example",
            token="token-123",
            dry_run=False,
        )
        payload = LeadIngestPayload(tenant_slug="smart-control-brasil")

        response_mock = Mock()
        response_mock.ok = True
        response_mock.status_code = 200
        response_mock.json.side_effect = ValueError("invalid json")
        response_mock.text = "not-json"

        with patch("integrations.smart360.client.requests.post", return_value=response_mock):
            response = client.ingest_lead(payload)

        self.assertFalse(response.success)
        self.assertEqual(response.status_code, 200)
        self.assertIn("JSON inválida", response.message)
        self.assertEqual(response.data, {"detail": "not-json"})

    def test_ingest_lead_request_exception_returns_failure(self):
        client = Smart360GrowthClient(
            base_url="https://smart360.example",
            token="token-123",
            dry_run=False,
        )
        payload = LeadIngestPayload(tenant_slug="smart-control-brasil")

        with patch(
            "integrations.smart360.client.requests.post",
            side_effect=smart360_client.requests.RequestException("timeout"),
        ):
            response = client.ingest_lead(payload)

        self.assertFalse(response.success)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Falha ao enviar lead", response.message)


class OpenAIChatClientTests(SimpleTestCase):
    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=True, LIVIA_OPENAI_API_KEY="secret")
    def test_dry_run_does_not_call_api(self):
        client = OpenAIChatClient()

        with patch("integrations.openai.client.requests.post") as post_mock:
            result = client.create_chat_completion(messages=[{"role": "user", "content": "oi"}])

        post_mock.assert_not_called()
        self.assertFalse(result.success)
        self.assertTrue(result.dry_run)

    @override_settings(
        LIVIA_AI_ENABLED=True,
        LIVIA_AI_DRY_RUN=False,
        LIVIA_OPENAI_API_KEY="secret",
        LIVIA_OPENAI_MODEL="gpt-4.1-mini",
        LIVIA_OPENAI_TIMEOUT_SECONDS=3,
        LIVIA_OPENAI_MAX_OUTPUT_TOKENS=120,
        LIVIA_OPENAI_TEMPERATURE=0.2,
    )
    def test_real_mode_posts_expected_payload_without_logging_secret(self):
        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {"choices": [{"message": {"content": "Resposta IA"}}]}
        client = OpenAIChatClient()

        with patch("integrations.openai.client.requests.post", return_value=response_mock) as post_mock:
            result = client.create_chat_completion(messages=[{"role": "user", "content": "oi"}])

        post_mock.assert_called_once()
        kwargs = post_mock.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "gpt-4.1-mini")
        self.assertEqual(kwargs["json"]["max_tokens"], 120)
        self.assertEqual(kwargs["json"]["temperature"], 0.2)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(kwargs["timeout"], 3)
        self.assertTrue(result.success)
        self.assertEqual(result.text, "Resposta IA")

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="secret")
    def test_timeout_returns_failure_result(self):
        client = OpenAIChatClient()

        with patch("integrations.openai.client.requests.post", side_effect=smart360_client.requests.Timeout("timeout")):
            result = client.create_chat_completion(messages=[{"role": "user", "content": "oi"}])

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "Timeout")
