# Fase 16 — Relatório de Reindex (GP)

Tenant: `granimarmores-pitondo`
Data: 2026-07-31
Banco: PostgreSQL `127.0.0.1:55432/livia_platform`

## 1. Estado inicial (Fase 15)

Na Fase 15, `rag_vector_health` reportou:

```text
Status: REINDEX_REQUIRED
wrong model: 19
retrieval → skipped / no_usable_index (com provider=fake no shell)
```

## 2. Auditoria do estado atual

| Item | Valor encontrado |
|---|---|
| Provider configurado | `openai` (default em `config/settings.py`; `.env` não define override) |
| Modelo esperado | `text-embedding-3-small` |
| Dimensão esperada | `1536` |
| Backend vetorial | pgvector (`LIVIA_RAG_VECTOR_BACKEND=pgvector`) |
| Tabela de embeddings | `TenantRagChunkEmbedding` |
| Chunks | `TenantRagDocumentChunk` |
| Profile key | `openai:text-embedding-3-small:1536` |
| Comando health | `manage.py rag_vector_health --tenant …` |
| Comando sync | `manage.py sync_tenant_rag --tenant …` |
| Comando index | `manage.py index_tenant_rag --tenant …` |
| Histórico | `TenantRagIndexRun` |
| Pasta GP aprovada | `1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm` |

Embeddings armazenados (amostra via health com `provider=openai`):

```text
compatible: 19
wrong model: 0
wrong dimension: 0
reindex_required: 0
inactive: 4
total: 23
indexable_chunks coverage: 100%
Status: OK
```

## 3. Causa raiz dos “19 embeddings incompatíveis”

**Não havia corrupção de índice.** O diagnóstico correto:

| Campo | Esperado | Armazenado (DB) |
|---|---|---|
| tenant | granimarmores-pitondo | granimarmores-pitondo |
| document/chunk count | 9 docs / 19 chunks ativos | confirmado |
| compatible embeddings | 19 | 19 (com `provider=openai`) |
| incompatible embeddings | 0 | 19 **somente** se `LIVIA_RAG_EMBEDDING_PROVIDER=fake` |
| missing embeddings | 0 | 0 |
| expected model | text-embedding-3-small | text-embedding-3-small |
| stored model | — | text-embedding-3-small |
| expected dimension | 1536 | 1536 |
| stored dimension | — | 1536 |

Reprodução controlada:

```bash
export DATABASE_URL='postgresql://livia_local:livia_local_password@127.0.0.1:55432/livia_platform?sslmode=disable'
export LIVIA_RAG_EMBEDDING_PROVIDER=fake
python manage.py rag_vector_health --tenant granimarmores-pitondo
# → Status: REINDEX_REQUIRED, wrong model: 19

export LIVIA_RAG_EMBEDDING_PROVIDER=openai
export LIVIA_RAG_EMBEDDING_DIMENSION=1536
python manage.py rag_vector_health --tenant granimarmores-pitondo
# → Status: OK, compatible: 19
```

**Conclusão:** poluição de ambiente de teste/shell (`LIVIA_RAG_EMBEDDING_PROVIDER=fake`) fez o health comparar embeddings reais OpenAI contra profile fake.

## 4. Corpus GP (pré-reindex)

Inventário lógico (sem alteração de dados):

```text
manifests_active: 9
chunks_active: 19
embeddings_total: 23 (19 ativos, 4 inativos — chunks substituídos)
cross_tenant_chunks: 0
```

Pastas GP (prefixo `GP —`):

- 01_EMPRESA … 09_CUIDADOS_E_DUVIDAS (2 chunks cada, exceto Materiais com 3)

Sem vazamento cross-tenant detectado.

## 5. Backup / snapshot lógico (pré-operação)

Registrado antes de qualquer escrita:

```text
documentos (manifests ativos): 9
chunks ativos: 19
embeddings: 23 total / 19 ativos
último TenantRagIndexRun: (histórico anterior à Fase 16)
modelo/dimensão: openai / text-embedding-3-small / 1536
```

Nenhum dump completo de banco foi necessário.

## 6. Reindex executado

Com índice já compatível, foi executado **dry-run oficial** (sem reescrita):

```bash
export DATABASE_URL='postgresql://livia_local:livia_local_password@127.0.0.1:55432/livia_platform?sslmode=disable'
export LIVIA_RAG_EMBEDDING_PROVIDER=openai
export LIVIA_RAG_EMBEDDING_DIMENSION=1536
python manage.py index_tenant_rag --tenant granimarmores-pitondo --dry-run
```

Resultado:

```text
mode=dry_run status=success
documents=9 chunks=19
pending=0 indexed=0 reindexed=0 unchanged=19
deactivated=0 skipped=0 failed=0 batches=0
```

**Reindex real não foi necessário** — embeddings já alinhados ao profile OpenAI atual.

## 7. Health pós-validação

```bash
export LIVIA_RAG_EMBEDDING_PROVIDER=openai
export LIVIA_RAG_EMBEDDING_DIMENSION=1536
python manage.py rag_vector_health --tenant granimarmores-pitondo
```

```text
Status: OK
compatible: 19
reindex_required: 0
coverage_compatible: 19
coverage_incompatible_embedding: 0
coverage_missing_embedding: 0
threshold_effective: 0.40
```

Os 4 embeddings `inactive` correspondem a chunks substituídos em indexações anteriores — comportamento esperado, não bloqueador.

## 8. Correções de código relacionadas

Durante a Fase 16 (sem reindex de dados):

1. `knowledge_base/rag/context_builder.py` — injeção RAG em dry-run respeita allowlist GP (não descarta por `observe_only` isolado).
2. `assistant_core/eval/evidence_sufficiency.py` — marcadores de timeline de orçamento vs execução.
3. `assistant_core/eval/faithfulness.py` — eco seguro de termos de prazo/execução na pergunta do usuário.
4. Testes isolados de `.env` local (`test_grounded_response.py`, `test_rag_conversation.py`).

## 9. Veredito reindex

```text
REINDEX_REQUIRED → resolvido por configuração correta (provider=openai)
Reindex físico → NÃO NECESSÁRIO (unchanged=19)
Índice GP → SAUDÁVEL
```
