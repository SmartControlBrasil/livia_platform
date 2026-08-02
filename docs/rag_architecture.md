# Arquitetura RAG multi-tenant da Lívia

## Visão geral

O RAG da plataforma percorre um pipeline único por tenant:

```text
Google Drive
    ↓
manifest
    ↓
document / staging
    ↓
chunks
    ↓
embeddings
    ↓
pgvector (PostgreSQL) ou in-memory (SQLite)
    ↓
nearest-neighbor search
    ↓
threshold / dedupe / budget
    ↓
context_builder
    ↓
decision / AI
```

## Fluxo de ingestão

1. Configuração (`TenantRagConfiguration`)
2. Inventário read-only do Google Drive
3. Exportação de texto + staging
4. Chunking determinístico (`TenantRagDocumentChunk`)
5. Embeddings + índice (`TenantRagChunkEmbedding`)
6. Recuperação semântica no fluxo de conversa
7. Montagem de contexto via `build_knowledge_context`

## Backends de busca vetorial

Abstração: `knowledge_base.rag.vector_search`

| Backend | Quando | Comportamento |
|---|---|---|
| `postgres_pgvector` | PostgreSQL com extensão `vector` | `CosineDistance` no banco, top-N candidatos |
| `in_memory` | SQLite / fallback | cosseno em Python após filtro por tenant |

Seleção:

```text
LIVIA_RAG_VECTOR_BACKEND=auto|in_memory|postgres_pgvector
```

Default: `auto`.

### Semântica de score

- pgvector usa distância cosseno (`<=>`): **menor distância = melhor**
- score de aplicação: `score = 1 - distance` (**maior score = melhor**)
- threshold opera sobre **score**

### Campo vetorial

`RagVectorField`:

- SQLite: JSON (lista de floats)
- PostgreSQL: `vector(n)` via pgvector

Dimensão centralizada em `LIVIA_RAG_EMBEDDING_DIMENSION` (alias `LIVIA_RAG_EMBEDDING_DIMENSIONS`).

Profile operacional: `knowledge_base/rag/embedding_profile.py` (`EmbeddingProfile`, health, cobertura).
Evolução/migração de dimensão: `docs/rag_embedding_evolution.md`.

### Índice HNSW

Migration PostgreSQL cria:

```text
knowledge_base_rag_embedding_hnsw_cosine
USING hnsw (vector vector_cosine_ops)
```

- Métrica: cosine
- Operador: `<=>`
- Adequado para bases pequenas/médias e crescimento gradual
- IVFFlat ficaria para workloads com rebuilds controlados e estimativa de listas; HNSW foi escolhido por melhor qualidade default e menor ajuste operacional
- Índice passa a valer após a migration no PostgreSQL com extensão `vector`

Imagem local recomendada: `pgvector/pgvector:pg16` (`docker-compose.postgres.yml`).

## Query de retrieval (PostgreSQL)

Fluxo:

1. Filtrar `tenant`, ativo, status, provider, model, dimensão e assinatura de config
2. Anotar `CosineDistance("vector", query_vector)`
3. Ordenar por distância
4. Limitar candidatos (`LIVIA_RAG_VECTOR_CANDIDATE_LIMIT`, default 20)
5. Converter distância → score
6. Aplicar threshold / dedupe / max chunks / max chars

O tenant **sempre** restringe o conjunto candidato no ORM antes da ordenação vetorial.

Embeddings históricos de outro provider/model/dimensão/assinatura **não** entram na mesma comparação.

## Feature flags

| Camada | Flag | Default |
|---|---|---|
| Global | `LIVIA_RAG_ENABLED` | `False` |
| Global | `LIVIA_RAG_DRY_RUN` | `True` |
| Tenant | `retrieval_enabled` | `False` |

## Threshold e limites

| Setting | Default |
|---|---|
| `LIVIA_RAG_MIN_SIMILARITY_SCORE` | `0.25` (default global) |
| `TenantRagConfiguration.min_similarity_score` | `null` → usa global |
| `LIVIA_RAG_MAX_RETRIEVED_CHUNKS` | `5` |
| `LIVIA_RAG_MAX_CONTEXT_CHARS` | `3000` (aplica-se a `selected_chars`) |
| `LIVIA_RAG_MAX_CHUNKS_PER_MANIFEST` | `2` |
| `LIVIA_RAG_VECTOR_CANDIDATE_LIMIT` | `20` |

Precedência: override de eval/comando → threshold do tenant → default global.

Calibração: não mudar o global por um único tenant; preferir `min_similarity_score` por tenant.

## Observabilidade

Logs: `rag.retrieval.{started,completed,empty,skipped,failed}`

Métricas persistidas em `RagRetrievalEvent` (sem pergunta/documento/embedding/segredo):

- status, backend, provider/model
- duration_ms, candidate_count, result_count
- max_score, threshold, threshold_source, dry_run, hit

### Hit-rate

```text
hit = status=completed AND result_count > 0
hit_rate = hits / (completed + empty + failed)
```

`skipped` (disabled/dry-run/sem índice) **não** entra no denominador.

Comando:

```bash
python manage.py rag_retrieval_report --tenant granimarmores-pitondo --days 7
```

## Readiness

`python manage.py database_readiness` inclui inspeção readonly de:

- vendor
- extensão `vector` / versão
- tipo da coluna
- índice HNSW
- provider/model/dimensão
- backend ativo

Não altera o banco.

## Fallback seguro

Erro vetorial / ausência de pgvector em ambientes permitidos / skip de flags:

- não gera 500 exclusivo de RAG no chat
- preserva discovery/qualification/handoff
- usa knowledge textual determinística quando disponível

## Segurança multi-tenant

- tenant obrigatório
- filtro ORM por tenant antes da similaridade
- nunca global nearest neighbors + filtro posterior
- tenant inativo não consulta

## Ativação futura (staging)

1. Subir PostgreSQL com imagem `pgvector/pgvector`
2. `CREATE EXTENSION vector` via migration
3. migrar/reindexar embeddings
4. validar `database_readiness`
5. dry-run de retrieval report
6. habilitar `LIVIA_RAG_ENABLED=True`, `LIVIA_RAG_DRY_RUN=False`, `--enable-retrieval`
