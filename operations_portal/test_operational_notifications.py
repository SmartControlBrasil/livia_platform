from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import ACTION_OPERATIONAL_NOTIFICATION_CREATED, ACTION_OPERATIONAL_NOTIFICATION_READ, AuditEvent
from config.environment_safety import inspect_environment_safety
from knowledge_base.models import TenantOperationalAlert, TenantOperationalNotification, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.operational_alert_sync import sync_operational_alerts
from knowledge_base.rag.operational_notification_delivery import DeliveryResult
from knowledge_base.rag.operational_notification_events import EVENT_ALERT_CRITICAL_CREATED
from knowledge_base.rag.operational_notification_hooks import notify_alert_critical_created
from knowledge_base.rag.operational_notification_processor import process_operational_notifications_batch
from knowledge_base.rag.operational_notification_services import (
    NotificationError,
    enqueue_operational_notifications_for_event,
    get_or_create_preference,
    mark_notification_read,
    update_notification_preferences,
)
from knowledge_base.rag.operational_notification_events import OperationalNotificationEvent
from knowledge_base.rag.operational_work_queue_services import claim_operational_alert, transfer_operational_alert
from tenants.models import Tenant, TenantMembership

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STORAGES,
    LIVIA_RAG_ENABLED=True,
    LIVIA_RAG_DRY_RUN=True,
    LIVIA_RAG_EMBEDDING_PROVIDER="fake",
    LIVIA_RAG_OPERATIONS_ENABLED=True,
    LIVIA_RAG_OPERATIONS_DRY_RUN=True,
    LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED=False,
    LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN=True,
)
class OperationalNotificationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="notif-admin", password="pass", email="admin@example.com")
        self.operator = get_user_model().objects.create_user(username="notif-operator", password="pass", email="op@example.com")
        self.other = get_user_model().objects.create_user(username="notif-other", password="pass")
        self.tenant_a = Tenant.objects.create(name="Notif A", slug="notif-a")
        self.tenant_b = Tenant.objects.create(name="Notif B", slug="notif-b")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_b,
            approved_folder_id="folder-b",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.admin_membership = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.operator_membership = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.operator,
            role=TenantMembership.Role.OPERATOR,
        )
        self.other_membership = TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.other,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.client = Client()

    def _login(self, user=None, tenant=None):
        user = user or self.admin
        tenant = tenant or self.tenant_a
        self.client.force_login(user)
        session = self.client.session
        session["operations_portal_active_tenant_id"] = str(tenant.pk)
        session.save()

    def _create_stale_operation(self, tenant=None):
        return TenantRagOperationRequest.objects.create(
            tenant=tenant or self.tenant_a,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="notif-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def _sync_critical_alert(self, tenant=None):
        self._create_stale_operation(tenant)
        with self.captureOnCommitCallbacks(execute=True):
            sync_operational_alerts(tenant=tenant or self.tenant_a, actor=self.admin)
        return TenantOperationalAlert.objects.get(tenant=tenant or self.tenant_a)

    def _process_pending(self):
        return process_operational_notifications_batch(limit=50)

    def test_critical_created_generates_in_app_notification(self):
        alert = self._sync_critical_alert()
        self.assertEqual(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                event_type=EVENT_ALERT_CRITICAL_CREATED,
            ).count(),
            TenantMembership.objects.filter(tenant=self.tenant_a, is_active=True).count(),
        )
        summary = self._process_pending()
        self.assertGreater(summary.delivered, 0)
        self.assertTrue(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                status=TenantOperationalNotification.Status.DELIVERED,
            ).exists()
        )
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_NOTIFICATION_CREATED).exists())
        self.assertNotIn("api_key", TenantOperationalNotification.objects.first().summary.lower())

    def test_deduplication_same_event(self):
        alert = self._sync_critical_alert()
        with self.captureOnCommitCallbacks(execute=True):
            notify_alert_critical_created(alert=alert, actor=self.admin)
        count = TenantOperationalNotification.objects.filter(
            tenant=self.tenant_a,
            recipient_membership=self.admin_membership,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
        ).count()
        self.assertEqual(count, 1)

    def test_reopen_allows_new_notification_cycle(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        with self.captureOnCommitCallbacks(execute=True):
            sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        with self.captureOnCommitCallbacks(execute=True):
            sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.reopen_count, 1)
        self.assertGreater(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                event_type=EVENT_ALERT_CRITICAL_CREATED,
            ).count(),
            1,
        )

    def test_claim_notifies_assignee(self):
        alert = self._sync_critical_alert()
        with self.captureOnCommitCallbacks(execute=True):
            claim_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.operator)
        self.assertTrue(
            TenantOperationalNotification.objects.filter(
                recipient_membership=self.operator_membership,
                event_type="alert_assigned",
            ).exists()
        )

    def test_transfer_notifies_new_owner(self):
        alert = self._sync_critical_alert()
        alert.assigned_to = self.admin_membership
        alert.save(update_fields=["assigned_to", "updated_at"])
        with self.captureOnCommitCallbacks(execute=True):
            transfer_operational_alert(
                tenant=self.tenant_a,
                alert_id=alert.pk,
                actor=self.admin,
                membership_id=self.operator_membership.pk,
                reason="Transferência operacional para operador de plantão do tenant.",
            )
        self.assertTrue(
            TenantOperationalNotification.objects.filter(
                recipient_membership=self.operator_membership,
                event_type="alert_transferred",
            ).exists()
        )

    def test_sla_breach_single_per_cycle(self):
        alert = self._sync_critical_alert()
        alert.ack_due_at = timezone.now() - timedelta(minutes=1)
        alert.save(update_fields=["ack_due_at", "updated_at"])
        from knowledge_base.rag.operational_notification_hooks import evaluate_sla_breach_notifications

        with self.captureOnCommitCallbacks(execute=True):
            evaluate_sla_breach_notifications(tenant=self.tenant_a, actor=self.admin)
            evaluate_sla_breach_notifications(tenant=self.tenant_a, actor=self.admin)
        per_recipient = TenantOperationalNotification.objects.filter(
            tenant=self.tenant_a,
            event_type="sla_ack_breached",
            recipient_membership=self.admin_membership,
        ).count()
        self.assertEqual(per_recipient, 1)

    def test_preferences_disable_email_channel(self):
        pref = get_or_create_preference(tenant=self.tenant_a, membership=self.admin_membership)
        pref.email_enabled = False
        pref.save(update_fields=["email_enabled", "updated_at"])
        alert = self._sync_critical_alert()
        self.assertFalse(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                channel=TenantOperationalNotification.Channel.EMAIL,
            ).exists()
        )

    def test_mandatory_in_app_even_when_disabled(self):
        pref = get_or_create_preference(tenant=self.tenant_a, membership=self.admin_membership)
        pref.in_app_enabled = False
        pref.save(update_fields=["in_app_enabled", "updated_at"])
        alert = self._sync_critical_alert()
        self.assertTrue(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                recipient_membership=self.admin_membership,
                channel=TenantOperationalNotification.Channel.IN_APP,
                event_type=EVENT_ALERT_CRITICAL_CREATED,
            ).exists()
        )

    def test_mark_read_own_notification(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        notification = TenantOperationalNotification.objects.filter(
            recipient_membership=self.admin_membership,
            status=TenantOperationalNotification.Status.DELIVERED,
        ).first()
        mark_notification_read(
            notification=notification,
            membership=self.admin_membership,
            actor=self.admin,
        )
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)
        self.assertEqual(notification.status, TenantOperationalNotification.Status.READ)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_NOTIFICATION_READ).exists())

    def test_mark_read_cross_user_blocked(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        notification = TenantOperationalNotification.objects.filter(
            recipient_membership=self.admin_membership,
        ).first()
        with self.assertRaises(NotificationError):
            mark_notification_read(
                notification=notification,
                membership=self.operator_membership,
                actor=self.operator,
            )

    def test_badge_count(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        self._login()
        response = self.client.get(reverse("operations_portal:operational_notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.context["unread_notification_count"], 0)

    def test_tenant_isolation_list(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        self._login(user=self.other, tenant=self.tenant_b)
        response = self.client.get(reverse("operations_portal:operational_notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"]), 0)

    def test_mark_read_post(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        notification = TenantOperationalNotification.objects.filter(
            recipient_membership=self.admin_membership,
        ).first()
        self._login()
        response = self.client.post(
            reverse("operations_portal:operational_notification_mark_read", kwargs={"pk": notification.pk})
        )
        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_mark_all_read(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        self._login()
        response = self.client.post(reverse("operations_portal:operational_notification_mark_all_read"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TenantOperationalNotification.objects.filter(
                tenant=self.tenant_a,
                recipient_membership=self.admin_membership,
                read_at__isnull=True,
                status=TenantOperationalNotification.Status.DELIVERED,
            ).exists()
        )

    @patch("knowledge_base.rag.operational_notification_processor.deliver_notification")
    def test_retry_on_temporary_error(self, deliver_mock):
        deliver_mock.return_value = DeliveryResult(
            success=False,
            retryable=True,
            error_category="timeout",
            message="Temporary provider timeout.",
        )
        notification = TenantOperationalNotification.objects.create(
            tenant=self.tenant_a,
            recipient_membership=self.admin_membership,
            channel=TenantOperationalNotification.Channel.EMAIL,
            category=TenantOperationalNotification.Category.ALERT,
            severity=TenantOperationalNotification.Severity.CRITICAL,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            title="Retry teste",
            summary="Resumo.",
            status=TenantOperationalNotification.Status.PENDING,
            source_type=TenantOperationalNotification.SourceType.OPERATIONAL_ALERT,
            deduplication_key="test-retry-temporary",
            scheduled_at=timezone.now(),
        )
        summary = process_operational_notifications_batch(limit=10)
        notification.refresh_from_db()
        self.assertEqual(summary.retry_scheduled, 1)
        self.assertEqual(notification.status, TenantOperationalNotification.Status.PENDING)
        self.assertIsNotNone(notification.next_attempt_at)

    @patch("knowledge_base.rag.operational_notification_processor.deliver_notification")
    def test_non_retryable_invalid_recipient(self, deliver_mock):
        deliver_mock.return_value = DeliveryResult(
            success=False,
            retryable=False,
            error_category="invalid_recipient",
            message="Recipient invalid.",
        )
        notification = TenantOperationalNotification.objects.create(
            tenant=self.tenant_a,
            recipient_membership=self.admin_membership,
            channel=TenantOperationalNotification.Channel.EMAIL,
            category=TenantOperationalNotification.Category.ALERT,
            severity=TenantOperationalNotification.Severity.CRITICAL,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            title="Teste",
            summary="Resumo seguro.",
            status=TenantOperationalNotification.Status.PENDING,
            source_type=TenantOperationalNotification.SourceType.OPERATIONAL_ALERT,
            deduplication_key="test-non-retryable-key",
            scheduled_at=timezone.now(),
        )
        process_operational_notifications_batch(limit=5)
        notification.refresh_from_db()
        self.assertEqual(notification.status, TenantOperationalNotification.Status.FAILED)

    def test_email_dry_run_no_external_send(self):
        notification = TenantOperationalNotification.objects.create(
            tenant=self.tenant_a,
            recipient_membership=self.admin_membership,
            channel=TenantOperationalNotification.Channel.EMAIL,
            category=TenantOperationalNotification.Category.ALERT,
            severity=TenantOperationalNotification.Severity.WARNING,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            title="Alerta",
            summary="Resumo.",
            status=TenantOperationalNotification.Status.PENDING,
            source_type=TenantOperationalNotification.SourceType.OPERATIONAL_ALERT,
            deduplication_key="test-email-dry-run",
            scheduled_at=timezone.now(),
        )
        summary = process_operational_notifications_batch(limit=5)
        notification.refresh_from_db()
        self.assertEqual(notification.status, TenantOperationalNotification.Status.SENT)
        self.assertEqual(summary.delivered, 0)

    @override_settings(LIVIA_ENVIRONMENT="staging", LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED=True, LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN=False)
    def test_staging_email_real_prohibited(self):
        checks = inspect_environment_safety()
        codes = {item.code for item in checks if not item.ok}
        self.assertIn("operational_email_dry_run", codes)

    def test_cancel_on_inactive_membership(self):
        notification = TenantOperationalNotification.objects.create(
            tenant=self.tenant_a,
            recipient_membership=self.admin_membership,
            channel=TenantOperationalNotification.Channel.IN_APP,
            category=TenantOperationalNotification.Category.ALERT,
            severity=TenantOperationalNotification.Severity.CRITICAL,
            event_type=EVENT_ALERT_CRITICAL_CREATED,
            title="Pendente",
            summary="Resumo.",
            status=TenantOperationalNotification.Status.PENDING,
            source_type=TenantOperationalNotification.SourceType.OPERATIONAL_ALERT,
            deduplication_key="test-cancel-membership",
            scheduled_at=timezone.now(),
        )
        self.admin_membership.is_active = False
        self.admin_membership.save(update_fields=["is_active", "updated_at"])
        process_operational_notifications_batch(limit=5)
        notification.refresh_from_db()
        self.assertEqual(notification.status, TenantOperationalNotification.Status.CANCELLED)

    def test_preferences_update(self):
        update_notification_preferences(
            tenant=self.tenant_a,
            membership=self.admin_membership,
            actor=self.admin,
            email_enabled=True,
            notify_on_assignment=False,
        )
        pref = get_or_create_preference(tenant=self.tenant_a, membership=self.admin_membership)
        self.assertTrue(pref.email_enabled)
        self.assertFalse(pref.notify_on_assignment)

    def test_worker_tenant_filter(self):
        alert = self._sync_critical_alert()
        self._process_pending()
        summary = process_operational_notifications_batch(limit=50, tenant_slug="notif-b")
        self.assertEqual(summary.claimed, 0)

    def test_sanitized_content(self):
        alert = self._sync_critical_alert()
        notification = TenantOperationalNotification.objects.first()
        blob = f"{notification.title} {notification.summary}".lower()
        for forbidden in ("api_key", "sk-", "password", "token"):
            self.assertNotIn(forbidden, blob)