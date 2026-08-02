from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import (
    ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED,
    ACTION_OPERATIONAL_ALERT_ASSIGNED,
    ACTION_OPERATIONAL_ALERT_SILENCED,
    ACTION_OPERATIONAL_ALERT_UNSILENCED,
    ACTION_OPERATIONAL_MAINTENANCE_CANCELLED,
    ACTION_OPERATIONAL_MAINTENANCE_CREATED,
    AuditEvent,
)
from knowledge_base.models import (
    TenantOperationalAlert,
    TenantOperationalAlertSilence,
    TenantOperationalMaintenanceWindow,
    TenantRagConfiguration,
    TenantRagOperationRequest,
)
from knowledge_base.rag.alert_governance import build_alert_governance_state, compute_sla_deadlines
from knowledge_base.rag.alert_governance_services import (
    GovernanceError,
    assign_operational_alert,
    cancel_maintenance_window,
    cancel_operational_alert_silence,
    create_maintenance_window,
    silence_operational_alert,
)
from knowledge_base.rag.operational_alert_sync import acknowledge_operational_alert, sync_operational_alerts
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
    LIVIA_ALERT_CRITICAL_ACK_SLA_MINUTES=30,
    LIVIA_ALERT_CRITICAL_RESOLUTION_SLA_MINUTES=240,
    LIVIA_ALERT_SILENCE_MAX_HOURS=168,
)
class OperationalGovernanceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="gov-admin", password="pass")
        self.operator = get_user_model().objects.create_user(username="gov-operator", password="pass")
        self.foreign = get_user_model().objects.create_user(username="gov-foreign", password="pass")
        self.tenant_a = Tenant.objects.create(name="Gov A", slug="gov-a")
        self.tenant_b = Tenant.objects.create(name="Gov B", slug="gov-b")
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
        self.membership_a = TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.admin,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        self.membership_b = TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.foreign,
            role=TenantMembership.Role.TENANT_ADMIN,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.operator,
            role=TenantMembership.Role.OPERATOR,
        )
        self.client = Client()

    def _create_stale_operation(self, tenant):
        return TenantRagOperationRequest.objects.create(
            tenant=tenant,
            requested_by=self.admin,
            operation=TenantRagOperationRequest.Operation.INVENTORY,
            status=TenantRagOperationRequest.Status.RUNNING,
            dry_run=True,
            run_id="gov-stale",
            started_at=timezone.now() - timedelta(hours=2),
            lease_expires_at=timezone.now() - timedelta(minutes=5),
            attempt_count=1,
            last_heartbeat_at=timezone.now() - timedelta(hours=1),
        )

    def _sync_alert(self, tenant=None):
        tenant = tenant or self.tenant_a
        self._create_stale_operation(tenant)
        return sync_operational_alerts(tenant=tenant, actor=self.admin)

    def _alert(self, tenant=None):
        return TenantOperationalAlert.objects.filter(tenant=tenant or self.tenant_a).first()

    def test_create_maintenance_window(self):
        now = timezone.now()
        window = create_maintenance_window(
            tenant=self.tenant_a,
            actor=self.admin,
            title="Reindexação GP",
            description="Reindexação planejada da base GP durante validação do novo corpus.",
            starts_at=now,
            ends_at=now + timedelta(hours=2),
            scope=TenantOperationalMaintenanceWindow.Scope.CATEGORIES,
            scope_categories=[TenantOperationalAlert.Category.VECTOR_HEALTH],
            request=None,
        )
        self.assertEqual(window.tenant_id, self.tenant_a.pk)
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_OPERATIONAL_MAINTENANCE_CREATED, tenant=self.tenant_a).exists()
        )

    def test_invalid_maintenance_end_before_start(self):
        now = timezone.now()
        with self.assertRaises(GovernanceError):
            create_maintenance_window(
                tenant=self.tenant_a,
                actor=self.admin,
                title="Janela inválida com título suficiente",
                description="Motivo válido com mais de dez caracteres para auditoria.",
                starts_at=now,
                ends_at=now - timedelta(minutes=1),
                scope="all",
                request=None,
            )

    def test_maintenance_scope_matches_category(self):
        self._sync_alert()
        alert = self._alert()
        now = timezone.now()
        create_maintenance_window(
            tenant=self.tenant_a,
            actor=self.admin,
            title="Manutenção RAG ops",
            description="Manutenção planejada apenas para operações RAG do tenant.",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1),
            scope=TenantOperationalMaintenanceWindow.Scope.CATEGORIES,
            scope_categories=[TenantOperationalAlert.Category.RAG_OPERATIONS],
            request=None,
        )
        state = build_alert_governance_state(alert=alert)
        self.assertTrue(state.is_under_maintenance)

    def test_non_suppressible_rule_stays_visible_when_silenced(self):
        alert = TenantOperationalAlert.objects.create(
            tenant=self.tenant_a,
            category=TenantOperationalAlert.Category.INTEGRATION_SAFETY,
            severity=TenantOperationalAlert.Severity.CRITICAL,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="integration_safety",
            fingerprint="integration_safety:test",
            title="Integration safety",
            summary="Side effect inseguro detectado",
            detected_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        with self.assertRaises(GovernanceError):
            silence_operational_alert(
                tenant=self.tenant_a,
                alert_id=alert.pk,
                actor=self.admin,
                reason="Tentativa inválida de silenciar regra crítica de integração.",
                duration_key="1h",
            )
        TenantOperationalAlertSilence.objects.create(
            tenant=self.tenant_a,
            alert=alert,
            reason="Silenciamento manual simulado para teste de visibilidade crítica.",
            starts_at=timezone.now() - timedelta(minutes=1),
            ends_at=timezone.now() + timedelta(hours=1),
            created_by=self.admin,
        )
        state = build_alert_governance_state(alert=alert)
        self.assertFalse(state.is_silenced)
        self.assertFalse(state.suppress_operational_noise)

    def test_silence_and_expiration(self):
        self._sync_alert()
        alert = self._alert()
        silence = silence_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            reason="Reindexação planejada da base GP durante validação do novo corpus.",
            duration_key="1h",
        )
        state = build_alert_governance_state(alert=alert)
        self.assertTrue(state.is_silenced)
        silence.ends_at = timezone.now() - timedelta(minutes=1)
        silence.save(update_fields=["ends_at"])
        state = build_alert_governance_state(alert=alert)
        self.assertFalse(state.is_silenced)

    def test_cancel_silence_audit(self):
        self._sync_alert()
        alert = self._alert()
        silence_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            reason="Silenciamento temporário para validação operacional controlada.",
            duration_key="4h",
        )
        cancel_operational_alert_silence(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_UNSILENCED, tenant=self.tenant_a).exists()
        )

    def test_assign_membership_same_tenant(self):
        self._sync_alert()
        alert = self._alert()
        assign_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            membership_id=self.membership_a.pk,
        )
        alert.refresh_from_db()
        self.assertEqual(alert.assigned_to_id, self.membership_a.pk)

    def test_cross_tenant_assignment_blocked(self):
        self._sync_alert()
        alert = self._alert()
        with self.assertRaises(GovernanceError):
            assign_operational_alert(
                tenant=self.tenant_a,
                alert_id=alert.pk,
                actor=self.admin,
                membership_id=self.membership_b.pk,
            )

    def test_ack_auto_assigns_when_unassigned(self):
        self._sync_alert()
        alert = self._alert()
        acknowledge_operational_alert(tenant=self.tenant_a, alert_id=alert.pk, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.assigned_to.user_id, self.admin.pk)
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_ASSIGNED).exists())
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_ACKNOWLEDGED).exists())

    def test_sla_deadlines_on_create_and_reopen(self):
        self._sync_alert()
        alert = self._alert()
        self.assertIsNotNone(alert.ack_due_at)
        self.assertIsNotNone(alert.resolution_due_at)
        original_ack = alert.ack_due_at
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertNotEqual(alert.ack_due_at, original_ack)

    def test_sla_not_extended_on_same_batch_update(self):
        self._create_stale_operation(self.tenant_a)
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin, sync_batch_id="batch-1")
        alert = self._alert()
        original_ack = alert.ack_due_at
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin, sync_batch_id="batch-1")
        alert.refresh_from_db()
        self.assertEqual(alert.ack_due_at, original_ack)
        self.assertEqual(alert.occurrence_count, 1)

    def test_sla_breached_state(self):
        self._sync_alert()
        alert = self._alert()
        alert.ack_due_at = timezone.now() - timedelta(minutes=5)
        alert.save(update_fields=["ack_due_at", "updated_at"])
        state = build_alert_governance_state(alert=alert)
        self.assertEqual(state.sla_state, "breached")
        self.assertTrue(state.ack_sla_breached)

    def test_maintenance_pauses_sla(self):
        self._sync_alert()
        alert = self._alert()
        now = timezone.now()
        create_maintenance_window(
            tenant=self.tenant_a,
            actor=self.admin,
            title="Manutenção banco",
            description="Manutenção programada do banco durante validação operacional.",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=2),
            scope=TenantOperationalMaintenanceWindow.Scope.CATEGORIES,
            scope_categories=[TenantOperationalAlert.Category.RAG_OPERATIONS],
            request=None,
        )
        alert.ack_due_at = timezone.now() - timedelta(minutes=10)
        alert.save(update_fields=["ack_due_at", "updated_at"])
        state = build_alert_governance_state(alert=alert)
        self.assertEqual(state.sla_state, "paused")

    def test_auto_resolution_while_silenced(self):
        self._sync_alert()
        alert = self._alert()
        silence_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            reason="Silenciamento temporário enquanto operação stale é corrigida.",
            duration_key="1h",
        )
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.lease_expires_at = timezone.now() + timedelta(hours=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert.refresh_from_db()
        self.assertEqual(alert.status, TenantOperationalAlert.Status.RESOLVED)

    def test_reopen_under_maintenance(self):
        self._sync_alert()
        alert = self._alert()
        operation = TenantRagOperationRequest.objects.get(tenant=self.tenant_a)
        operation.status = TenantRagOperationRequest.Status.SUCCEEDED
        operation.save(update_fields=["status", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        now = timezone.now()
        create_maintenance_window(
            tenant=self.tenant_a,
            actor=self.admin,
            title="Manutenção ops",
            description="Manutenção programada de operações RAG durante deploy controlado.",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1),
            scope=TenantOperationalMaintenanceWindow.Scope.CATEGORIES,
            scope_categories=[TenantOperationalAlert.Category.RAG_OPERATIONS],
            request=None,
        )
        operation.status = TenantRagOperationRequest.Status.RUNNING
        operation.lease_expires_at = timezone.now() - timedelta(minutes=1)
        operation.save(update_fields=["status", "lease_expires_at", "updated_at"])
        sync_operational_alerts(tenant=self.tenant_a, actor=self.admin)
        alert = TenantOperationalAlert.objects.get(tenant=self.tenant_a)
        state = build_alert_governance_state(alert=alert)
        self.assertEqual(alert.status, TenantOperationalAlert.Status.OPEN)
        self.assertTrue(state.is_under_maintenance)

    def test_cancel_maintenance(self):
        now = timezone.now()
        window = create_maintenance_window(
            tenant=self.tenant_a,
            actor=self.admin,
            title="Manutenção curta",
            description="Manutenção curta para teste de cancelamento com auditoria.",
            starts_at=now,
            ends_at=now + timedelta(hours=1),
            scope="all",
            request=None,
        )
        cancel_maintenance_window(
            tenant=self.tenant_a,
            window_id=window.pk,
            actor=self.admin,
            cancellation_note="Cancelamento por mudança de janela de deploy em staging.",
        )
        window.refresh_from_db()
        self.assertEqual(window.status, TenantOperationalMaintenanceWindow.Status.CANCELLED)
        self.assertTrue(
            AuditEvent.objects.filter(action=ACTION_OPERATIONAL_MAINTENANCE_CANCELLED).exists()
        )

    def test_sanitize_short_reason(self):
        with self.assertRaises(GovernanceError):
            silence_operational_alert(
                tenant=self.tenant_a,
                alert_id=999,
                actor=self.admin,
                reason="curto",
                duration_key="1h",
            )

    def test_portal_maintenance_list_empty(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("operations_portal:knowledge_base_maintenance"),
            {"tenant": self.tenant_a.pk},
        )
        self.assertEqual(response.status_code, 200)

    def test_portal_alert_filters_unassigned(self):
        self._sync_alert()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("operations_portal:knowledge_base_alerts"),
            {"tenant": self.tenant_a.pk, "unassigned": "yes"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operação RAG stale")

        now = timezone.now()
        ack_due, resolution_due = compute_sla_deadlines(
            severity=TenantOperationalAlert.Severity.CRITICAL,
            detected_at=now,
        )
        self.assertTrue(timezone.is_aware(ack_due))
        self.assertTrue(timezone.is_aware(resolution_due))

    def test_silence_audit_event(self):
        self._sync_alert()
        alert = self._alert()
        silence_operational_alert(
            tenant=self.tenant_a,
            alert_id=alert.pk,
            actor=self.admin,
            reason="Silenciamento auditável com motivo suficientemente descritivo.",
            duration_key="24h",
        )
        self.assertTrue(AuditEvent.objects.filter(action=ACTION_OPERATIONAL_ALERT_SILENCED).exists())
