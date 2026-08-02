# Fase 13 — Governança operacional de alertas

## Princípios

| Conceito | Significado |
|----------|-------------|
| **ACK** | Alguém assumiu conhecimento do alerta |
| **Silenciado** | Condição persiste; ruído operacional suprimido temporariamente |
| **Resolvido** | Condição ausente ou encerrada formalmente |
| **Manutenção** | Janela programada em que certas condições são esperadas |

**Abordagem A (adotada):** status persistido `open` / `acknowledged` / `resolved` + estados derivados (`is_silenced`, `is_under_maintenance`, `sla_state`).

Silenciamento **não** é resolução. Alertas permanecem no histórico. Regras críticas não silenciáveis continuam visíveis.

## Modelos

- `TenantOperationalAlert` — campos de atribuição (`assigned_to`, `assigned_by`, `assigned_at`), SLA (`ack_due_at`, `resolution_due_at`), `last_sync_batch_id`
- `TenantOperationalAlertSilence` — silenciamento temporário com expiração por `ends_at`
- `TenantOperationalMaintenanceWindow` — janela agendada/ativa/encerrada/cancelada com escopo

## Política central

Arquivo: `knowledge_base/rag/alert_governance.py`

- Regras não silenciáveis: `provider_forbidden`, `integration_safety`; categorias `tenant_isolation`, `integration_safety`
- SLA defaults: critical ACK 30 min / resolução 4 h; warning ACK 4 h / resolução 3 d; info sem SLA
- Pausa de SLA: apenas durante manutenção válida (não durante silenciamento manual)
- Presets de silenciamento: `1h`, `4h`, `24h`, `7d` (máximo configurável)

## Settings

```text
LIVIA_ALERT_CRITICAL_ACK_SLA_MINUTES
LIVIA_ALERT_CRITICAL_RESOLUTION_SLA_MINUTES
LIVIA_ALERT_WARNING_ACK_SLA_MINUTES
LIVIA_ALERT_WARNING_RESOLUTION_SLA_MINUTES
LIVIA_ALERT_SLA_DUE_SOON_MINUTES
LIVIA_ALERT_SILENCE_MAX_HOURS
```

## Serviços

- `alert_governance_services.py` — criar/cancelar manutenção, silenciar/dessilenciar, atribuir/desatribuir
- `operational_alert_sync.py` — deadlines na criação/reabertura; `sync_batch_id` evita inflar `occurrence_count` na mesma execução; ACK autoatribui se sem responsável

## Portal

```text
/painel/base-de-conhecimento/alertas/          — filtros SLA, responsável, silenciado, manutenção
/painel/base-de-conhecimento/alertas/<id>/     — governança, atribuição, silenciamento
/painel/base-de-conhecimento/manutencoes/      — listagem e cancelamento
/painel/base-de-conhecimento/manutencoes/nova/ — criação (configure)
```

Central de Saúde: scorecards de governança (silenciados, manutenção, sem responsável, SLA vencido).

## RBAC

| Capability | Permissão |
|------------|-----------|
| `knowledge_base.view` | Ver alertas, SLA, manutenções |
| `knowledge_base.operate` | ACK, resolver, atribuir, silenciar |
| `knowledge_base.configure` | Criar/cancelar manutenção ampla |

## Auditoria

`operational_alert.assigned`, `.unassigned`, `.silenced`, `.unsilenced`, `operational_maintenance.created`, `.cancelled`

## Escopos de manutenção

- `all` — todas as regras operacionais
- `categories` — lista em `scope_categories`
- `rules` — lista em `scope_rule_ids`
- `resource` — `scope_resource_reference` (= `source_reference` do alerta)

## Comportamento durante sync

Diagnóstico continua; alerta persiste/atualiza; auto-resolução funciona mesmo silenciado; reabertura recalcula deadlines; manutenção marca `is_under_maintenance` sem ocultar regras críticas.

Documentação complementar: `docs/phase13_initial_audit.md`, `docs/phase13_final_report.md`.
