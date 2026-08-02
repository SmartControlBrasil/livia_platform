# Fase 12 — Monitoramento operacional automático

## Visão geral

Automatiza o fluxo da Fase 11 via execução periódica one-shot (systemd timer), sem Celery/Redis e sem notificações externas.

```text
Agendador (systemd timer — template only)
   ↓
process_operational_monitoring
   ↓
OperationalMonitoringBatchRun (lock global)
   ↓
TenantOperationalMonitoringRun (por tenant)
   ↓
sync_operational_alerts (snapshot local)
   ↓
TenantOperationalAlert
```

## Feature gates

| Setting | Default | Efeito |
|---------|---------|--------|
| `LIVIA_OPERATIONAL_MONITORING_ENABLED` | `False` | Gate global para execução automática (`scheduler`, `--all-eligible`) |
| `LIVIA_OPERATIONAL_MONITORING_DRY_RUN` | `True` | Dry-run em execuções automáticas |
| `TenantRagConfiguration.operational_monitoring_enabled` | `False` | Tenant elegível para `--all-eligible` |

**Manual (portal / `--tenant`):** não exige gate global; portal executa sync real (como Fase 11).

## Tenants elegíveis

```text
tenant.is_active
approved_folder_id preenchido
operational_monitoring_enabled=True
```

## Comandos

```bash
# Automático (requer gate global)
python manage.py process_operational_monitoring --all-eligible --limit 20

# Manual por tenant (sem gate global)
python manage.py process_operational_monitoring --tenant <slug> --no-dry-run

# Dry-run explícito
python manage.py process_operational_monitoring --tenant <slug> --dry-run

# Recuperação stale
python manage.py process_operational_monitoring --recover-stale-only

# Retenção (default 90 dias)
python manage.py prune_operational_monitoring_runs
python manage.py prune_operational_monitoring_runs --days 30 --json
```

## Concorrência

* Batch `RUNNING` + lease (`LIVIA_OPERATIONAL_MONITORING_LEASE_SECONDS`, default 900s)
* PostgreSQL: advisory lock
* SQLite: `select_for_update` + verificação de batch ativo
* Execução concorrente → status `skipped` (`concurrency_conflict`)

## Stale recovery

Batch `RUNNING` com lease expirado → `failed` + audit `operational_monitoring.recovered`.

## Systemd (templates — não habilitar nesta fase)

| Arquivo | Função |
|---------|--------|
| `deploy/staging/livia-operational-monitoring.service` | oneshot |
| `deploy/staging/livia-operational-monitoring.timer` | a cada 15 min + `RandomizedDelaySec=120` |

## Runbook de ativação (não executado nesta fase)

1. Aplicar migrations
2. `manage.py check`
3. Configurar `LIVIA_OPERATIONAL_MONITORING_ENABLED=true`
4. Manter `LIVIA_OPERATIONAL_MONITORING_DRY_RUN=true`
5. Habilitar `operational_monitoring_enabled` por tenant (admin/CLI)
6. Executar `process_operational_monitoring --tenant <slug> --dry-run`
7. Revisar batch/run no admin ou Central de Saúde
8. Executar manual com `--no-dry-run` em tenant piloto
9. Copiar/habilitar timer systemd
10. Observar execuções
11. Somente depois considerar `LIVIA_OPERATIONAL_MONITORING_DRY_RUN=false`

**Rollback:**

1. Desabilitar timer
2. `LIVIA_OPERATIONAL_MONITORING_ENABLED=false`
3. Alertas existentes permanecem
4. Investigar último `OperationalMonitoringBatchRun`

## Segurança

O monitoramento **não** executa sync Drive, indexação, OpenAI, CRM, webhooks ou e-mail. Apenas leitura local + sync de alertas internos.

## Limitações

* Timer não ativado por padrão
* Gate global desligado por padrão
* “Próxima execução” não exibida (sem consulta systemd no Django)
* Concorrência PostgreSQL validada condicionalmente (GO CONDICIONAL)

## Integração Fase 13

`sync_operational_alerts` recebe `sync_batch_id=str(batch.pk)` durante monitoramento automático para contabilizar **uma ocorrência por regra por execução** (evita inflar `occurrence_count` a cada polling). SLA e governança operacional documentados em `docs/phase13_operational_governance.md`.
