# Fase 12 — Auditoria inicial

**Data:** 2026-08-01
**Base:** Fase 11 (`sync_operational_alerts`, `TenantOperationalAlert`)

## Execução periódica existente

| Mecanismo | Arquivo | Padrão |
|-----------|---------|--------|
| Worker RAG ops | `process_tenant_rag_operations` | oneshot + systemd timer |
| Sync alertas | `sync_operational_alerts` | manual portal/CLI |
| Timer staging | `deploy/staging/livia-rag-operations-worker.timer` | `OnUnitActiveSec=2min` |

**Conclusão:** reutilizar padrão oneshot + timer versionado, não Celery.

## Reutilização do worker RAG

| Padrão RAG | Aplicação monitoring |
|------------|---------------------|
| Lease + stale recovery | Batch `OperationalMonitoringBatchRun` |
| `--limit` por ciclo | `LIVIA_OPERATIONAL_MONITORING_MAX_TENANTS` |
| Gate global + dry-run | `LIVIA_OPERATIONAL_MONITORING_*` |
| Isolamento por tenant | transação por tenant + run filho |

## Riscos de concorrência

* Dois timers simultâneos → batch lock global (`RUNNING` + lease)
* Dois syncs no mesmo tenant → `select_for_update` em alertas (Fase 11)
* SQLite: `select_for_update` + constraint; PostgreSQL: advisory lock opcional

## Custo do snapshot

`build_rag_health_dashboard` agrega ORM por tenant — aceitável com `--limit` e timer 15min.

**Sem chamadas externas:** vector health e readiness usam dados locais.

## Tenants elegíveis

```text
tenant.is_active
TenantRagConfiguration.approved_folder_id preenchido
operational_monitoring_enabled=True  (default False)
gate global LIVIA_OPERATIONAL_MONITORING_ENABLED=True
```

## Modelo de execução escolhido

**Híbrido simples:**

* `OperationalMonitoringBatchRun` — 1 por invocação (lock global)
* `TenantOperationalMonitoringRun` — 1 por tenant processado

Justificativa: auditoria batch + isolamento/falhas por tenant.

## Diagnósticos excluídos do ciclo automático

Nenhum diagnóstico atual chama OpenAI na renderização. Monitoramento reutiliza o mesmo snapshot da Central de Saúde.
