# Fase 12 — Relatório final: Monitoramento operacional automático

**Data:** 2026-08-01
**Branch:** `chore/postgresql-readiness`
**Working tree:** alterações Fase 12 **sem commit**

---

## 1. Diagnóstico inicial

Ver `docs/phase12_initial_audit.md`. Reutilizado padrão do worker RAG (`oneshot` + timer) e serviços da Fase 11 sem duplicar regras de alerta.

## 2. Arquitetura implementada

```text
systemd timer (template) / portal POST / CLI
        ↓
process_operational_monitoring
        ↓
OperationalMonitoringBatchRun (lock + agregados)
        ↓
TenantOperationalMonitoringRun (por tenant, transação isolada)
        ↓
sync_operational_alerts (dry-run opcional)
        ↓
TenantOperationalAlert + AuditEvent
```

## 3. Modelo de execução

**Decisão:** híbrido batch + run por tenant.

| Modelo | Papel |
|--------|-------|
| `OperationalMonitoringBatchRun` | Lock global, métricas agregadas, lease/heartbeat |
| `TenantOperationalMonitoringRun` | Resultado por tenant, falhas isoladas |

## 4. Tenants elegíveis

`is_active` + `approved_folder_id` + `operational_monitoring_enabled=True`.

Portal manual processa tenant ativo mesmo sem flag (operador explícito).

## 5. Feature gates

| Gate | Default |
|------|---------|
| `LIVIA_OPERATIONAL_MONITORING_ENABLED` | `False` |
| `LIVIA_OPERATIONAL_MONITORING_DRY_RUN` | `True` |
| `operational_monitoring_enabled` | `False` |

Automático (`scheduler`, `--all-eligible`) exige gate global. CLI `--tenant` e portal não exigem.

## 6. Dry-run

Automático respeita `LIVIA_OPERATIONAL_MONITORING_DRY_RUN`. Portal manual executa sync **real** (preserva Fase 11).

## 7. Concorrência e locks

* Lease em batch (`LIVIA_OPERATIONAL_MONITORING_LEASE_SECONDS=900`)
* Batch `RUNNING` com lease válido bloqueia nova execução
* PostgreSQL: `pg_try_advisory_lock`
* `IntegrityError` retry em alertas (herança Fase 11)

## 8. Lease/heartbeat

Implementado no batch: `lease_expires_at`, `last_heartbeat_at` atualizado entre tenants. Recuperação stale via `recover_stale_monitoring_batches()`.

## 9. Isolamento de falhas

Loop por tenant com try/except; status `partial` quando mix success/failure; erros categorizados e sanitizados.

## 10. CLI

`process_operational_monitoring` + `prune_operational_monitoring_runs`.

## 11. Systemd

Templates versionados em `deploy/staging/livia-operational-monitoring.{service,timer}` — **não habilitados**.

## 12. Central de Saúde

Seção **Monitoramento automático** + POST existente adaptado para criar run com trigger `portal`.

## 13. Readiness

Seção `monitoring` adicionada em `build_consolidated_readiness` via `build_monitoring_readiness_checks`.

## 14. Auditoria

Actions: `operational_monitoring.started`, `.completed`, `.partial`, `.failed`, `.skipped`, `.recovered`.

## 15. Métricas

`build_tenant_monitoring_summary` + agregados 24h (execuções, success rate, mediana duração, alertas).

## 16. Retenção

`prune_operational_monitoring_runs` — default 90 dias (`LIVIA_OPERATIONAL_MONITORING_RETENTION_DAYS`).

## 17. Tenant isolation

Runs e alertas filtrados por tenant; testes cobrem falha parcial e CLI por slug.

## 18. Segurança

Nenhuma chamada externa no monitoramento — apenas snapshot local + sync de alertas.

## 19. Arquivos principais

| Arquivo | Finalidade |
|---------|------------|
| `knowledge_base/rag/operational_monitoring.py` | Serviço core |
| `knowledge_base/models.py` | Modelos + flag tenant |
| `knowledge_base/management/commands/process_operational_monitoring.py` | CLI worker |
| `knowledge_base/management/commands/prune_operational_monitoring_runs.py` | Retenção |
| `deploy/staging/livia-operational-monitoring.*` | Templates systemd |
| `operations_portal/test_operational_monitoring_portal.py` | Testes |
| `operations_portal/knowledge_base_views.py` | Portal integrado |
| `operations_portal/templates/.../health.html` | UI monitoring |

## 20. Migrations

* `knowledge_base/migrations/0014_operational_monitoring.py`
* `audit/migrations/0011_operational_monitoring.py`

## 21. Testes

| Suíte | Resultado |
|-------|-----------|
| `manage.py check` | passed |
| Fase 12 (`test_operational_monitoring_portal`) | 11 passed |
| Fase 11 regressão | 18 passed |
| Suíte completa SQLite | **616 passed**, 11 skipped |
| PostgreSQL concorrência/advisory lock | **pendente** (infra indisponível) |

## 22. Pendências PostgreSQL

* Validar advisory lock sob concorrência real
* Validar `select_for_update` em batch lock com múltiplos workers

## 23. Riscos restantes

| Risco | Severidade |
|-------|------------|
| Timer não ativado (por design) | Baixa |
| Staging VPS bloqueado | Operacional |
| Concorrência só parcialmente validada em SQLite | Média |

## 24. Runbook de ativação

Documentado em `docs/phase12_operational_monitoring.md` — **não executado**.

## 25. Veredito

```text
FASE 12 CONCLUÍDA — GO CONDICIONAL
```

Condições: gate/timer desligados por padrão; testes PostgreSQL de concorrência pendentes; alterações sem commit/push; staging físico ainda bloqueado.
