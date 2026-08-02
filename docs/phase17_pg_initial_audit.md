# Fase 17 — Auditoria inicial PostgreSQL

**Branch:** `chore/postgresql-readiness`
**HEAD base:** `9fd5d38` — `fix: reforca concorrencia e readiness das operacoes RAG`
**Data:** 2026-07-29

## Snapshot do working tree (antes das correções desta fase)

| Métrica | Valor |
|---------|-------|
| Entradas `git status --short` | ~119 |
| Arquivos modificados (`M`) | ~30 |
| Arquivos untracked (`??`) | ~92 |

Arquivos modificados relevantes (RAG/dimensão):

- `knowledge_base/models.py`
- `knowledge_base/rag/embeddings.py`
- `knowledge_base/rag/indexing.py`
- `knowledge_base/rag/vector_field.py`
- `knowledge_base/test_rag_*.py`

Migrations operacionais untracked (Fases 11–16):

- `audit/migrations/0010` … `0015`
- `knowledge_base/migrations/0013` … `0017`

Scripts operacionais untracked:

- `scripts/run_postgresql_validation.sh`
- `scripts/phase7_pgvector_validation.py`

Testes PostgreSQL untracked:

- `operations_portal/test_operational_postgresql_concurrency.py`
- `operations_portal/test_operational_analytics_postgresql.py`
- `knowledge_base/test_rag_embedding_dimension_regression.py`

## Bloqueio raiz identificado

| Camada | Dimensão |
|--------|----------|
| Testes RAG (`@override_settings`) | 8 |
| Migration `0008` (PostgreSQL) | `vector(1536)` |
| Settings produtivos | 1536 (`text-embedding-3-small`) |

## Patches experimentais encontrados (revertidos nesta fase)

| Arquivo | Comportamento experimental |
|---------|---------------------------|
| `knowledge_base/models.py` — `TenantRagChunkEmbedding.save()` | Reescrevia `dimension = len(vector)` em `RUNNING_TESTS` + PostgreSQL |
| `knowledge_base/rag/embeddings.py` — `effective_embedding_dimension()` | Lia dimensão do schema PG e ignorava `config.dimension` |
| `knowledge_base/rag/indexing.py` — `_persist_embedding()` | Usava `effective_embedding_dimension()` em vez de `config.dimension` |
| `knowledge_base/rag/vector_field.py` — `_resolved_dimensions()` | Consultava `database_vector_column_dimension()` em qualquer PostgreSQL |

**Regra arquitetural aplicada:** incompatibilidade entre vetor, metadata `dimension`, schema `vector(N)` e profile deve **falhar explicitamente** — sem correção silenciosa no `save()`.

## Estratégia de testes adotada

- **SQLite / testes lógicos:** dimensão 8 permitida (`EmbeddingProfileTests`, validadores unitários).
- **PostgreSQL / persistência pgvector:** dimensão 1536 via `RagTestDimensionMixin` + helper `rag_test_embedding_dimension()`.
- **Helper:** `knowledge_base/testing/rag_dimensions.py` (somente camada de testes).

## makemigrations --check

`python manage.py makemigrations --check --dry-run` propõe `0018` com **renomeação automática de índices** da migration `0017_operational_notifications` — ruído de nomenclatura Django, **sem alteração de schema funcional**. Não foi criada migration `0018` nesta fase.
