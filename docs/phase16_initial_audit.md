# Fase 16 — Auditoria inicial: Analytics Operacional

## Dados disponíveis

| Fonte | Campos / eventos úteis |
|-------|------------------------|
| `TenantOperationalAlert` | lifecycle, SLA, assignment, reopen, escalation, occurrence |
| `AuditEvent` | timeline de ações (`created_at` + metadata) |
| `OperationalMonitoringBatchRun` | throughput sync, duração, falhas |
| `TenantOperationalMonitoringRun` | por tenant |
| `TenantOperationalNotification` | entrega/leitura in-app |
| `TenantOperationalNotificationWorkerRun` | execuções worker |

## Eventos deriváveis diretamente

- Volume no período: `detected_at`, `acknowledged_at`, `resolved_at`, `last_reopened_at`
- Backlog atual: status open/acknowledged + governança
- MTTA/MTTR: timestamps do alerta (não depende de audit)
- SLA compliance: `ack_due_at`/`resolution_due_at` vs timestamps reais + `compute_sla_state` para pausa
- Prioridade P1–P4: `calculate_operational_priority` existente
- Escalonamento: `escalation_level`, `escalation_trigger`, audit
- Capacity: backlog por `assigned_to` + pesos P1–P4
- Notificações: status/read_at por channel
- Monitoramento: batch/tenant runs

## Métricas que exigiriam histórico inexistente

- Backlog ao **fim de cada dia** passado (sem snapshots diários)
- SLA pausado retroativo minuto a minuto (deadline não é estendido; pausa é runtime)
- Tempo até leitura antes da Fase 15 (notificações)

**Decisão:** cálculo direto + tendência diária aproximada por eventos; snapshots **não** criados nesta fase.

## Distorções conhecidas

- Manutenção **suprime** breach em runtime, não altera `ack_due_at`
- `occurrence_count` ≠ `reopen_count` (polling vs ciclo)
- Percentis em SQLite via Python (amostra limitada) vs PostgreSQL nativo
- Alertas INFO sem SLA excluídos de compliance

## Denominadores

Documentados por métrica no service (`population_note` nos payloads).

Ausência de amostra → `"Sem dados suficientes"`, não zero.

## Queries de maior custo

- Iteração open alerts + `build_alert_governance_state` (O(n) inevitável hoje)
- Tendência diária: aggregates com `TruncDate`
- Ownership: single pass compartilhando open queryset

**Mitigação:** queryset base único, filtros compartilhados, paginação em drill-down.

## Estratégia recomendada

1. `knowledge_base/rag/operational_analytics.py` — service central
2. `operations_portal/operational_analytics_services.py` — serialização portal
3. Reutilizar `parse_health_period` estendido com `90d`
4. Sem novo modelo / migration
5. CLI reutiliza o mesmo service
