# Evolução de embeddings RAG

## Fonte única de verdade

Configuração efetiva:

```text
settings → load_embedding_config() → EmbeddingProfile
```

Campos:

```text
LIVIA_RAG_EMBEDDING_PROVIDER
LIVIA_RAG_EMBEDDING_MODEL
LIVIA_RAG_EMBEDDING_DIMENSION   (alias LIVIA_RAG_EMBEDDING_DIMENSIONS)
LIVIA_RAG_EMBEDDING_BATCH_SIZE
```

Assinatura determinística (SHA-256 de provider/model/dimension/batch_size) usada em:

- `TenantRagChunkEmbedding.embedding_config_signature`
- indexing / retrieval / readiness / health

Chave legível:

```text
provider:model:dimension
```

Exemplo: `openai:text-embedding-3-small:1536`

## PostgreSQL `vector(n)`

A coluna é tipada no schema (`migration 0008`). Alterar apenas:

```text
LIVIA_RAG_EMBEDDING_DIMENSION=3072
```

**não** altera `vector(1536)` e deve falhar via `ensure_profile_schema_compatible()`.

`ALTER COLUMN vector TYPE vector(3072)` **não** converte semanticamente vetores 1536 → 3072; exige **novo embedding** por chunk.

## Estratégia recomendada para mudança de dimensão

1. Adicionar nova coluna ou tabela de embeddings com `vector(n_new)` (ou profile paralelo).
2. Manter embeddings antigos até cobertura validada.
3. Reindexar com provider/model/dimension novos.
4. Validar cobertura (`embedding_coverage_ratio`).
5. Switch de retrieval para o profile novo.
6. Remover coluna/profile antigo em fase posterior.

## Coexistência e rollback de profile

Retrieval filtra por `provider`, `model`, `dimension` e `embedding_config_signature`. Profiles distintos **não** se misturam no ranking.

Rollback operacional: manter embeddings do profile anterior ativos e apontar settings de volta — sem apagar índice até estabilizar o novo.

## Estados operacionais (calculados)

```text
CURRENT          — compatível com profile ativo
STALE            — provider/model/dimension OK, signature diferente (ex.: batch_size mudou)
REINDEX_REQUIRED — provider/model/dimension/signature incompatível
INVALID          — null, inativo ou dimensão de vetor inválida
```

## Comandos

Ver também [rag_staging_runbook.md](./rag_staging_runbook.md).

```bash
python manage.py rag_vector_health --tenant <slug>
python manage.py index_tenant_rag --tenant <slug> --only-stale --limit 50
python manage.py rag_eval --tenant <slug> --dataset knowledge_base/rag/eval/datasets/granimarmores_staging.json
```
