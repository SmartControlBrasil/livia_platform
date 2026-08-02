# Fase 17 — Validação PostgreSQL

## Ambiente local (confirmado)

| Parâmetro | Valor |
|-----------|-------|
| Compose | `docker-compose.postgres.yml` |
| Host | `127.0.0.1` |
| Porta | `55432` |
| Database | `livia_platform` |
| User | `livia_local` |
| Engine | PostgreSQL 16 + pgvector |
| Test DB | `test_livia_platform` |

Proteção: `config/database.py` bloqueia `DATABASE_URL` externa em testes.

## Migrations do zero

Banco `test_livia_platform` recriado (drop + migrate completo). Aplicadas com sucesso:

- Extensão `vector`
- `knowledge_base.0008` — coluna `vector(1536)`
- Migrations operacionais `0013`–`0017` (knowledge_base) e `0010`–`0015` (audit)

## Contrato de dimensão preservado

- `LIVIA_RAG_EMBEDDING_DIMENSION` default produtivo: **1536**
- Migration `0008`: **vector(1536)** — não alterada
- Patches experimentais revertidos (ver `phase17_pg_initial_audit.md`)

## Testes RAG focados (PostgreSQL)

```bash
DATABASE_URL='postgresql://livia_local:…@127.0.0.1:55432/livia_platform?sslmode=disable' \
.venv/bin/python manage.py test \
  knowledge_base.test_rag_embedding_dimension_regression \
  knowledge_base.test_rag_conversation \
  knowledge_base.test_rag_indexing \
  knowledge_base.test_rag_pgvector \
  --verbosity=2
```

**Resultado:** 82 testes, OK (2 skipped esperados: asserções sqlite-only).

## Regressão de dimensão

`knowledge_base/test_rag_embedding_dimension_regression.py`:

- PG + `dimension=8` + vetor 1536 → `ValidationError` (sem correção silenciosa)
- PG + `dimension=1536` + vetor 1536 → sucesso

## Suíte PostgreSQL completa

```text
Ran 701 tests in ~164s
OK (skipped=3)
```

Skips PG (esperados):

1. `test_dimension_change_forces_reindex` — dimensão fixa no schema pgvector
2. `test_forced_postgres_backend_fails_on_sqlite` — asserção sqlite-only
3. `test_concurrent_sync_does_not_duplicate` — delegado a `OperationalPostgresConcurrencyTests`

## makemigrations --check

Propõe `0018` apenas com rename de índices de `0017` — **ruído**, não bloqueio funcional. Ver relatório final para veredito.
