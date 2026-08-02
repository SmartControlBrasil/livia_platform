from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_ALERT_CLAIMED,
    ACTION_OPERATIONAL_ALERT_ESCALATED,
    ACTION_OPERATIONAL_ALERT_OWNER_INVALIDATED,
    ACTION_OPERATIONAL_ALERT_TRANSFERRED,
    AuditEvent,
)
from knowledge_base.models import TenantOperationalAlert, TenantRagConfiguration, TenantRagOperationRequest
from knowledge_base.rag.alert_governance import build_alert_governance_state
from knowledge_base.rag.operational_alert_sync import sync_operational_alerts
from knowledge_base.rag.operational_work_queue import (
    PRIORITY_P1,
    PRIORITY_P2,
    calculate_operational_priority,
    evaluate_auto_escalation,
)
from knowledge_base.rag.operational_work_queue_services import (
    WorkQueueError,
    claim_operational_alert,
    process_operational_work_queue,
    transfer_operational_alert,
)
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
    LIVIA_ALERT_ESCALATION_UNASSIGNED_CRITICAL_MINUTES=0,
    LIVIA_ALERT_ESCALATION_REOPEN_THRESHOLD=2,
)
class OperationalWorkQueueTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="wq-admin", password="pass")
        self.operator = get_user_model().objects.create_user(username="wq-operator", password="pass")
        self.foreign = get_user_model().objects.create_user(username="wq-foreign", password="pass")
        self.tenant_a = Tenant.objects.create(name="WQ A", slug="wq-a")
        self.tenant_b = Tenant.objects.create(name="WQ B", slug="wq-b")
        TenantRagConfiguration.objects.create(
            tenant=self.tenant_a,
            approved_folder_id="folder-a",
            sync_enabled=True,
            retrieval_enabled=True,
        )
        self.membership_a = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.operator_membership = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.operator,
            role=TenantMembership.Role.OPERATOR,
        )
        self.foreign_membership = TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.foreign,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.client = Client()

    def _create_stale_operation(self, tenant):
        return TenantRagOperationRequest.objects.create(
            tenant=tenant,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="wq-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def _sync_alert(self, tenant=None):
        self._create_stale_operation(tenant or self.tenant_a)
        sync_operational_alerts(tenant=tenant or self.tenant_a, actor=self.admin)

    def test_priority_critical_unassigned_is_p1(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        priority = calculate_operational_priority(alert=alert)
        self.assertEqual(priority, PRIORITY_P1)

    def test_claim_assigns_membership(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        claim_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.assigned_to_id, self.membership_a.pk)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_CLAIMED).exists())

    def test_claim_conflict_when_assigned(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        alert.assigned_to = self.membership_a
        alert.save(update_fields=["assigned_to", "updated_at"])
        with self.assertRaises(WorkQueueError):
            claim_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.operator)

    def test_transfer_cross_tenant_blocked(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        with self.assertRaises(WorkQueueError):
            transfer_operational_alert(
                tenant=self.tenant_a,
                alert_id=alert.pk,
                actor=self.admin,
                membership_id=self.foreign_membership.pk,
                reason="Tentativa inválida de transferência cross-tenant para outro tenant.",
            )

    def test_transfer_valid_membership(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        transfer_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            membership_id=self.operator_membership.pk,
            reason="Transferência operacional para operador de plantão do tenant.",
        )
        alert.refresh_from_db()
        self.assertEqual(alert.assigned_to_id, self.operator_membership.pk)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_TRANSFERRED).exists())

    def test_auto_escalation_ack_sla(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        alert.ack_due_at = timezone.now() - timedelta(minutes=5)
        alert.save(update_fields=["ack_due_at", "updated_at"])
        candidate = evaluate_auto_escalation(alert=alert)
        self.assertIsNotNone(candidate)
        result = process_operational_work_queue(tenant=self.tenant_a, actor=self.admin)
        self.assertEqual(result.auto_escalated, 1)
        alert.refresh_from_db()
        self.assertGreater(alert.escalation_level, 0)

    def test_auto_escalation_dry_run(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        alert.ack_due_at = timezone.now() - timedelta(minutes=5)
        alert.save(update_fields=["ack_due_at", "updated_at"])
        result = process_operational_work_queue(tenant=self.tenant_a, dry_run=True)
        self.assertGreater(len(result.candidates), 0)
        alert.refresh_from_db()
        self.assertEqual(alert.escalation_level, 0)

    def test_inactive_owner_invalidated(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        alert.assigned_to = self.membership_a
        alert.save(update_fields=["assigned_to", "updated_at"])
        self.membership_a.is_active = False
        self.membership_a.save(update_fields=["is_active", "updated_at"])
        result = process_operational_work_queue(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(result.inactive_owners_cleared, 1)
        self.assertIsNone(alert.assigned_to_id)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_OWNER_INVALIDATED).exists())

    def test_reopen_count_and_priority(self):
        self._sync_alert()
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.reopen_count, 1)
        alert.reopen_count = 2
        alert.save(update_fields=["reopen_count", "updated_at"])
        process_operational_work_queue(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertGreater(alert.escalation_level, 0)

    def test_personal_queue_portal(self):
        self._sync_alert()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations_portal:operational_my_work"), {"tenant": self.tenant_a.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operação RAG stale")

    def test_tenant_queue_portal(self):
        self._sync_alert()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations_portal:operational_work_queue"), {"tenant": self.tenant_a.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "P1")

        alert = TenantOperationalAlert.objects.create(
            tenant=self.tenant_a,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_operation_stale",
            fingerprint="wq:stale",
            title="Stale op",
            summary="Operação stale para teste de silenciamento sem pausar SLA.",
            detected_at=timezone.now() - timedelta(hours=2),
            last_seen_at=timezone.now(),
            ack_due_at=timezone.now() - timedelta(minutes=10),
            resolution_due_at=timezone.now() + timedelta(hours=1),
        )
        from knowledge_base.models import TenantOperationalAlertSilence

        TenantOperationalAlertSilence.objects.create(
            tenant=self.tenant_a,
            alert=alert,
            reason="Silenciamento temporário durante validação operacional controlada.",
            starts_at=timezone.now() - timedelta(minutes=1),
            ends_at=timezone.now() + timedelta(hours=1),
            created_by=self.admin,
        )
        governance = build_alert_governance_state(alert=alert)
        candidate = evaluate_auto_escalation(alert=alert, governance=governance)
        self.assertIsNotNone(candidate)

    def test_priority_warning_sla_breached_is_p2(self):
        alert = TenantOperationalAlert.objects.create(
            tenant=self.tenant_a,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.ACKNOWLEDGED,
            rule_id="rag_operation_stale",
            fingerprint="wq:warn",
            title="Warning stale",
            summary="Alerta warning com SLA de resolução vencido para prioridade.",
            detected_at=timezone.now() - timedelta(days=1),
            last_seen_at=timezone.now(),
            resolution_due_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(calculate_operational_priority(alert=alert), PRIORITY_P2)
