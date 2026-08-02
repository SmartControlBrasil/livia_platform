# Fase 11 — Auditoria inicial

**Data:** 2026-08-01
**Branch:** `chore/postgresql-readiness`
**Base:** Fase 10 (Central de Saúde RAG/IA)

## Diagnósticos que podem gerar alertas

| Fonte | Rule IDs candidatos | Severidade típica |
|-------|---------------------|-------------------|
| `build_consolidated_readiness` | `environment_not_ready:*`, `database_not_ready` | critical |
| `inspect_environment_safety` | `provider_forbidden:*`, `integration_safety:*` | critical |
| `build_vector_health_summary` | `vector_incompatible` | warning/critical |
| `build_operations_summary` + ORM stale | `rag_operation_stale:{id}` | critical |
| `TenantRagOperationRequest` FAILED recente | `rag_operation_failed:{id}` | warning |
| `build_retrieval_metrics` | `retrieval_empty_elevated:{period}` | warning (amostra mínima) |
| `build_ai_usage_summary` | `openai_failures:{period}` | warning (volume mínimo) |
| tokens agregados | `token_usage_elevated:{period}` | warning informativo |

## Recomendações Fase 10 que **não** viram alerta automático

| Code | Motivo |
|------|--------|
| `operations_dry_run` | info — comportamento esperado |
| `operations_disabled` | info — gate intencional |
| `configuration_missing` | tenant novo / sem tráfego — evitar fadiga |
| ausência de eventos | SEM DADOS ≠ falha |

## Estados existentes reutilizados

- Severidade Fase 10: `info`, `warning`, `critical`
- Readiness: `READY`, `READY_WITH_WARNINGS`, `NOT_READY`
- Health overall: `SAUDÁVEL`, `ATENÇÃO`, `BLOQUEADO`, `SEM DADOS`

## Riscos de duplicação

- Não copiar thresholds em views/templates — centralizar em `alert_thresholds.py`
- Não criar regras paralelas às de `operational_diagnostics.py` — derivar candidatos do mesmo snapshot
- CLI e portal devem chamar `sync_operational_alerts()` único

## Modelo de persistência escolhido

`TenantOperationalAlert` em `knowledge_base` (tenant-scoped), com:

- `fingerprint` único por tenant (deduplicação)
- status `open` / `acknowledged` / `resolved`
- reopen automático quando condição reaparece após resolução
- auto-resolve quando condição desaparece

**Decisão de fingerprint:** `{rule_id}` para condições globais do tenant; `{rule_id}:{source_reference}` para recursos (operação RAG, check de ambiente).

**Decisão de reopen:** reutilizar o mesmo registro (incrementa `occurrence_count`, limpa resolução manual) — auditável e simples.

## Integração portal

| Peça existente | Uso Fase 11 |
|----------------|-------------|
| `knowledge_base_views._resolve_knowledge_base_access` | RBAC |
| `knowledge_base.view` | listagem/detalhe |
| `knowledge_base.operate` | sync, acknowledge, resolve |
| `record_audit_event` | trilha imutável |
| `_nav.html` | badge + link alertas |
| `health.html` | POST sincronizar |

## RBAC

Sem novas capabilities — reutilizar matriz existente:

- visualizar: `knowledge_base.view`
- sincronizar / reconhecer / resolver: `knowledge_base.operate`
