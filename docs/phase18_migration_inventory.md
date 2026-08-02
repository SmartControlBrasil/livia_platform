# Fase 18 — Inventário de migrations operacionais

## Grafo validado

```text
knowledge_base:
  0012 → 0013_operational_alerts
       → 0014_operational_monitoring
       → 0015_operational_governance
       → 0016_operational_work_queue
       → 0017_operational_notifications

audit:
  0009 → 0010_operational_alerts
       → 0011_operational_monitoring
       → 0012_operational_governance
       → 0013_operational_work_queue
       → 0014_operational_notifications
       → 0015_operational_analytics
```

`python manage.py migrate --plan` → **No planned migration operations.**
`python manage.py makemigrations --check --dry-run` → **No changes detected.**

---

## knowledge_base

| # | Arquivo | Dep | Fase | Operações principais |
|---|---------|-----|------|----------------------|
| 0013 | `operational_alerts.py` | `0012` | 11 | `TenantOperationalAlert`; unique `(tenant, fingerprint)`; índices tenant/status/severity/category/last_seen/fingerprint |
| 0014 | `operational_monitoring.py` | `0013` | 12 | `OperationalMonitoringBatchRun`, `TenantOperationalMonitoringRun`; flag `operational_monitoring_enabled` em `TenantRagConfiguration` |
| 0015 | `operational_governance.py` | `0014` | 13 | `TenantOperationalAlertSilence`, `TenantOperationalMaintenanceWindow`; SLA fields em alert; ownership `assigned_to`/`assigned_by` SET_NULL |
| 0016 | `operational_work_queue.py` | `0015` | 14 | Escalation fields em alert; índice `escalation_level` |
| 0017 | `operational_notifications.py` | `0016` | 15 | `TenantOperationalNotificationPreference`, `TenantOperationalNotification`, `TenantOperationalNotificationWorkerRun`; unique `deduplication_key`; índices alinhados Django |

### Constraints críticas (0013–0017)

| Constraint | Model |
|------------|-------|
| `unique_operational_alert_fingerprint_per_tenant` | `TenantOperationalAlert` |
| `unique_operational_notification_pref_per_membership` | `TenantOperationalNotificationPreference` |
| `unique_operational_notification_dedupe_key` | `TenantOperationalNotification` |

### Ruído 0017 — resolvido (Fase 18)

Migration usava nomes customizados (`knowledge_b_tenant__notif_read_idx`, etc.). Django 6.0.6 esperava hashes automáticos (`knowledge_b_tenant__91f759_idx`, etc.).

**Solução:** Opção A — corrigir nomes na migration untracked `0017` (não publicada). Sem migration `0018` de rename.

---

## audit

| # | Arquivo | Dep | Fase | Operações |
|---|---------|-----|------|-----------|
| 0010 | `operational_alerts.py` | `0009` | 11 | `AlterField` action — eventos `operational_alert.*` |
| 0011 | `operational_monitoring.py` | `0010` | 12 | `AlterField` — eventos monitoring |
| 0012 | `operational_governance.py` | `0011` | 13 | `AlterField` — eventos governance |
| 0013 | `operational_work_queue.py` | `0012` | 14 | `AlterField` — eventos work queue |
| 0014 | `operational_notifications.py` | `0013` | 15 | `AlterField` — eventos notifications |
| 0015 | `operational_analytics.py` | `0014` | 16 | `AlterField` — eventos analytics |

Todas reversíveis via `AlterField` (sem RunPython destrutivo).

---

## Defaults e nullability (amostra auditada)

| Campo | null | on_delete | Notas |
|-------|------|-----------|-------|
| `acknowledged_by`, `resolved_by` | null=True | SET_NULL | histórico preservado |
| `assigned_to`, `assigned_by` | null=True | SET_NULL | ownership revogável |
| `silence.created_by`, `cancelled_by` | null=True | SET_NULL | audit trail |
| `maintenance.created_by`, `cancelled_by` | null=True | SET_NULL | audit trail |
| `recipient_membership` | NOT NULL | CASCADE | notificação ligada ao membership |

---

## Migrations publicadas (HEAD) — não editadas

Tracked em HEAD até `knowledge_base/0012`, `audit/0009`. Migrations `0013+` / `0010+` são untracked e foram corrigidas antes do primeiro commit.
