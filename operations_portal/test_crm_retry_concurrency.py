from __future__ import annotations

import threading
import time
import unittest
import uuid
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings

from conversations.models import Conversation
from integrations.models import OutboxEvent
from leads.models import LeadDraft
from operations_portal.crm_retry import execute_portal_crm_retry
from tenants.models import Tenant


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL-specific row lock semantics.")
@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class CrmRetryPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="CRM Tenant", slug="crm-retry-tenant")
        self.other = Tenant.objects.create(name="Other", slug="crm-retry-other")
        self.conversation = Conversation.objects.create(tenant=self.tenant, session_id="crm-retry-session")
        self.lead = LeadDraft.objects.create(
            tenant=self.tenant,
            conversation=self.conversation,
            status=LeadDraft.Status.FAILED,
            crm_error="timeout",
            name="Lead Retry",
        )

    def test_lock_sql_has_no_nullable_outer_join(self):
        with transaction.atomic():
            qs = (
                LeadDraft.objects.select_for_update()
                .select_related("tenant")
                .filter(pk=self.lead.pk, tenant_id=self.tenant.pk)
            )
            sql = str(qs.query).lower()
        self.assertIn("for update", sql)
        self.assertIn("tenant_id", sql)
        self.assertNotIn("left outer join", sql)
        self.assertNotIn("conversations_conversation", sql)

    def test_concurrent_retries_create_single_outbox_event(self):
        barrier = threading.Barrier(3)
        outcomes: list[str] = []
        errors: list[str] = []

        def worker():
            connection.close()
            try:
                barrier.wait(timeout=5)
                outcome = execute_portal_crm_retry(lead=self.lead)
                outcomes.append(outcome.code)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            finally:
                connection.close()

        first = threading.Thread(target=worker, daemon=True)
        second = threading.Thread(target=worker, daemon=True)
        first.start()
        second.start()
        barrier.wait(timeout=5)
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes.count("enqueued"), 1)
        self.assertEqual(outcomes.count("blocked_active"), 1)
        self.assertEqual(
            OutboxEvent.objects.filter(
                tenant=self.tenant,
                event_type=OutboxEvent.EventType.LEAD_QUALIFIED,
                aggregate_id=str(self.lead.pk),
            ).count(),
            1,
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadDraft.Status.QUALIFIED)
        self.assertEqual(self.lead.crm_error, "")

    def test_retry_keeps_tenant_scope_on_lock(self):
        foreign_lead = LeadDraft.objects.create(
            tenant=self.other,
            status=LeadDraft.Status.FAILED,
            crm_error="x",
        )
        # lead object com pk de outro tenant não pode ser processado no escopo do tenant A
        forged = LeadDraft(pk=foreign_lead.pk, tenant_id=self.tenant.pk)
        with self.assertRaises(LeadDraft.DoesNotExist):
            execute_portal_crm_retry(lead=forged)

    def test_rollback_releases_lock_and_preserves_state(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                locked = (
                    LeadDraft.objects.select_for_update()
                    .select_related("tenant")
                    .get(pk=self.lead.pk, tenant_id=self.tenant.pk)
                )
                locked.status = LeadDraft.Status.QUALIFIED
                locked.save(update_fields=["status", "updated_at"])
                raise RuntimeError("forced_rollback")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadDraft.Status.FAILED)
        self.assertEqual(self.lead.crm_error, "timeout")

        outcome = execute_portal_crm_retry(lead=self.lead)
        self.assertEqual(outcome.code, "enqueued")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadDraft.Status.QUALIFIED)

    def test_lock_blocks_second_transaction_until_release(self):
        lock_acquired = threading.Event()
        release_lock = threading.Event()
        probe_done = threading.Event()
        elapsed = {"seconds": 0.0}

        def locker():
            connection.close()
            try:
                with transaction.atomic():
                    LeadDraft.objects.select_for_update().get(pk=self.lead.pk, tenant_id=self.tenant.pk)
                    lock_acquired.set()
                    release_lock.wait(timeout=5)
            finally:
                connection.close()

        def probe():
            connection.close()
            try:
                lock_acquired.wait(timeout=5)
                started = time.monotonic()
                with transaction.atomic():
                    LeadDraft.objects.select_for_update().get(pk=self.lead.pk, tenant_id=self.tenant.pk)
                elapsed["seconds"] = time.monotonic() - started
            finally:
                probe_done.set()
                connection.close()

        t1 = threading.Thread(target=locker, daemon=True)
        t2 = threading.Thread(target=probe, daemon=True)
        t1.start()
        t2.start()
        self.assertTrue(lock_acquired.wait(timeout=5))
        time.sleep(0.35)
        release_lock.set()
        self.assertTrue(probe_done.wait(timeout=5))
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertGreaterEqual(elapsed["seconds"], 0.30)

    def test_external_http_not_called_inside_retry_transaction(self):
        with patch("integrations.smart360.client.requests.post") as mocked_post:
            outcome = execute_portal_crm_retry(lead=self.lead)
        self.assertEqual(outcome.code, "enqueued")
        mocked_post.assert_not_called()
