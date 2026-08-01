# Fase 17 — Relatório de Observabilidade

---

## 1. Sinais existentes (reutilizados)

| Sinal | Onde |
|---|---|
| retrieval hit/empty/failed/skipped | `RagRetrievalEvent` + `rag_retrieval_report` |
| vector health / reindex | `rag_vector_health`, `database_readiness` |
| grounded started/completed/failed | logs `assistant_core.services.grounded_response` |
| evidence partial/insufficient | logs `rag.evidence_partial`, `rag.evidence_insufficient` |
| OpenAI success/failure | logs `integrations.openai.client` |
| idempotency replay | logs `assistant_core.services.chat_idempotency` |
| audit operacional | app `audit` (`AuditEvent`) |
| chat requests | `chat_request_report` (JSON) |
| outbox | `outbox_report` |

---

## 2. Novo: `rag_operational_report`

Comando: `python manage.py rag_operational_report --tenant granimarmores-pitondo [--days N] [--json]`

Consolida:

- environment + feature flags
- tenant gates (RAG + grounded)
- embedding profile (ou erro se `fake` indevido)
- métricas `RagRetrievalEvent` (hits/empty/failed, dry_run split)
- readiness snapshot (`inspect_rag_vector_readiness`)

Exemplo (1 dia, GP):

```text
executed: 615
hits: 432 | empty: 182 | failed: 1
dry_run: 499 | active: 116
avg latency: 609.5 ms
avg max score: 0.469
Status vector: OK (19 compatible)
```

---

## 3. Gaps de observabilidade (não bloqueadores de Fase 17)

| Gap | Impacto | Fase posterior |
|---|---|---|
| `evidence_status` não persistido em DB | dashboards históricos limitados | migration opcional |
| tokens/custo OpenAI não registrados | sem chargeback por tenant | parse `usage` no client |
| portal `/painel/` sem visão RAG | operadores não veem grounded/partial | UI mínima futura |
| health HTTP `/health/` genérico | não inclui RAG readiness | endpoint dedicado opcional |

---

## 4. Portal operacional

Inspeção: `operations_portal/selectors.py` expõe status de integrações (OpenAI enabled/dry-run, CRM, webhooks, handoff).

**Não há** colunas RAG/grounded/evidence nas views de conversa.

Decisão Fase 17: **documentar para fase posterior** — redesign não autorizado nesta fase.

---

## 5. Proteção operacional embedding

Guard `fake` impede falso `REINDEX_REQUIRED` por poluição de shell (incidente Fase 16).

Health operacional deve sempre rodar com:

```text
LIVIA_RAG_EMBEDDING_PROVIDER=openai
```

---

## 6. Distinção de ambientes

| Tipo | O que foi executado |
|---|---|
| test automation | 508 testes SQLite + PG |
| simulation | OpenAI failure paths (unit tests) |
| staging-like | soak 32 interações via `/api/chat/` local |
| real staging | **não disponível nesta sessão** |

---

## 7. Recomendações operacionais contínuas

1. Rodar diariamente: `rag_vector_health --tenant granimarmores-pitondo`
2. Rodar semanalmente: `rag_operational_report --tenant granimarmores-pitondo --days 7`
3. Alertar se: `failed > 0`, `reindex_required > 0`, `embedding_error` presente
4. Nunca exportar `LIVIA_RAG_EMBEDDING_PROVIDER=fake` em shells de validação real
5. Garantir `SMART360_LEAD_DISPATCH_DRY_RUN=True` em staging

---

## 8. Veredito observabilidade

```text
Observabilidade mínima: DISPONÍVEL (CLI consolidado + eventos existentes)
Observabilidade avançada (portal/tokens): PENDENTE
```
