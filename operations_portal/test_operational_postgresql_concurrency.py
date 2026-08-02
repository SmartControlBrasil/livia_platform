from __future__ import annotations

import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from knowledge_base.models import (
    OperationalMonitoringBatchRun,
    TenantOperationalAlert,
    TenantOperationalNotification,
    TenantRagConfiguration,
    TenantRagOperationRequest,
)
from knowledge_base.rag.alert_governance_services import GovernanceError, assign_operational_alert
from knowledge_base.rag.operational_alert_sync import acknowledge_operational_alert, sync_operational_alerts
from knowledge_base.rag.operational_monitoring import process_operational_monitoring
from knowledge_base.rag.operational_notification_events import (
    EVENT_ALERT_CRITICAL_CREATED,
    OperationalNotificationEvent,
)
from knowledge_base.rag.operational_notification_processor import process_operational_notifications_batch
from knowledge_base.rag.operational_notification_services import enqueue_operational_notifications_for_event
from knowledge_base.rag.operational_work_queue_services import WorkQueueError, claim_operational_alert
from knowledge_base.rag.operations import RagOperationsError, create_operation_request
from tenants.models import Tenant, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL-specific concurrency semantics.")
@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
    LIVIA_OPERATIONAL_MONITORING_ENABLED=True,
    LIVIA_OPERATIONAL_MONITORING_DRY_RUN=False,
    LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED=False,
    LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN=True,
)
class OperationalPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="pgc-admin", password="pass")
        self.operator = user_model.objects.create_user(username="pgc-operator", password="pass")
        self.tenant = Tenant.objects.create(name="PG Concurrency", slug="pg-concurrency")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="folder-pgc",
            sync_enabled=True,
            retrieval_enabled=True,
            operational_monitoring_enabled=True,
        )
        self.admin_membership = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.operator_membership = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.operator,
            role=TenantMembership.Role.OPERATOR,
        )

    def _create_stale_operation(self):
        return TenantRagOperationRequest.objects.create(
            tenant=self.tenant,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id=f"pgc-stale-{uuid.uuid4().hex[:8]}",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def _sync_alert(self) -> TenantOperationalAlert:
        self._create_stale_operation()
        sync_operational_alerts(tenant=self.tenant, actor=self.admin)
        return TenantOperationalAlert.objects.get(tenant=self.tenant)

    def test_concurrent_alert_sync_does_not_duplicate(self):
        self._create_stale_operation()

        def _run_sync():
            close_old_connections()
            try:
                sync_operational_alerts(tenant=self.tenant, actor=self.admin)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_run_sync), pool.submit(_run_sync)]
            for future in as_completed(futures):
                future.result()

        self.assertEqual(
            TenantOperationalAlert.objects.filter(
                tenant=self.tenant,
                rule_id="rag_operation_stale",
            ).count(),
            1,
        )

    def test_concurrent_claim_single_owner(self):
        alert = self._sync_alert()

        def claim(actor):
            close_old_connections()
            try:
                claim_operational_alert(tenant=self.tenant, alert_id=alert.pk, actor=actor)
            finally:
                connection.close()

        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, self.admin), pool.submit(claim, self.operator)]
            for future in as_completed(futures):
                try:
                    future.result()
                except WorkQueueError as exc:
                    errors.append(str(exc))

        alert.refresh_from_db()
        self.assertIsNotNone(alert.assigned_to_id)
        self.assertEqual(len(errors), 1)

    def test_concurrent_ack_and_assign(self):
        alert = self._sync_alert()

        def ack():
            close_old_connections()
            try:
                acknowledge_operational_alert(tenant=self.tenant, alert_id=alert.pk, actor=self.admin)
            finally:
                connection.close()

        def assign():
            close_old_connections()
            try:
                assign_operational_alert(
                    tenant=self.tenant,
                    alert_id=alert.pk,
                    actor=self.admin,
                    membership_id=self.admin_membership.pk,
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ack), pool.submit(assign)]
            for future in as_completed(futures):
                try:
                    future.result()
                except GovernanceError:
                    pass

        alert.refresh_from_db()
        self.assertIn(
            alert.status,
            {TenantOperationalAlert.Status.ACKNOWLEDGED, TenantOperationalAlert.Status.OPEN},
        )
        self.assertIsNotNone(alert.assigned_to_id)

    def test_notification_deduplication_concurrent(self):
        alert = self._sync_alert()
        event = OperationalNotificationEvent(
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            tenant_id=self.tenant.pk,
            alert_id=alert.pk,
            reopen_count=alert.reopen_count,
            target_membership_id=self.admin_membership.pk,
        )
        barrier = threading.Barrier(2)

        def enqueue_once():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    enqueue_operational_notifications_for_event(
                        event=event,
                        tenant=self.tenant,
                        alert=alert,
                        actor=self.admin,
                    )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(enqueue_once), pool.submit(enqueue_once)]
            for future in as_completed(futures):
                future.result()

        self.assertEqual(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant,
                event_type=EVENT_ALERT_CRITICAL_CREATED,
                recipient_membership=self.admin_membership,
            ).count(),
            1,
        )

    def _create_open_alert(self) -> TenantOperationalAlert:
        now = timezone.now()
        return TenantOperationalAlert.objects.create(
            tenant=self.tenant,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_operation_stale",
            fingerprint=f"pgc:{uuid.uuid4().hex}",
            title="Alerta concorrência",
            summary="Alerta mínimo para testes PostgreSQL de concorrência.",
            detected_at=now,
            last_seen_at=now,
        )

    def test_parallel_notification_workers_single_delivery(self):
        alert = self._create_open_alert()
        notification = TenantOperationalNotification.objects.create(
            tenant=self.tenant,
            recipient_membership=self.admin_membership,
            channel=TenantOperationalNotification.Channel.IN_APP,
            category=TenantOperationalNotification.Category.ALERT,
            severity=TenantOperationalNotification.Severity.WARNING,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            title="Worker concurrency",
            summary="Notificação única para validação de worker paralelo.",
            status=TenantOperationalNotification.Status.PENDING,
            source_type=TenantOperationalNotification.SourceType.OPERATIONAL_ALERT,
            source_reference=str(alert.pk),
            deduplication_key=f"pgc-worker:{alert.pk}:{self.admin_membership.pk}",
            scheduled_at=timezone.now(),
        )

        def run_worker(worker_id: str):
            close_old_connections()
            try:
                return process_operational_notifications_batch(limit=5, worker_id=worker_id)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            summaries = list(pool.map(run_worker, ["worker-a", "worker-b"]))

        notification.refresh_from_db()
        total_claimed = sum(summary.claimed for summary in summaries)
        self.assertLessEqual(total_claimed, 1)
        self.assertIn(
            notification.status,
            {
                TenantOperationalNotification.Status.DELIVERED,
                TenantOperationalNotification.Status.SENT,
                TenantOperationalNotification.Status.PROCESSING,
            },
        )
        self.assertLessEqual(notification.attempt_count, 1)

    def test_monitoring_advisory_lock_blocks_second_batch(self):
        self._create_stale_operation()
        started = threading.Event()
        release = threading.Event()
        results: dict[str, str] = {}

        def hold_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_try_advisory_lock(%s)", [915_120_001])
                        started.set()
                        release.wait(timeout=10)
                        cursor.execute("SELECT pg_advisory_unlock(%s)", [915_120_001])
            finally:
                connection.close()

        locker = threading.Thread(target=hold_lock, daemon=True)
        locker.start()
        self.assertTrue(started.wait(timeout=5))

        with patch(
            "knowledge_base.rag.operational_monitoring.ADVISORY_LOCK_ID",
            915_120_001,
        ):
            blocked = process_operational_monitoring(tenant_slug=self.tenant.slug, trigger="cli", dry_run=False)
            release.set()
            locker.join(timeout=5)
            allowed = process_operational_monitoring(tenant_slug=self.tenant.slug, trigger="cli", dry_run=False)

        results["blocked"] = blocked.status
        results["allowed"] = allowed.status
        self.assertEqual(results["blocked"], OperationalMonitoringBatchRun.Status.SKIPPED)
        self.assertEqual(results["allowed"], OperationalMonitoringBatchRun.Status.SUCCEEDED)

    def test_rag_operation_active_constraint_under_concurrency(self):
        errors: list[str] = []

        def create_inventory():
            close_old_connections()
            try:
                create_operation_request(
                    tenant=self.tenant,
                    operation=TenantRagOperationRequest.Operation.INVENTORY,
                    requested_by=self.admin,
                )
            except RagOperationsError as exc:
                errors.append(exc.code)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create_inventory), pool.submit(create_inventory)]
            for future in as_completed(futures):
                future.result()

        active = TenantRagOperationRequest.objects.filter(
            tenant=self.tenant,
            status__in=[
                TenantRagOperationRequest.Status.PENDING,
                TenantRagOperationRequest.Status.RUNNING,
            ],
        )
        self.assertEqual(active.count(), 1)
        self.assertIn("duplicate_operation", errors)
