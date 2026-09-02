from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from assistant_core.prompts.livia import DEFAULT_REPLY
from conversations.models import ChatRequest, Conversation, HandoffRequest, Message
from integrations.models import OutboxEvent
from knowledge_base.models import RagRetrievalEvent
from leads.models import LeadDraft
from operations_portal.quality_metrics import (
    build_fallback_metrics,
    build_quality_dashboard,
    build_rag_metrics,
    resolve_quality_period,
)
from tenants.models import Tenant, TenantMembership


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class QualityPortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="quality-user", password="pass")
        self.scb = Tenant.objects.create(name="Smart Control", slug="smart-control-brasil")
        self.pitondo = Tenant.objects.create(name="Pitondo", slug="granimarmores-pitondo")
        TenantMembership.objects.create(tenant=self.scb, user=self.user, role=TenantMembership.Role.VIEWER)
        self.client = Client()
        self.client.force_login(self.user)

        self.conv_scb = Conversation.objects.create(tenant=self.scb, session_id="scb-session", source_page="https://scb.example/")
        self.conv_pit = Conversation.objects.create(tenant=self.pitondo, session_id="pit-session", source_page="https://pit.example/")
        Message.objects.create(conversation=self.conv_scb, role=Message.Role.USER, content="Quanto custa um site?")
        Message.objects.create(conversation=self.conv_scb, role=Message.Role.ASSISTANT, content=DEFAULT_REPLY)
        Message.objects.create(conversation=self.conv_scb, role=Message.Role.ASSISTANT, content="Prazo depende do escopo.")
        Message.objects.create(conversation=self.conv_pit, role=Message.Role.USER, content="Vocês fazem bancada?")
        Message.objects.create(conversation=self.conv_pit, role=Message.Role.ASSISTANT, content="Sim, trabalhamos com granito.")

        LeadDraft.objects.create(tenant=self.scb, conversation=self.conv_scb, status=LeadDraft.Status.QUALIFIED)
        LeadDraft.objects.create(tenant=self.pitondo, conversation=self.conv_pit, status=LeadDraft.Status.DRAFT)
        HandoffRequest.objects.create(tenant=self.scb, conversation=self.conv_scb, status=HandoffRequest.Status.PENDING)
        HandoffRequest.objects.create(tenant=self.pitondo, conversation=self.conv_pit, status=HandoffRequest.Status.SENT)

        ChatRequest.objects.create(
            tenant=self.scb,
            conversation=self.conv_scb,
            session_id="scb-session",
            request_id=uuid.uuid4(),
            status=ChatRequest.Status.COMPLETED,
            request_fingerprint="fp-scb",
            response_payload={"reply": DEFAULT_REPLY, "intent": "quote_request", "observability": {"is_fallback": True}},
            response_status_code=200,
            completed_at=timezone.now(),
        )
        ChatRequest.objects.create(
            tenant=self.pitondo,
            conversation=self.conv_pit,
            session_id="pit-session",
            request_id=uuid.uuid4(),
            status=ChatRequest.Status.COMPLETED,
            request_fingerprint="fp-pit",
            response_payload={"reply": "ok", "intent": "general"},
            response_status_code=200,
            completed_at=timezone.now(),
        )
        RagRetrievalEvent.objects.create(
            tenant=self.scb,
            conversation_id=self.conv_scb.pk,
            status=RagRetrievalEvent.Status.EMPTY,
            hit=False,
            max_score=0.1,
            result_count=0,
            duration_ms=12,
        )
        RagRetrievalEvent.objects.create(
            tenant=self.scb,
            conversation_id=self.conv_scb.pk,
            status=RagRetrievalEvent.Status.COMPLETED,
            hit=True,
            max_score=0.82,
            result_count=2,
            duration_ms=40,
        )
        RagRetrievalEvent.objects.create(
            tenant=self.pitondo,
            conversation_id=self.conv_pit.pk,
            status=RagRetrievalEvent.Status.COMPLETED,
            hit=True,
            max_score=0.7,
            result_count=1,
            duration_ms=20,
        )
        OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=self.scb,
            event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
            aggregate_type="lead",
            aggregate_id=str(self.conv_scb.pk),
            deduplication_key=f"lead-scb-{uuid.uuid4()}",
            status=OutboxEvent.Status.SUCCEEDED,
            available_at=timezone.now(),
        )
        OutboxEvent.objects.create(
            event_id=uuid.uuid4(),
            tenant=self.pitondo,
            event_type=OutboxEvent.EventType.HANDOFF_CREATED,
            aggregate_type="handoff",
            aggregate_id=str(self.conv_pit.pk),
            deduplication_key=f"handoff-pit-{uuid.uuid4()}",
            status=OutboxEvent.Status.DEAD_LETTER,
            available_at=timezone.now(),
            last_error_code="smtp_error",
        )

    def test_anonymous_blocked(self):
        anon = Client()
        response = anon.get(reverse("operations_portal:quality_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_dashboard_renders_for_member(self):
        response = self.client.get(reverse("operations_portal:quality_dashboard"), {"period": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Qualidade da Lívia")
        self.assertContains(response, "Fallback rate")

    def test_tenant_isolation_metrics(self):
        period = resolve_quality_period(period="7d")
        scb = build_quality_dashboard(tenant=self.scb, period=period)
        pit = build_quality_dashboard(tenant=self.pitondo, period=period)
        self.assertEqual(scb["counts"]["conversations"], 1)
        self.assertEqual(pit["counts"]["conversations"], 1)
        self.assertEqual(scb["leads"]["total"], 1)
        self.assertEqual(pit["leads"]["total"], 1)
        self.assertEqual(scb["outbox"]["dead_letter"], 0)
        self.assertEqual(pit["outbox"]["dead_letter"], 1)

        fallback = build_fallback_metrics(tenant=self.scb, period=period)
        self.assertGreaterEqual(fallback["fallback_count"], 1)
        pit_fallback = build_fallback_metrics(tenant=self.pitondo, period=period)
        self.assertEqual(pit_fallback["fallback_count"], 0)

        rag_scb = build_rag_metrics(tenant=self.scb, period=period)
        rag_pit = build_rag_metrics(tenant=self.pitondo, period=period)
        self.assertEqual(rag_scb["attempted"], 2)
        self.assertEqual(rag_pit["attempted"], 1)
        self.assertEqual(rag_scb["empty"], 1)
        self.assertEqual(rag_pit["empty"], 0)
        self.assertEqual(rag_scb["hits"], 1)
        self.assertEqual(rag_pit["hits"], 1)

    def test_quality_pages_do_not_leak_other_tenant(self):
        response = self.client.get(reverse("operations_portal:quality_conversations"), {"period": "30d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "scb-session")
        self.assertNotContains(response, "pit-session")

        response = self.client.get(reverse("operations_portal:quality_conversation_transcript", args=[self.conv_pit.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse("operations_portal:quality_tenant_detail", args=[self.pitondo.slug]))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("operations_portal:quality_tenant_detail", args=[self.scb.slug]), {"period": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "smart-control-brasil")
        self.assertNotContains(response, "pit-session")

    def test_knowledge_gaps_and_outbox_pages(self):
        response = self.client.get(reverse("operations_portal:quality_knowledge_gaps"), {"period": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lacunas de conhecimento")

        response = self.client.get(reverse("operations_portal:quality_outbox"), {"period": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outbox operacional")
        self.assertNotContains(response, "granimarmores-pitondo")

    def test_transcript_hides_system_by_default(self):
        Message.objects.create(conversation=self.conv_scb, role=Message.Role.SYSTEM, content="internal-debug-marker")
        response = self.client.get(reverse("operations_portal:quality_conversation_transcript", args=[self.conv_scb.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cliente")
        self.assertContains(response, "Lívia")
        self.assertNotContains(response, "internal-debug-marker")

    def test_query_budget_dashboard_reasonable(self):
        period = resolve_quality_period(period="7d")
        with CaptureQueriesContext(connection) as ctx:
            build_quality_dashboard(tenant=self.scb, period=period)
        self.assertLess(len(ctx), 120, f"Dashboard usou {len(ctx)} queries")
