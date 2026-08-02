# Fase 13 — Auditoria inicial

**Data:** 2026-08-01
**Base:** Fases 10–12 (alertas + monitoramento)

## Criação/atualização de alertas hoje

`sync_operational_alerts` em `operational_alert_sync.py`:

* avalia candidatos via `evaluate_alert_candidates`;
* upsert por `(tenant, fingerprint)`;
* incrementa `occurrence_count` a cada sync;
* reopen limpa ACK/resolução;
* auto-resolve quando candidato desaparece.

**Ponto de integração suppression:** após persistência, estados derivados via `alert_governance.py` — **não** alterar fingerprint/deduplicação.

## Decisão de representação (Abordagem A)

Manter status `open | acknowledged | resolved` e adicionar estados **derivados**:

```text
is_silenced
is_under_maintenance
suppress_operational_noise
sla_state
```

Silenciamento em modelo separado `TenantOperationalAlertSilence`.

## Atribuição

`TenantMembership` já existe com roles. FK `assigned_to` → `TenantMembership` (validação tenant).

ACK sem responsável → autoatribui membership do actor.

## SLA

Deadlines persistidos em `ack_due_at` / `resolution_due_at` na abertura e reopen.

Manutenção ativa → `sla_state=paused` (não estende deadline).

Silenciamento manual → SLA continua correndo.

## Regras não silenciáveis

Centralizadas em `alert_governance.py`:

```text
provider_forbidden
integration_safety
tenant_isolation (categoria/rule)
```

Permanecem visíveis; manutenção adiciona contexto apenas.

## Riscos

| Risco | Mitigação |
|-------|-----------|
| Silenciar crítico indevido | lista não silenciável + duração máxima |
| Cross-tenant assignment | FK membership + validação |
| SLA estendido silenciosamente | reopen recalcula; update não altera deadlines |
| occurrence inflado por timer | `sync_batch_id` — incremento só por execução |

## Timezone

Usar `timezone.now()` aware em todo fluxo (padrão Django atual).
