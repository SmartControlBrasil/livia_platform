# Fase 5 — Integração segura da recuperação vetorial ao chat

**Branch:** `chore/postgresql-readiness`
**Commit base da auditoria:** `1c4afab`
**Data:** 2026-07-31
**Escopo:** integração RAG ↔ chat público (sem commit, push, deploy ou migrate em VPS)

---

## Veredito

**FASE 5 CONCLUÍDA — GO**

A integração vetorial ao chat já estava implementada (fases 12–18). Nesta fase foram validados os requisitos, documentada a arquitetura existente e adicionados apenas os campos per-tenant faltantes (`max_retrieved_chunks`, `max_context_chars`, `retrieval_timeout_seconds`) com migration reversível e testes complementares.

**GO não autoriza:** habilitar RAG em tenant real, piloto, staging físico ou alteração em produção.

---

## 1. Diagnóstico do fluxo anterior

### Estado Git (pré-implementação desta sessão)

| Item | Valor |
|------|-------|
| Branch | `chore/postgresql-readiness` |
| HEAD | `1c4afab` — feat: prepara deploy físico controlado de staging |
| Working tree | limpo |

### Endpoint público

- **`POST /api/chat/`** — `assistant_core/views.py` → `chat_api`
- Resolve tenant por slug (`tenant` ou `tenant_id`), valida origin (`tenants.origins.validate_tenant_origin`), rate limit, spam guard, idempotência via `request_id`
- Delega processamento a `process_chat_request` em `assistant_core/services/chat_processing.py`

### Serviço central de processamento

```
mensagem
  → analyze_message (preview discovery, fora da transação)
  → build_knowledge_context (RAG opcional, fora da transação)
  → transaction.atomic:
       decisão determinística (use_ai=False no profile)
       persistência user/assistant messages
       complete_chat_request
  → pós-commit: GroundedResponseService ou rewrite legado
  → resposta JSON 200 (falhas RAG/IA não viram HTTP 500)
```

### Decisão determinística

- **`assistant_core/services/livia_decision.py`** → `LiviaDecisionService.generate_reply`
- Produz intent, reply, handoff, qualificação, LeadDraft — **soberana sobre o RAG**
- RAG entra apenas como `knowledge_context` (string); não altera máquina de estados

### Síntese grounded

- **`assistant_core/services/grounded_response.py`** → `GroundedResponseService`
- **`assistant_core/prompts/grounded_ai.py`** — regras anti-injection, subordinação à decisão
- **`assistant_core/services/decision_outcome.py`** — `resolve_decision_outcome`
- **`assistant_core/eval/evidence_sufficiency.py`** — `SUFFICIENT` / `PARTIAL` / `INSUFFICIENT`

### Knowledge context

- **`knowledge_base/rag/context_builder.py`** → `build_knowledge_context`
- Preferência: semântico (`retrieve_context`) → fallback keyword (`retrieve_relevant_knowledge`)
- Formato: bloco `[KNOWLEDGE_BASE]…[/KNOWLEDGE_BASE]` com aviso de conteúdo não confiável

### Tenant e origin

- Tenant: slug no payload + header `X-Livia-Tenant` (deve coincidir)
- Origin: validação CORS/allowlist por tenant profile
- RAG: filtro explícito `tenant=tenant` em todas as queries de embedding/chunk

### Idempotência e replay

- **`assistant_core/services/chat_idempotency.py`**
- `request_id` UUID reservado antes do processamento
- Replay retorna payload persistido com `X-Livia-Idempotent-Replay: true`
- RAG não reexecuta em replay quando mockado no teste (`test_idempotent_replay_does_not_retrieve_again`)

### Falhas pós-commit

- Síntese grounded falha → log + retorno da resposta determinística já persistida
- RAG falha → contexto vazio → fluxo normal sem erro HTTP

### Auditoria e telemetria

- **`knowledge_base/rag/metrics.py`** → `RagRetrievalEvent` (status, reason, scores, duração — sanitizado)
- **`assistant_core/services/ai_telemetry.py`** → `AiUsageEvent` apenas para embedding real e síntese grounded
- Admin RAG: `knowledge_base/admin.py` → `TenantRagConfigurationAdmin` com audit trail

### Feature flags

| Flag | Default | Efeito |
|------|---------|--------|
| `LIVIA_RAG_ENABLED` | `False` | Gate global de retrieval |
| `LIVIA_RAG_DRY_RUN` | `True` | Observa retrieval sem injetar contexto |
| `LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST` | `""` | Allowlist para injeção em dry-run |
| `TenantRagConfiguration.retrieval_enabled` | `False` | Gate por tenant |
| `LIVIA_AI_ENABLED` / grounded gates | variável | Síntese pós-commit |

---

## 2. Ponto exato de integração

| Etapa | Arquivo | Função |
|-------|---------|--------|
| Pré-transação | `chat_processing.py` | `build_knowledge_context(...)` linha ~40 |
| Decisão | `chat_processing.py` | `decision_service.generate_reply(..., knowledge_context=...)` |
| Pós-commit | `chat_processing.py` | `_refine_response_with_ai_if_enabled` → `GroundedResponseService.generate` |
| Retrieval | `conversation_retrieval.py` | `retrieve_context` |
| Montagem contexto | `context_builder.py` | `_build_semantic_context` |

---

## 3. Arquivos criados e alterados (esta sessão)

### Criados

- `knowledge_base/migrations/0010_tenantragconfiguration_chat_retrieval_limits.py`
- `docs/phase5_rag_chat_integration_report.md`

### Alterados

- `knowledge_base/models.py` — campos per-tenant de limites
- `knowledge_base/rag/conversation_retrieval.py` — `_resolve_effective_limits`, `_apply_tenant_retrieval_timeout`
- `knowledge_base/admin.py` — exposição e auditoria dos novos campos
- `knowledge_base/test_rag_conversation.py` — testes de limites per-tenant, tenant sem config, prompt injection grounded

### Arquitetura pré-existente (não duplicada)

- `knowledge_base/rag/conversation_retrieval.py` — serviço de recuperação para chat
- `knowledge_base/rag/vector_search.py` — busca vetorial multi-tenant
- `knowledge_base/rag/context_builder.py` — montagem do contexto
- `assistant_core/services/chat_processing.py` — orquestração
- `assistant_core/services/grounded_response.py` — síntese subordinada
- Modelos: `TenantRagDocumentChunk`, `TenantRagChunkEmbedding`, `RagRetrievalEvent`

---

## 4. Migration

**`0010_tenantragconfiguration_chat_retrieval_limits`**

Campos nullable (sem efeito em tenants existentes):

- `max_retrieved_chunks`
- `max_context_chars`
- `retrieval_timeout_seconds`

Reversível via `migrate knowledge_base 0009`.

---

## 5. Configuração e defaults

### Por tenant (`TenantRagConfiguration`)

| Campo | Default | Comportamento |
|-------|---------|---------------|
| `retrieval_enabled` | `False` | RAG desligado |
| `min_similarity_score` | `null` | Usa global |
| `max_retrieved_chunks` | `null` | Usa global; se setado, `min(global, tenant)` |
| `max_context_chars` | `null` | Usa global; se setado, `min(global, tenant)` |
| `retrieval_timeout_seconds` | `null` | Usa global; se setado, `min(global, tenant)` |

### Globais (`config/settings.py`)

- `LIVIA_RAG_MIN_SIMILARITY_SCORE` = 0.25
- `LIVIA_RAG_MAX_RETRIEVED_CHUNKS` = 5
- `LIVIA_RAG_MAX_CONTEXT_CHARS` = 3000
- `LIVIA_RAG_MAX_CHUNKS_PER_MANIFEST` = 2
- `LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS` = 30

Nenhuma flag global habilita silenciosamente todos os tenants.

---

## 6. Arquitetura da recuperação

```
build_knowledge_context
  → retrieve_context(tenant, query)
      → _can_attempt_retrieval (tenant ativo, config, retrieval_enabled, LIVIA_RAG_ENABLED)
      → _resolve_effective_limits / _resolve_effective_threshold
      → build_retrieval_query (somente mensagem atual)
      → embed query (timeout controlado)
      → vector search (filtro tenant_id)
      → _dedupe_and_limit (score, chunks, chars, diversidade por manifest)
      → RagRetrievalResult tipado
      → record_retrieval_event
  → format_knowledge_base_block (se completed e não dry-run blocked)
```

Taxonomia de status:

| Status retrieval | Mapeamento spec |
|------------------|-----------------|
| `skipped` + `global_disabled` / `tenant_retrieval_disabled` / `configuration_missing` | **DISABLED** |
| `failed` + provider/backend/timeout | **UNAVAILABLE** |
| `empty` + `below_threshold_or_empty` | **INSUFFICIENT** (evidência) |
| `completed` | base para **SUFFICIENT** / **PARTIAL** na camada grounded |

---

## 7. Regras de score, limites e suficiência

- Score abaixo do threshold → chunk descartado (não entra como “contexto fraco”)
- Chunks inativos / embedding incompatível com profile → excluídos na query
- Deduplicação por `chunk_id` e `chunk_sha256`
- Diversidade: `LIVIA_RAG_MAX_CHUNKS_PER_MANIFEST`
- Ordem determinística por score decrescente
- Orçamento de chars aplicado antes da síntese (`_dedupe_and_limit` + truncate com `...`)
- Sufficiency final: `assess_evidence_sufficiency` no `GroundedResponseService`

---

## 8. Fallback por cenário

| Cenário | Comportamento |
|---------|---------------|
| RAG desativado (global/tenant) | `status=skipped`, contexto vazio, chat normal |
| Tenant sem config | `configuration_missing`, skipped |
| Índice vazio / sem embedding | `no_usable_index` ou `empty` |
| Provider/embedding falha | `status=failed`, contexto vazio, HTTP 200 |
| Score insuficiente | `empty`, síntese usa modo sem KB ou parcial |
| Síntese falha | Resposta determinística persistida |
| Replay idempotente | Payload cacheado, sem reprocessamento |
| Dry-run | Observa métricas, não injeta `[KNOWLEDGE_BASE]` |

---

## 9. Isolamento multi-tenant

- Todas as queries ORM e vector search filtram `tenant_id`
- Testes: `test_multi_tenant_isolation`, `test_retrieve_requires_tenant`
- Configuração OneToOne por tenant; tenant sem config não recupera

---

## 10. Proteção contra prompt injection

Camadas (defesa em profundidade, não só prompt):

1. RAG retorna **dados** em bloco delimitado marcado como não confiável
2. Decisão determinística **não lê** instruções do KB para qualificar/criar lead
3. Prompt grounded declara ignorar injection documental
4. Conteúdo malicioso permanece no bloco USER, nunca no SYSTEM
5. Testes: `test_qualification_remains_sovereign_against_malicious_doc`, `test_prompt_keeps_knowledge_as_untrusted_data`, `test_grounded_prompt_treats_malicious_chunk_as_untrusted_data`

---

## 11. Auditoria e telemetria

**Registra (sanitizado):** tenant_id, conversation_id, status, reason, candidate_count, result_count, max_score, threshold, duration_ms, provider/model, chunk_ids internos em logs de sufficiency.

**Não registra:** API keys, vetores completos, prompt integral, conteúdo integral de chunks, PII em texto aberto.

**AiUsageEvent:** apenas embedding de query e síntese grounded bem-sucedida — não conta RAG disabled, cache ou fallback local.

---

## 12. Cobertura dos 25 testes obrigatórios

| # | Requisito | Teste existente |
|---|-----------|-----------------|
| 1 | RAG desligado por padrão | `test_no_openai_call_when_disabled`, defaults model |
| 2 | Tenant sem config | `test_tenant_without_configuration_skips_retrieval` (**novo**) |
| 3 | RAG habilitado com evidência | `test_retrieve_relevant_chunks_with_limit_and_threshold` |
| 4 | Score abaixo do mínimo | `test_retrieve_*` + empty status |
| 5 | Limite max chunks | `test_tenant_max_retrieved_chunks_override_tightens_global_limit` (**novo**) |
| 6 | Orçamento de contexto | `test_retrieve_respects_context_char_limit` |
| 7 | Índice vazio | embedding profile / no_usable_index paths |
| 8 | Falha embedding query | `test_provider_failure_falls_back_empty` |
| 9 | Timeout/indisponível | grounded timeout test, vector_backend failure |
| 10 | Falha banco/busca | pgvector tests com mocks |
| 11 | Fallback sem HTTP 500 | `test_chat_keeps_flow_when_rag_errors` |
| 12 | Isolamento 2 tenants | `test_multi_tenant_isolation` |
| 13 | Chunk outro tenant | isolamento + ORM filters |
| 14 | Decisão não alterada | `test_decision_service_does_not_change_state_from_ai` |
| 15 | Lead/handoff não alterado | `test_qualification_remains_sovereign_against_malicious_doc` |
| 16 | Replay idempotente | `test_idempotent_replay_does_not_retrieve_again` |
| 17 | Auditoria sanitizada | indexing/inventory audit tests |
| 18 | Sem segredo em logs | `test_rag_indexing` audit blob tests |
| 19 | Prompt injection em chunk | `test_grounded_prompt_treats_malicious_chunk_as_untrusted_data` (**novo**) |
| 20 | Flag off = comportamento anterior | `test_chat_keeps_flow_when_rag_disabled` |
| 21 | Ordenação determinística | score sort em `_dedupe_and_limit` |
| 22 | Métricas IA só chamadas reais | telemetry gated em retrieval/grounded |
| 23 | Migrations consistentes | `makemigrations --check` OK |
| 24 | SQLite nos testes | suíte default SQLite |
| 25 | pgvector sem serviço externo | `test_rag_pgvector` com backend in-memory/fake |

---

## 13. Testes executados e resultados

```text
python manage.py check                          → OK
python manage.py makemigrations --check --dry-run → No changes detected
python manage.py test (módulos focados)         → 63 tests OK (1 skipped)
python manage.py test (suíte completa)            → 532 tests OK (11 skipped)
git diff --check                                → OK
```

Busca por segredos nos arquivos alterados: **nenhum valor sensível encontrado** (apenas referências a settings/test doubles).

---

## 14. Riscos e pendências

| Risco | Mitigação |
|-------|-----------|
| Habilitação acidental em prod | Defaults fail-closed; dry-run default True |
| Migration 0010 não aplicada em staging/prod | Aplicar apenas em janela controlada; campos nullable |
| Score semântico ≠ equivalência factual | evidence_sufficiency + prompts grounded |
| pgvector real não exercitado na suíte CI | testes com fake/in-memory; validação física separada (fase 17+) |

**Pendências fora do escopo Fase 5:**

- Habilitar `retrieval_enabled=True` em tenant piloto (GP)
- Deploy staging físico (Fase 19/20)
- Reindexação de corpus real

---

## 15. Git status --short (pós-implementação)

```text
 M knowledge_base/admin.py
 M knowledge_base/models.py
 M knowledge_base/rag/conversation_retrieval.py
 M knowledge_base/test_rag_conversation.py
?? knowledge_base/migrations/0010_tenantragconfiguration_chat_retrieval_limits.py
?? docs/phase5_rag_chat_integration_report.md
```

**Sem commit** (conforme instrução).
