# Fase 14 — Fila operacional e escalonamento interno

## Decisões

| Tópico | Decisão |
|--------|---------|
| Fila | **Derivada** (Abordagem A) — sem `TenantOperationalWorkItem` |
| Escalonamento | Campos no alerta + auditoria (sem modelo separado) |
| Notificações | Apenas portal (badge, scorecards) |

## Prioridade P1–P4

Calculada em `knowledge_base/rag/operational_work_queue.py` via `calculate_operational_priority()`.

Considera: severidade, SLA vencido, responsável ausente, escalonamento, manutenção (redução derivada), regras não silenciáveis.

## Escalonamento (níveis 0–3)

| Nível | Significado |
|-------|-------------|
| 0 | Normal |
| 1 | Atenção da operação |
| 2 | Gestão |
| 3 | Crítico administrativo |

Gatilhos automáticos (via `process_operational_work_queue` após sync):

- ACK SLA vencido
- Resolution SLA vencido
- Crítico sem responsável (threshold configurável)
- Reaberturas repetidas (`reopen_count`)
- Responsável/membership inativa

Manutenção ativa **suspende** auto-escalação salvo regras não silenciáveis. Silenciamento **não** pausa SLA nem escalonamento.

## Campos novos em `TenantOperationalAlert`

`reopen_count`, `last_reopened_at`, `escalation_level`, `escalated_at`, `escalation_trigger`, `escalation_reason`

## Serviços

- `operational_work_queue.py` — prioridade, ordenação, summary
- `operational_work_queue_services.py` — claim, transfer, escalate/deescalate, processamento batch

Integração:

```text
process_operational_monitoring → sync → process_operational_work_queue
```

Dry-run simula candidatos sem persistir.

## Portal

| Rota | Descrição |
|------|-----------|
| `/painel/operacoes/minhas-pendencias/` | Fila pessoal |
| `/painel/operacoes/fila/` | Fila do tenant + scorecards |
| Ações no detalhe do alerta | Assumir, transferir, escalar, encerrar escalonamento |

## RBAC

| Capability | Ações |
|------------|-------|
| `knowledge_base.view` | Ver filas |
| `knowledge_base.operate` | Assumir, ACK, resolver |
| `knowledge_base.configure` | Transferir, desatribuir, escalar, encerrar escalonamento |

## Auditoria

`operational_alert.claimed`, `.transferred`, `.escalated`, `.deescalated`, `.owner_invalidated`

## Settings

```text
LIVIA_ALERT_ESCALATION_UNASSIGNED_CRITICAL_MINUTES
LIVIA_ALERT_ESCALATION_REOPEN_THRESHOLD
LIVIA_ALERT_ESCALATION_SUSPEND_UNDER_MAINTENANCE
```

## CLI

```bash
python manage.py operational_work_queue_report --tenant <slug> [--json]
```

## Fase 15

Notificações operacionais in-app e outbox dedicada — ver `docs/phase15_operational_notifications.md`. A fila continua derivada do alerta; notificações são camada separada pós-evento.

## Fase 16

Analytics operacional tenant-scoped — ver `docs/phase16_operational_analytics.md`.
