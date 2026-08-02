# Fase 10 — Auditoria inicial

**Data:** 2026-08-01
**Branch:** `chore/postgresql-readiness` @ `9fd5d38`

## Dados já disponíveis

| Fonte | Dados | Escopo |
|-------|-------|--------|
| `TenantRagConfiguration` | sync/index timestamps, retrieval flags, limites | tenant |
| `TenantRagOperationRequest` | fila, lease, heartbeat, tentativas | tenant |
| `RagRetrievalEvent` | status, hit, scores, latência, dry_run | tenant |
| `AiUsageEvent` | tokens, latência, operação, error_type | tenant |
| `inspect_tenant_embedding_health` | cobertura, incompatibilidades | tenant |
| `inspect_rag_operations_readiness` | gates operacionais, stale | global/tenant |
| `inspect_rag_vector_readiness` | pgvector, backend | global (infra) |
| `inspect_environment_safety` | gates ambiente + tenant | global/tenant |
| `build_dashboard_metrics` | readiness label, contagens | tenant |
| `build_operations_dashboard` | operações ativas/stale | tenant |

## Dados inexistentes ou parciais

| Métrica desejada | Situação |
|------------------|----------|
| Evidence sufficient/partial/insufficient persistido | Não há campo em `RagRetrievalEvent`; proxy via `hit` + `status` |
| Fallback rate no chat | Não persistido como evento dedicado |
| Custo financeiro OpenAI | `estimated_cost_usd` sempre null no CLI |
| Heartbeat por etapa no portal | Existe em `TenantRagOperationRequest.last_heartbeat_at` |

## Duplicação identificada

- `rag_operational_report` e `ai_usage_report` agregam ORM inline — candidatos a extração.
- `build_dashboard_metrics` e `build_operations_dashboard` sobrepõem config/sync parcialmente.
- `rag_vector_health` duplica formatação de `inspect_tenant_embedding_health`.

## Riscos cross-tenant

- Aggregates devem sempre filtrar `tenant=`.
- `build_database_validation_report` é global — exibir como infra, não misturar KPIs de outro tenant.
- Superuser global no portal usa tenant selecionado — manter padrão existente.

## Ponto de integração escolhido

```text
knowledge_base/rag/operational_metrics.py      ← agregações compartilhadas
knowledge_base/rag/operational_diagnostics.py ← recomendações + overall
operations_portal/rag_health_services.py       ← montagem do dashboard
operations_portal/knowledge_base_views.py      ← view tenant-scoped
/painel/base-de-conhecimento/saude/            ← rota nova
```

RBAC: reutilizar `knowledge_base.view` (VIEWER+ já acessa KB).

CLI refatorado para importar `operational_metrics` sem alterar contrato de saída.
