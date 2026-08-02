# Fase 14 — Auditoria inicial (fila operacional e escalonamento)

**Base:** Fase 13 (`TenantOperationalAlert`, governança, SLA, manutenção, silenciamento)

## Priorização atual

Alertas são listados por `-last_seen_at` em `get_operational_alert_list`. Não há prioridade P1–P4 nem ordenação por SLA vencido. Scorecards de governança existem em `build_tenant_governance_summary`.

## Dados disponíveis para fila

| Campo / derivado | Uso na fila |
|------------------|-------------|
| `severity`, `status`, `category`, `rule_id` | Prioridade |
| `assigned_to`, `assigned_at` | Ownership |
| `ack_due_at`, `resolution_due_at` | SLA |
| `occurrence_count` | Ocorrências por sync (≠ reopen) |
| `detected_at`, `last_seen_at` | Tempo aberto |
| `build_alert_governance_state` | Silenciamento, manutenção, SLA derivado |
| Runbooks | Contexto operacional |

**Ausente antes da Fase 14:** `reopen_count`, `escalation_level`, histórico de escalonamento persistido.

## Decisão: fila derivada (Abordagem A)

A fila **não** usa modelo `TenantOperationalWorkItem`. Alertas abertos/acknowledged já concentram ownership, SLA e severidade. Prioridade e ordenação são calculadas em `operational_work_queue.py`. Paginação limita custo de avaliação em Python.

## Decisão: escalonamento no alerta + auditoria

Sem modelo `TenantOperationalEscalation` separado. Campos no alerta:

- `escalation_level` (0–3)
- `escalated_at`, `escalation_trigger`, `escalation_reason`
- `reopen_count`, `last_reopened_at`

Histórico via `AuditEvent` (`escalated`, `deescalated`, `claimed`, `transferred`, `owner_invalidated`).

## Responsáveis

Atribuição via `TenantMembership` (Fase 13). ACK autoatribui se vazio. Membership inativa deve ser invalidada no ciclo de monitoramento.

## Riscos de escalonamento

- Escalar alertas em manutenção de baixo risco → suspenso salvo regra não silenciável
- Escalar repetidamente a cada sync → deduplicar por nível/trigger no mesmo ciclo
- Silenciamento não pausa SLA nem escalonamento (política Fase 14)

## Usuários inativos

`assigned_to.is_active=False` → desatribuir, auditar `owner_invalidated`, elevar prioridade/escalar.

## Concorrência e tenant isolation

Padrão Fase 13: `select_for_update`, validação membership no tenant, POST + CSRF. Testes cross-tenant obrigatórios.

## Hook de integração

```text
process_operational_monitoring
  → sync_operational_alerts
  → process_operational_work_queue (inativos + auto-escalação)
```

Dry-run simula candidatos sem persistir.
