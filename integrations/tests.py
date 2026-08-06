import uuid
import threading
import unittest
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

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
        response_mock.json.return_value = {
            "choices": [{"message": {"content": "Resposta IA"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
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
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 7)
        self.assertEqual(result.total_tokens, 18)

    @override_settings(LIVIA_AI_ENABLED=True, LIVIA_AI_DRY_RUN=False, LIVIA_OPENAI_API_KEY="secret")
    def test_timeout_returns_failure_result(self):
        client = OpenAIChatClient()

        with patch("integrations.openai.client.requests.post", side_effect=smart360_client.requests.Timeout("timeout")):
            result = client.create_chat_completion(messages=[{"role": "user", "content": "oi"}])

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "Timeout")


from django.contrib import admin
from django.test import TestCase

from conversations.models import Conversation, HandoffRequest
from integrations.models import TenantWebhookConfig, WebhookDeliveryLog
from integrations.webhooks import service as webhook_service_module
from integrations.webhooks.service import WebhookDispatchService
from leads.models import LeadDraft
from tenants.models import Tenant


class TenantWebhookConfigTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil", domain="https://example.com")

    def test_webhook_config_can_be_created_for_tenant(self):
        config = TenantWebhookConfig.objects.create(
            tenant=self.tenant,
            name="N8N Handoff",
            event_type=TenantWebhookConfig.EventType.HANDOFF_CREATED,
            target_url="https://n8n.example/webhook/livia",
            secret_token="secret-token",
        )

        self.assertEqual(config.tenant, self.tenant)
        self.assertTrue(config.is_active)
        self.assertTrue(config.dry_run)

    def test_webhook_models_are_registered_in_admin(self):
        self.assertIn(TenantWebhookConfig, admin.site._registry)
        self.assertIn(WebhookDeliveryLog, admin.site._registry)
        self.assertNotIn("secret_token", admin.site._registry[TenantWebhookConfig].list_display)


class WebhookDispatchServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Smart Control Brasil", slug="smart-control-brasil", domain="https://example.com")
        self.conversation = Conversation.objects.create(
            tenant=self.tenant,
            session_id="webhook-session",
            source_page="https://example.com/origem",
        )
        self.handoff = HandoffRequest.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            reason=HandoffRequest.Reason.EXPLICIT_REQUEST,
            priority=HandoffRequest.Priority.HIGH,
            visitor_name="Maria",
            visitor_company="ACME",
            visitor_phone="11999999999",
            visitor_email="maria@example.com",
            summary="Resumo seguro do handoff",
            source_page="https://example.com/origem",
        )
        self.lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            name="Maria",
            company="ACME",
            phone="11999999999",
            email="maria@example.com",
            need_summary="Preciso de automação industrial para uma linha de produção",
            status=LeadDraft.Status.QUALIFIED,
        )
        self.service = WebhookDispatchService()

    def _create_config(self, **overrides):
        data = {
            "tenant": self.tenant,
            "name": "N8N",
            "event_type": TenantWebhookConfig.EventType.ALL,
            "target_url": "https://n8n.example/webhook/livia",
            "secret_token": "super-secret-token",
            "is_active": True,
            "dry_run": True,
        }
        data.update(overrides)
        return TenantWebhookConfig.objects.create(**data)

    def test_inactive_config_does_not_send_or_log(self):
        self._create_config(is_active=False)

        with patch("integrations.webhooks.service.requests.post") as post_mock:
            logs = self.service.dispatch_handoff_created(self.handoff)

        post_mock.assert_not_called()
        self.assertEqual(logs, [])
        self.assertEqual(WebhookDeliveryLog.objects.count(), 0)

    @override_settings(LIVIA_WEBHOOKS_ENABLED=True, LIVIA_WEBHOOKS_DRY_RUN=True)
    def test_global_dry_run_does_not_post_real_request(self):
        self._create_config(dry_run=False)

        with patch("integrations.webhooks.service.requests.post") as post_mock:
            logs = self.service.dispatch_handoff_created(self.handoff)

        post_mock.assert_not_called()
        self.assertEqual(logs[0].status, WebhookDeliveryLog.Status.DRY_RUN)

    @override_settings(LIVIA_WEBHOOKS_ENABLED=True, LIVIA_WEBHOOKS_DRY_RUN=False)
    def test_config_dry_run_does_not_post_real_request(self):
        self._create_config(dry_run=True)

        with patch("integrations.webhooks.service.requests.post") as post_mock:
            logs = self.service.dispatch_handoff_created(self.handoff)

        post_mock.assert_not_called()
        self.assertEqual(logs[0].status, WebhookDeliveryLog.Status.DRY_RUN)

    def test_handoff_payload_contains_expected_fields(self):
        payload = self.service.build_handoff_payload(self.handoff)

        self.assertEqual(payload["tenant_slug"], self.tenant.slug)
        self.assertEqual(payload["event_type"], TenantWebhookConfig.EventType.HANDOFF_CREATED)
        self.assertEqual(payload["handoff_id"], self.handoff.id)
        self.assertEqual(payload["visitor_phone"], "11999999999")
        self.assertEqual(payload["source_page"], "https://example.com/origem")
        self.assertIn("created_at", payload)

    def test_lead_payload_contains_expected_fields(self):
        payload = self.service.build_lead_payload(self.lead)

        self.assertEqual(payload["tenant_slug"], self.tenant.slug)
        self.assertEqual(payload["event_type"], TenantWebhookConfig.EventType.LEAD_QUALIFIED)
        self.assertEqual(payload["lead_id"], self.lead.id)
        self.assertEqual(payload["service_area"], "automation")
        self.assertEqual(payload["source_page"], "https://example.com/origem")

    @override_settings(LIVIA_WEBHOOKS_ENABLED=True, LIVIA_WEBHOOKS_DRY_RUN=True)
    def test_secret_token_does_not_appear_in_payload_preview(self):
        self._create_config(secret_token="super-secret-token")

        self.service.dispatch_handoff_created(self.handoff)

        log = WebhookDeliveryLog.objects.get()
        self.assertNotIn("super-secret-token", str(log.payload_preview))
        self.assertNotIn("secret_token", log.payload_preview)

    @override_settings(LIVIA_WEBHOOKS_ENABLED=True, LIVIA_WEBHOOKS_DRY_RUN=True)
    def test_dispatch_handoff_created_creates_delivery_log(self):
        self._create_config(event_type=TenantWebhookConfig.EventType.HANDOFF_CREATED)

        self.service.dispatch_handoff_created(self.handoff)

        log = WebhookDeliveryLog.objects.get(related_handoff=self.handoff)
        self.assertEqual(log.event_type, TenantWebhookConfig.EventType.HANDOFF_CREATED)
        self.assertEqual(log.status, WebhookDeliveryLog.Status.DRY_RUN)

    @override_settings(LIVIA_WEBHOOKS_ENABLED=True, LIVIA_WEBHOOKS_DRY_RUN=True)
    def test_dispatch_lead_qualified_creates_delivery_log(self):
        self._create_config(event_type=TenantWebhookConfig.EventType.LEAD_QUALIFIED)

        self.service.dispatch_lead_qualified(self.lead)

        log = WebhookDeliveryLog.objects.get(related_lead=self.lead)
        self.assertEqual(log.event_type, TenantWebhookConfig.EventType.LEAD_QUALIFIED)
        self.assertEqual(log.status, WebhookDeliveryLog.Status.DRY_RUN)

    @override_settings(
        LIVIA_WEBHOOKS_ENABLED=True,
        LIVIA_WEBHOOKS_DRY_RUN=False,
        LIVIA_WEBHOOKS_REAL_ENABLED=True,
        LIVIA_WEBHOOKS_REAL_ALLOWED_ENVS="development",
        LIVIA_ENVIRONMENT="development",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_http_error_does_not_break_flow(self):
        self._create_config(dry_run=False)
        response = Mock(status_code=500, text="server error")

        with patch("integrations.webhooks.service.requests.post", return_value=response):
            logs = self.service.dispatch_handoff_created(self.handoff)

        self.assertEqual(logs[0].status, WebhookDeliveryLog.Status.FAILED)
        self.assertEqual(logs[0].status_code, 500)

    @override_settings(
        LIVIA_WEBHOOKS_ENABLED=True,
        LIVIA_WEBHOOKS_DRY_RUN=False,
        LIVIA_WEBHOOKS_REAL_ENABLED=True,
        LIVIA_WEBHOOKS_REAL_ALLOWED_ENVS="development",
        LIVIA_ENVIRONMENT="development",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_timeout_does_not_break_flow(self):
        self._create_config(dry_run=False)

        with patch("integrations.webhooks.service.requests.post", side_effect=webhook_service_module.requests.Timeout("timeout")):
            logs = self.service.dispatch_handoff_created(self.handoff)

        self.assertEqual(logs[0].status, WebhookDeliveryLog.Status.FAILED)
        self.assertIn("timeout", logs[0].error_message)


from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.urls import reverse

from audit.models import ACTION_OUTBOX_REQUEUED, AuditEvent
from conversations.models import Conversation, HandoffRequest
from integrations.models import OutboxEvent
from integrations.outbox.handlers import HandlerResult
from integrations.outbox.processor import calculate_backoff_seconds, claim_outbox_events, finalize_outbox_event, process_outbox_batch, recover_abandoned_locks
from integrations.outbox.registry import get_handler
from integrations.outbox.service import enqueue_handoff_created, enqueue_lead_qualified, enqueue_outbox_event
from leads.models import LeadDraft
from tenants.models import Tenant


class OutboxEventModelAndEnqueueTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        self.conversation = Conversation.objects.create(tenant=self.tenant, session_id="outbox-session")

    def test_event_id_unique_and_logical_deduplication(self):
        event_id = uuid.uuid4()
        OutboxEvent.objects.create(event_id=event_id, tenant=self.tenant, event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_type="LeadDraft", aggregate_id="1", deduplication_key="same", available_at=timezone.now())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OutboxEvent.objects.create(event_id=event_id, tenant=self.tenant, event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_type="LeadDraft", aggregate_id="2", deduplication_key="other", available_at=timezone.now())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OutboxEvent.objects.create(event_id=uuid.uuid4(), tenant=self.tenant, event_type=OutboxEvent.EventType.LEAD_QUALIFIED, aggregate_type="LeadDraft", aggregate_id="2", deduplication_key="same", available_at=timezone.now())

    def test_defaults_indexes_and_payload_shape(self):
        event, created = enqueue_outbox_event(tenant=self.tenant, event_type=OutboxEvent.EventType.CONVERSATION_SUMMARY_READY, aggregate_type="Conversation", aggregate_id=self.conversation.pk, data={"conversation_id": self.conversation.pk})

        self.assertTrue(created)
        self.assertEqual(event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(event.attempts, 0)
        self.assertEqual(event.payload["event_id"], str(event.event_id))
        self.assertIn("schema_version", event.payload)
        self.assertNotIn("secret", str(event.payload).lower())
        index_fields = {tuple(index.fields) for index in OutboxEvent._meta.indexes}
        self.assertIn(("status", "available_at"), index_fields)
        self.assertIn(("tenant", "status"), index_fields)
        self.assertIn(("aggregate_type", "aggregate_id"), index_fields)

    def test_lead_qualified_enqueue_is_deduplicated_and_rollback_safe(self):
        lead = LeadDraft.objects.create(tenant=self.tenant, conversation=self.conversation, status=LeadDraft.Status.QUALIFIED, need_summary="Automação")

        event, created = enqueue_lead_qualified(lead)
        second, second_created = enqueue_lead_qualified(lead)

        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(event.pk, second.pk)
        self.assertEqual(OutboxEvent.objects.count(), 1)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                other = LeadDraft.objects.create(tenant=self.tenant, conversation=None, status=LeadDraft.Status.QUALIFIED, need_summary="Teste")
                enqueue_lead_qualified(other)
                raise RuntimeError("rollback")
        self.assertEqual(OutboxEvent.objects.count(), 1)

    def test_handoff_enqueue_and_cross_tenant_guard(self):
        handoff = HandoffRequest.objects.create(tenant=self.tenant, conversation=self.conversation, reason=HandoffRequest.Reason.EXPLICIT_REQUEST)
        event, created = enqueue_handoff_created(handoff)
        self.assertTrue(created)
        self.assertEqual(event.event_type, OutboxEvent.EventType.HANDOFF_CREATED)

        other = Tenant.objects.create(name="Other", slug="other")
        handoff.tenant = other
        with self.assertRaises(ValueError):
            enqueue_handoff_created(handoff)


class OutboxProcessorTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        self.event, _ = enqueue_outbox_event(tenant=self.tenant, event_type=OutboxEvent.EventType.CONVERSATION_SUMMARY_READY, aggregate_type="Conversation", aggregate_id="1", data={"conversation_id": 1}, deduplication_key="event")

    def test_claim_marks_processing_and_second_claim_does_not_duplicate(self):
        first = claim_outbox_events(batch_size=1, worker_id="worker-a")
        second = claim_outbox_events(batch_size=1, worker_id="worker-b")

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.PROCESSING)
        self.assertEqual(self.event.locked_by, "worker-a")

    def test_finalize_success_skip_retry_dead_letter_and_wrong_worker_guard(self):
        claimed = claim_outbox_events(batch_size=1, worker_id="worker-a")[0]
        status = finalize_outbox_event(claimed, worker_id="worker-b", result=HandlerResult("succeeded"))
        self.assertEqual(status, OutboxEvent.Status.PROCESSING)
        status = finalize_outbox_event(claimed, worker_id="worker-a", result=HandlerResult("succeeded", code="ok"))
        self.assertEqual(status, OutboxEvent.Status.SUCCEEDED)
        claimed.refresh_from_db()
        self.assertEqual(claimed.attempts, 1)
        self.assertEqual(claimed.event_id, self.event.event_id)

    @override_settings(LIVIA_OUTBOX_BASE_RETRY_SECONDS=10, LIVIA_OUTBOX_MAX_RETRY_SECONDS=25)
    def test_backoff_and_max_attempts_dead_letter(self):
        self.assertEqual(calculate_backoff_seconds(1), 10)
        self.assertEqual(calculate_backoff_seconds(2), 20)
        self.assertEqual(calculate_backoff_seconds(3), 25)
        self.event.max_attempts = 1
        self.event.save(update_fields=["max_attempts"])
        claimed = claim_outbox_events(batch_size=1, worker_id="worker-a")[0]
        status = finalize_outbox_event(claimed, worker_id="worker-a", result=HandlerResult("retryable_failure", code="timeout", retryable=True))
        self.assertEqual(status, OutboxEvent.Status.DEAD_LETTER)

    @override_settings(LIVIA_OUTBOX_LOCK_TIMEOUT_SECONDS=1)
    def test_abandoned_lock_recovered_but_recent_lock_is_not(self):
        claimed = claim_outbox_events(batch_size=1, worker_id="worker-a")[0]
        self.assertEqual(recover_abandoned_locks(), 0)
        OutboxEvent.objects.filter(pk=claimed.pk).update(locked_at=timezone.now() - timedelta(seconds=5))
        self.assertEqual(recover_abandoned_locks(), 1)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, OutboxEvent.Status.RETRY)

    def test_process_outbox_dry_run_webhook_disabled_is_skipped(self):
        summary = process_outbox_batch(batch_size=1, worker_id="worker-a")
        self.assertEqual(summary.claimed, 1)
        self.assertEqual(summary.skipped, 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.SKIPPED)

    def test_registry_rejects_unknown_event_type(self):
        with self.assertRaises(KeyError):
            get_handler("unknown.event")


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL-specific skip_locked behavior.")
class OutboxProcessorPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")

    def test_skip_locked_allows_second_worker_to_claim_different_event(self):
        first = OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=self.tenant,
            event_type=OutboxEvent.EventType.CONVERSATION_SUMMARY_READY,
            aggregate_type="Conversation",
            aggregate_id="1",
            deduplication_key="event-1",
            payload={"event_id": str(uuid.uuid4()), "schema_version": 1},
            available_at=timezone.now(),
        )
        second = OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=self.tenant,
            event_type=OutboxEvent.EventType.CONVERSATION_SUMMARY_READY,
            aggregate_type="Conversation",
            aggregate_id="2",
            deduplication_key="event-2",
            payload={"event_id": str(uuid.uuid4()), "schema_version": 1},
            available_at=timezone.now(),
        )
        lock_acquired = threading.Event()
        release_lock = threading.Event()
        locked_pk = {"value": None}

        def hold_row_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    queryset = OutboxEvent.objects.filter(
                        status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.RETRY],
                    ).order_by("available_at", "created_at")
                    if connection.features.has_select_for_update:
                        queryset = queryset.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
                    locked = queryset.first()
                    locked_pk["value"] = getattr(locked, "pk", None)
                    lock_acquired.set()
                    release_lock.wait(timeout=5)
            finally:
                connection.close()

        locker = threading.Thread(target=hold_row_lock, daemon=True)
        locker.start()
        self.assertTrue(lock_acquired.wait(timeout=5))
        claimed = claim_outbox_events(batch_size=2, worker_id="worker-b")
        release_lock.set()
        locker.join(timeout=5)

        claimed_ids = {event.pk for event in claimed}
        self.assertEqual(len(claimed_ids), 1)
        self.assertNotIn(locked_pk["value"], claimed_ids)
        self.assertIn(second.pk if locked_pk["value"] == first.pk else first.pk, claimed_ids)
        claimed_event = OutboxEvent.objects.get(pk=next(iter(claimed_ids)))
        self.assertEqual(claimed_event.status, OutboxEvent.Status.PROCESSING)
        self.assertEqual(claimed_event.locked_by, "worker-b")


class OutboxCommandAndAdminTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        self.event, _ = enqueue_outbox_event(tenant=self.tenant, event_type=OutboxEvent.EventType.CONVERSATION_SUMMARY_READY, aggregate_type="Conversation", aggregate_id="1", data={"conversation_id": 1}, deduplication_key="admin-event")
        self.event.status = OutboxEvent.Status.DEAD_LETTER
        self.event.save(update_fields=["status"])
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser("root", "root@example.com", "pass")
        self.staff = user_model.objects.create_user("staff", "staff@example.com", "pass", is_staff=True)

    def test_process_outbox_without_execute_does_not_call_handler(self):
        output = StringIO()
        call_command("process_outbox", stdout=output)
        data = __import__("json").loads(output.getvalue())
        self.assertTrue(data["dry_run"])
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.DEAD_LETTER)

    def test_process_outbox_filters_and_outbox_report_are_readonly(self):
        self.event.status = OutboxEvent.Status.PENDING
        self.event.save(update_fields=["status"])
        output = StringIO()
        call_command("process_outbox", "--execute", "--once", "--batch-size", "1", "--event-type", OutboxEvent.EventType.CONVERSATION_SUMMARY_READY, "--tenant", self.tenant.slug, stdout=output)
        self.assertIn('"claimed": 1', output.getvalue())
        report = StringIO()
        call_command("outbox_report", stdout=report)
        self.assertIn("outbox_events", report.getvalue())

    def test_admin_superuser_only_and_requeue_audited(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin:integrations_outboxevent_changelist")).status_code, 403)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:integrations_outboxevent_changelist")).status_code, 200)
        response = self.client.post(reverse("admin:integrations_outboxevent_changelist"), {"action": "requeue_outbox_events", "_selected_action": [self.event.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(AuditEvent.objects.filter(action=ACTION_OUTBOX_REQUEUED).count(), 1)

    def test_admin_blocks_manual_create_edit_delete(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:integrations_outboxevent_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:integrations_outboxevent_change", args=[self.event.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:integrations_outboxevent_delete", args=[self.event.pk])).status_code, 403)


class OutboxExternalIdempotencyHeadersTests(TestCase):
    def test_smart360_receives_event_id_headers(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"external_id": "crm-1", "message": "ok"}
        client = Smart360GrowthClient(base_url="https://smart.example", token="token", dry_run=False)

        with patch("integrations.smart360.client.requests.post", return_value=response) as post_mock:
            result = client.ingest_lead({"tenant_slug": "tenant", "conversation_id": "c1"}, idempotency_key="event-123")

        self.assertTrue(result.success)
        headers = post_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Livia-Event-ID"], "event-123")
        self.assertEqual(headers["Idempotency-Key"], "event-123")

    @override_settings(
        LIVIA_WEBHOOKS_ENABLED=True,
        LIVIA_WEBHOOKS_DRY_RUN=False,
        LIVIA_WEBHOOKS_REAL_ENABLED=True,
        LIVIA_WEBHOOKS_REAL_ALLOWED_ENVS="development",
        LIVIA_ENVIRONMENT="development",
        LIVIA_ALLOW_REAL_SIDE_EFFECTS_IN_TESTS=True,
    )
    def test_webhook_receives_event_id_header_and_payload(self):
        tenant = Tenant.objects.create(name="Tenant", slug="tenant")
        TenantWebhookConfig.objects.create(tenant=tenant, name="Hook", target_url="https://hook.example/livia", dry_run=False)
        response = Mock(status_code=200)

        with patch("integrations.webhooks.service.requests.post", return_value=response) as post_mock:
            WebhookDispatchService().dispatch_event(tenant, TenantWebhookConfig.EventType.CONVERSATION_SUMMARY, {"data": {"ok": True}}, event_id="event-456")

        headers = post_mock.call_args.kwargs["headers"]
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(headers["X-Livia-Event-ID"], "event-456")
        self.assertEqual(headers["Idempotency-Key"], "event-456")
        self.assertEqual(payload["event_id"], "event-456")
        self.assertNotIn("Authorization", payload)
