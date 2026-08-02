# Fase 17 PostgreSQL — Relatório final

**Branch:** `chore/postgresql-readiness`
**Data:** 2026-07-29
**Commit base:** `9fd5d38`

---

## 1. Estado inicial

- Suíte SQLite verde (~699 testes).
- Suíte PostgreSQL **não verde** por divergência `LIVIA_RAG_EMBEDDING_DIMENSION=8` vs `vector(1536)`.
- Patches experimentais no working tree mascaravam incompatibilidade em `save()`, `indexing`, `embeddings` e `vector_field`.

## 2. Working tree

~119 entradas (30 modificados, ~92 untracked). Nenhum `git reset` / `git clean`. Snapshot em `docs/phase17_pg_initial_audit.md`.

## 3. Patches experimentais encontrados

Documentados em `phase17_pg_initial_audit.md`: auto-dimension no `save()`, `effective_embedding_dimension()`, schema introspection em `_resolved_dimensions()`.

## 4. Patches revertidos

| Arquivo | Reversão |
|---------|----------|
| `knowledge_base/models.py` | `save()` → apenas `full_clean()` + `super().save()` |
| `knowledge_base/rag/embeddings.py` | Removido `effective_embedding_dimension()`; `FakeEmbeddingProvider` usa `config.dimension` |
| `knowledge_base/rag/indexing.py` | `_persist_embedding()` valida/persiste `config.dimension` |
| `knowledge_base/rag/vector_field.py` | `_resolved_dimensions()` sem introspecção de schema |

## 5. Contrato de dimensão

```text
Produção / PostgreSQL: vector(1536), dimension=1536, text-embedding-3-small
Incompatibilidade → ValidationError / erro explícito (sem mascaramento)
```

## 6. Estratégia SQLite/PostgreSQL

- **A (lógico/SQLite):** dimensão 8 — `EmbeddingProfileTests`, validadores unitários.
- **B (persiste pgvector PG):** dimensão 1536 — mixin nos testes de conversa, indexação, pgvector, eval runner.
- **C (compartilhados):** `_make_config()` resolve dimensão via helper quando `dimension=None`.

## 7. Helpers de teste

`knowledge_base/testing/rag_dimensions.py`:

- `rag_test_embedding_dimension()` → 1536 (PG) / 8 (SQLite)
- `rag_test_zero_vector()`
- `RagTestDimensionMixin`

## 8. Migrations

Migrations do zero OK em `test_livia_platform`. `makemigrations --check` propõe `0018` com rename de índices de `0017` (ruído Django, sem schema novo).

## 9. Pgvector

Extensão instalada. Coluna `vector(1536)` validada. Backend `postgres_pgvector` operacional nos testes de retrieval e indexação.

## 10. Testes RAG

82 testes focados PG: OK. Correções pontuais:

- Asserts de dimensão dinâmica em `test_rag_indexing`
- `test_retrieve_respects_context_char_limit`: texto normalizado com `.strip()` (consistência query/indexação no pgvector)

## 11. Vector health

`test_rag_embedding_profile.py`: testes de profile/classificação OK; `RagEvalRunnerTests` migrado para mixin 1536 no PG.

## 12. Retrieval

Cosine distance, threshold, tenant filter, ordering e limit validados em `test_rag_pgvector` e `test_rag_conversation` no PostgreSQL.

## 13. Operações RAG concorrentes

Hardening das Fases 10–16 preservado (não revertido).

## 14. Alert sync concorrente

Savepoint + retry após `IntegrityError` — validado em `test_operational_postgresql_concurrency.py`.

## 15. Fila concorrente

Lock em duas fases (sem `FOR UPDATE` em outer join nullable) — OK.

## 16. Notification worker

`skip_locked` — uma notificação processada por worker — OK.

## 17. Monitoring advisory lock

Batch concorrente: um executa, outro skipped — OK.

## 18. Lease/stale

Recovery e invalidação de worker antigo — OK.

## 19. Percentis

Median, p75, p90, p95 — `test_operational_analytics_postgresql.py` — OK.

## 20. TruncDate/timezone

Agrupamento por dia local — OK.

## 21. Tenant isolation

Retrieval e indexação isolam tenant — OK.

## 22. Performance

Ver `docs/phase17_performance_validation.md` — auditoria mínima, sem novos índices.

## 23. Índices

Nenhum índice adicional criado nesta fase.

## 24. Script reproduzível

`scripts/run_postgresql_validation.sh` preservado. **Nota:** `makemigrations --check` falha por ruído de rename `0017` — tratar como aviso até alinhar nomes de índice na migration untracked.

## 25. Suíte PostgreSQL

```text
Ran 701 tests in ~164s
OK (skipped=3)
```

## 26. Suíte SQLite

```text
Ran 701 tests in ~140s
OK (skipped=23)
```

## 27. Skips restantes

**PostgreSQL (3):** dimensão fixa no schema; asserção sqlite-only; concorrência delegada.

**SQLite (23):** testes `@skipUnless(postgresql)` (concorrência, analytics PG, CRM retry, etc.).

## 28. Arquivos modificados (Fase 17 PG)

- `knowledge_base/models.py` (revert save)
- `knowledge_base/rag/embeddings.py`, `indexing.py`, `vector_field.py` (reverts)
- `knowledge_base/testing/rag_dimensions.py` (novo)
- `knowledge_base/test_rag_*.py` (mixin + regressão)
- `docs/phase17_*.md`

## 29. Arquivos untracked

Migrations operacionais, testes PG concorrência/analytics, scripts, docs Fases 10–16 — classificação completa no snapshot git.

## 30. Riscos restantes

1. **makemigrations --check / 0018 rename:** alinhar nomes de índice em `0017` antes de commit publicar migrations.
2. **Query/index text normalization:** retrieval usa `strip()` na query; chunks indexados com texto bruto podem ter score menor no pgvector (corrigido no teste; avaliar alinhamento produtivo futuro se necessário).
3. **Performance/EXPLAIN:** não auditado com volume representativo.

## 31. Veredito

```text
FASE 17 POSTGRESQL CONCLUÍDA — GO CONDICIONAL
```

**Condição:** alinhar ou suprimir ruído de `makemigrations --check` (rename de índices `0017`) antes de publicar migrations. Funcionalmente: dimensão 1536 preservada, patches revertidos, suítes PG e SQLite verdes, concorrência validada.

---

Sem commit. Sem push.
