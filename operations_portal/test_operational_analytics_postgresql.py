from __future__ import annotations

import unittest
from datetime import timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from knowledge_base.models import TenantOperationalAlert
from knowledge_base.rag.operational_analytics import (
    build_operational_analytics,
    compute_percentiles,
    parse_analytics_period,
)
from tenants.models import Tenant

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL-specific analytics validation.")
@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STORAGES)
class OperationalAnalyticsPostgresqlTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Analytics A", slug="analytics-a")
        self.tenant_b = Tenant.objects.create(name="Analytics B", slug="analytics-b")
        self.admin = get_user_model().objects.create_user(username="analytics-admin", password="pass")

    def test_percentiles_known_dataset(self):
        result = compute_percentiles([10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertTrue(result["has_data"])
        self.assertEqual(result["median_minutes"], 30.0)
        self.assertEqual(result["p75_minutes"], 40.0)
        self.assertEqual(result["p90_minutes"], 50.0)
        self.assertEqual(result["p95_minutes"], 50.0)
        self.assertEqual(result["max_minutes"], 50.0)

    def test_truncdate_respects_local_timezone(self):
        tz = ZoneInfo("America/Sao_Paulo")
        local_now = timezone.now().astimezone(tz)
        utc_value = local_now.astimezone(dt_timezone.utc)

        TenantOperationalAlert.objects.create(
            tenant=self.tenant_a,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_operation_stale",
            fingerprint="analytics-a:stale",
            title="Stale local",
            summary="Evento próximo da meia-noite local para validação TruncDate.",
            detected_at=utc_value,
            last_seen_at=utc_value,
        )
        TenantOperationalAlert.objects.create(
            tenant=self.tenant_b,
            category=TenantOperationalAlert.Category.RAG_OPERATIONS,
            severity=TenantOperationalAlert.Severity.WARNING,
            status=TenantOperationalAlert.Status.OPEN,
            rule_id="rag_operation_stale",
            fingerprint="analytics-b:stale",
            title="Outro tenant",
            summary="Dados similares em outro tenant não devem vazar.",
            detected_at=utc_value,
            last_seen_at=utc_value,
        )

        with override_settings(TIME_ZONE="America/Sao_Paulo"):
            report_a = build_operational_analytics(
                tenant=self.tenant_a,
                period=parse_analytics_period("30d"),
            )

        daily = report_a["trends"]["created_by_day"]
        self.assertEqual(sum(item["total"] for item in daily), 1)

    def test_tenant_isolation_in_aggregates(self):
        now = timezone.now()
        for index, tenant in enumerate([self.tenant_a, self.tenant_b], start=1):
            TenantOperationalAlert.objects.create(
                tenant=tenant,
                category=TenantOperationalAlert.Category.RAG_OPERATIONS,
                severity=TenantOperationalAlert.Severity.CRITICAL,
                status=TenantOperationalAlert.Status.OPEN,
                rule_id="rag_operation_stale",
                fingerprint=f"{tenant.slug}:critical-{index}",
                title=f"Alerta {index}",
                summary="Alerta para validação de isolamento tenant-scoped.",
                detected_at=now - timedelta(hours=index),
                last_seen_at=now,
                ack_due_at=now + timedelta(hours=1),
                resolution_due_at=now + timedelta(hours=4),
            )

        report_a = build_operational_analytics(tenant=self.tenant_a, period="30d")
        report_b = build_operational_analytics(tenant=self.tenant_b, period="30d")
        self.assertEqual(report_a["backlog"]["total_open"], 1)
        self.assertEqual(report_b["backlog"]["total_open"], 1)
