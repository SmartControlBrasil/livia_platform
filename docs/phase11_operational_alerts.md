# Fase 11 — Alertas operacionais RAG/IA

## Modelo

`TenantOperationalAlert` (`knowledge_base`) — tenant-scoped, persistente.

| Campo | Descrição |
|-------|-----------|
| `fingerprint` | Chave de deduplicação única por tenant |
| `rule_id` | Identificador estável da regra |
| `status` | `open`, `acknowledged`, `resolved` |
| `severity` | `info`, `warning`, `critical` |
| `occurrence_count` / `last_seen_at` | Repetições da mesma condição |

## Fingerprint

Formato:

```text
{rule_id}
{rule_id}:{source_reference}
```

Exemplos:

```text
database_not_ready
rag_operation_stale:42
environment_not_ready:embedding_provider_fake
retrieval_empty_elevated:7d
```

**Decisão:** reabrir o mesmo registro quando condição reaparece após resolução (incrementa `occurrence_count`).

## Regras iniciais

| Rule ID | Categoria | Severidade | Gatilho |
|---------|-----------|------------|---------|
| `environment_not_ready` | environment | critical | check crítico de ambiente |
| `database_not_ready` | database | critical | migrations pendentes |
| `vector_incompatible` | vector_health | warning/critical | REINDEX_REQUIRED |
| `provider_forbidden` | openai_provider | critical | fake provider proibido |
| `integration_safety` | integration_safety | critical | dry-run staging desativado |
| `rag_operation_failed` | rag_operations | warning | operação FAILED recente |
| `rag_operation_stale` | rag_operations | critical | RUNNING com lease expirado |
| `openai_failures` | openai_provider | warning | falhas ≥ mínimo |
| `retrieval_empty_elevated` | retrieval | warning | empty rate ≥ limiar com amostra mínima |
| `token_usage_elevated` | token_usage | warning | tokens ≥ limiar informativo |

**Não gera alerta:** dry-run intencional, tenant sem tráfego, gates tenant esperados (`tenant_rag_gate`), amostras pequenas.

## Thresholds (settings)

| Setting | Default |
|---------|---------|
| `LIVIA_RAG_ALERT_RETRIEVAL_MIN_EXECUTED` | 10 |
| `LIVIA_RAG_ALERT_RETRIEVAL_EMPTY_RATE` | 0.8 |
| `LIVIA_RAG_ALERT_AI_FAILURE_MIN` | 3 |
| `LIVIA_RAG_ALERT_TOKEN_WARNING` | 50000 |
| `LIVIA_RAG_ALERT_OPERATION_FAILED_WINDOW_DAYS` | 7 |

## Rotas portal

| Rota | Capability |
|------|------------|
| `/painel/base-de-conhecimento/alertas/` | `knowledge_base.view` |
| `/painel/base-de-conhecimento/alertas/<id>/` | `knowledge_base.view` |
| POST reconhecer / resolver | `knowledge_base.operate` |
| POST `/saude/sincronizar/` | `knowledge_base.operate` |

## CLI

```bash
python manage.py sync_operational_alerts --tenant <slug>
python manage.py sync_operational_alerts --all-tenants --dry-run --json
```

## Auditoria

Actions: `operational_alert.created`, `.updated`, `.acknowledged`, `.resolved`, `.reopened`, `.sync`

## Runbooks

Matriz centralizada em `knowledge_base/rag/operational_alert_runbooks.py`.

## Limitações

- Sem notificações externas (email, Slack, etc.)
- Sem worker periódico — sync explícito (portal ou CLI)
- Resolução manual pode ser revertida no próximo sync se condição persistir
