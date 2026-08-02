# Integracao RAG Multi-tenant com Google Drive (Fases 1-5)

## Limites desta fase

- Somente leitura na Google Drive API.
- Escopo unico: `https://www.googleapis.com/auth/drive.readonly`.
- `--inventory-only` nao exporta conteudo.
- `--export-text` exporta apenas Google Docs em `text/plain`.
- `--build-chunks` nao acessa Google Drive; usa somente staging local.
- Fase 4 cria embeddings locais versionados por tenant.
- Fase 5 conecta a recuperacao semantica ao fluxo real de `/api/chat/` via `build_knowledge_context`.
- Nao cria Vector Store remoto da OpenAI.
- Nao substitui discovery, qualification, handoff ou maquina de estados.
- Retriever textual antigo (`KnowledgeDocument`) permanece como fallback.

## Variavel de ambiente

- `LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE`: caminho absoluto para o JSON da service account.
- O arquivo deve ficar fora do repositorio.

## Configuracao por tenant

Comando:

```bash
python manage.py configure_tenant_rag \
  --tenant granimarmores-pitondo \
  --approved-folder-id 1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm \
  --enable-sync
```

Regras:

- `--tenant` usa o slug exato.
- `--approved-folder-id` aceita somente ID puro (URL e valores invalidos sao rejeitados).
- Comando idempotente.
- Credenciais nao sao persistidas no banco.

## Modos operacionais

### Inventario somente leitura

Comando:

```bash
python manage.py sync_tenant_rag \
  --tenant granimarmores-pitondo \
  --inventory-only
```

Regras:

- Inventario recursivo com paginacao.
- Compatibilidade com Shared Drives (`supportsAllDrives/includeItemsFromAllDrives`).
- Atalhos sao bloqueados por fail-closed e nao sao seguidos.
- Saida deterministica por nome/id.

### Inventario + exportacao de texto (fase 2)

Comando:

```bash
python manage.py sync_tenant_rag \
  --tenant granimarmores-pitondo \
  --export-text
```

Regras:

- Exige exatamente um modo (`--inventory-only` xor `--export-text` xor `--build-chunks`).
- Exporta somente MIME `application/vnd.google-apps.document`.
- Outros tipos sao inventariados e marcados como `skipped_unsupported`.
- Atalhos nunca sao seguidos.
- Nao aceita file ID arbitrario por CLI.
- Nao registra texto do documento no terminal ou logs.

### Chunking deterministico local (fase 3)

Comando:

```bash
python manage.py sync_tenant_rag \
  --tenant granimarmores-pitondo \
  --build-chunks
```

Regras:

- Nao chama Google Drive nesse modo.
- Nao chama OpenAI.
- Trabalha apenas no staging do tenant informado.

### Indexacao de embeddings (fase 4)

Comando dedicado (superficie operacional separada das fases 1-3):

```bash
python manage.py index_tenant_rag \
  --tenant granimarmores-pitondo
```

Dry-run seguro (nao chama provedor, nao grava embeddings):

```bash
python manage.py index_tenant_rag \
  --tenant granimarmores-pitondo \
  --dry-run
```

Regras:

- Exige `--tenant` explicito.
- Nao aceita file ID, pasta alternativa ou acesso global.
- Nao acessa Google Drive.
- Processa apenas chunks ativos do tenant.
- Falha de forma segura se `LIVIA_RAG_INDEXING_ENABLED` nao estiver `True` (exceto `--dry-run`).
- Nao ativa o retriever publico.

## Arquitetura de embeddings (fase 4)

### Solucao escolhida

- Embeddings persistidos localmente em `TenantRagChunkEmbedding.vector` (`JSONField`).
- Cada registro e obrigatoriamente vinculado a `tenant` + `chunk` + assinatura da configuracao.
- Busca administrativa usa cosseno em memoria **somente apos** filtrar candidatos pelo tenant.
- Compativel com SQLite (desenvolvimento/testes) e PostgreSQL (persistencia).

### Justificativa

- O projeto nao possui `pgvector` nas dependencias nem extensao preparada.
- Nao introduzir Vector Store remoto da OpenAI.
- Evitar infraestrutura paralela: reutiliza Django ORM + isolamento multi-tenant existente.
- SQLite nao oferece busca vetorial de producao; o fallback em memoria e apenas administrativo/teste.

### Preparacao futura para PostgreSQL/pgvector

Quando a escala exigir:

1. Adicionar dependencia/extensao `pgvector` no PostgreSQL de producao.
2. Migrar `vector` para coluna tipada (`vector(n)`) com indice HNSW/IVFFlat.
3. Manter `tenant_id` obrigatorio no predicado da consulta (nunca filtrar so depois).
4. Preservar assinatura de configuracao e versionamento atuais.

Ate la, a camada atual permanece a fonte de verdade operacional.

## Configuracao do provedor de embeddings

Variaveis (chave nunca vai para o banco):

| Variavel | Default | Notas |
|---|---|---|
| `LIVIA_RAG_INDEXING_ENABLED` | `False` | Gate explicito da indexacao real |
| `LIVIA_RAG_EMBEDDING_PROVIDER` | `openai` | `openai` ou `fake` |
| `LIVIA_RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | Independente do modelo de chat |
| `LIVIA_RAG_EMBEDDING_DIMENSION` | `1536` | Validada contra a resposta |
| `LIVIA_RAG_EMBEDDING_BATCH_SIZE` | `32` | Lotes por tenant |
| `LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS` | `30` | Timeout HTTP |
| `LIVIA_RAG_EMBEDDING_MAX_RETRIES` | `3` | Tentativas adicionais |
| `LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS` | `1.0` | Backoff exponencial |
| `LIVIA_RAG_EMBEDDING_API_KEY` | vazio | Separada de `LIVIA_OPENAI_API_KEY` |
| `LIVIA_RAG_INDEX_RUNNING_TIMEOUT_SECONDS` | `1800` | Recuperacao de lock abandonado |
| `LIVIA_RAG_ADMIN_SEARCH_MAX_RESULTS` | `20` | Teto da busca administrativa |

Assinatura da configuracao (`embedding_config_signature`) considera provider, modelo, dimensao e batch size.
Mudanca nesses valores exige reindexacao controlada.

Para desabilitar indexacao:

```bash
# .env
LIVIA_RAG_INDEXING_ENABLED=False
```

## Persistencia

### `TenantRagChunkEmbedding`

- tenant, chunk, manifesto de origem
- hashes/assinaturas de chunk e embedding
- provider, modelo, dimensao, vetor
- status (`active`/`replaced`/`failed`), `is_active`
- `first_indexed_at`, `last_indexed_at`, `last_error`
- unicidade: `tenant + chunk + embedding_config_signature`
- versao antiga e desativada, nao apagada automaticamente
- `clean()` rejeita relacoes cross-tenant

### `TenantRagIndexRun`

Registro operacional por execucao: run_id, modo, provider/modelo, status, contadores, dry_run, erro seguro.

## Processamento incremental

- chunk novo sem embedding -> indexar
- inalterado + mesma configuracao -> unchanged
- chunk alterado -> novo embedding
- mudanca de provider/modelo/dimensao/config -> reindexar
- chunk inativo -> desativar embedding
- chunk restaurado -> reativar versao compativel ou reindexar
- falha de lote preserva versao anterior valida
- continua outros lotes quando seguro
- lock por tenant (tenants diferentes nao se bloqueiam)
- runs `running` abandonados recuperaveis por timeout
- chamadas externas fora de transacao longa

Resumo deterministico:

- `documents`, `chunks`, `pending`, `indexed`, `reindexed`, `unchanged`, `deactivated`, `skipped`, `failed`, `batches`, `status`

Status global: `running`, `success`, `partial`, `failed`.

Codigo de saida diferente de zero para `partial`/`failed`.

## Busca administrativa isolada

Servico: `knowledge_base.rag.admin_search.admin_vector_search`.

- exige tenant
- consulta somente embeddings ativos do tenant
- retorna IDs, scores e metadados seguros
- limite maximo configuravel
- empates ordenados por `(-score, chunk_id, embedding_id)`
- **nao** ligado a `/api/chat/`
- **nao** chamado pelo retriever publico
- **nao** exposto por endpoint publico

## Manifesto persistente e incremental

Cada arquivo inventariado gera/atualiza manifesto por tenant + file_id:

- metadados de inventario (nome, mime, caminho relativo, modified time, tamanho);
- status operacional;
- hash SHA-256 do texto normalizado (quando exportado);
- datas de descoberta, ultima observacao e ultima exportacao;
- erros sanitizados.

Politica incremental:

- novo arquivo -> `discovered` e exportacao (quando `--export-text`);
- alterado -> reexporta;
- inalterado -> `unchanged`, sem nova exportacao;
- removido -> marcado logicamente como `removed` (sem delete fisico);
- restaurado -> reativado quando volta a aparecer.

Importante:

- remocao logica so ocorre apos varredura completa sem falha individual;
- varredura parcial/erro nao marca removidos.

## Staging versus chunks

- `TenantRagDriveTextStaging`: texto normalizado exportado do Google Docs por tenant/documento.
- `TenantRagDocumentChunk`: fragmentos deterministicos, versionados por:
  - hash do texto de origem;
  - assinatura da configuracao de chunking;
  - ordinal.

Sem compartilhamento entre tenants, mesmo para textos iguais.

## Configuracoes de chunking (fase 3)

- `LIVIA_RAG_CHUNK_SIZE_CHARS` (default `1200`)
- `LIVIA_RAG_CHUNK_OVERLAP_CHARS` (default `120`)
- `LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT` (default `400`)

Validacoes fail-closed:

- tamanho > 0
- overlap >= 0 e menor que tamanho
- max_chunks > 0

Valores invalidos interrompem a execucao com erro claro.

## Algoritmo de chunking

- Preferencia por limites naturais:
  1) paragrafos
  2) fronteiras de sentenca/pontuacao
  3) corte rigido apenas quando necessario
- Ordem original preservada.
- Overlap controlado para contexto.
- Sem chunks vazios.
- Deterministico para mesma entrada + mesma configuracao.
- Preserva Unicode/acento.

## Incremental de chunks

- staging novo sem chunks -> cria.
- staging inalterado + mesma configuracao -> unchanged.
- texto alterado -> rebuild atomico.
- configuracao alterada -> rebuild.
- manifesto inativo/removido -> chunks desativados.
- manifesto restaurado -> chunks reconstruidos.
- falha por documento preserva versao anterior valida.

Resumo do modo `--build-chunks`:

- `documents`, `created`, `rebuilt`, `unchanged`, `deactivated`, `chunks_created`, `skipped`, `failed`.

## Limite por documento

- Config: `LIVIA_RAG_EXPORT_MAX_BYTES` (default `1000000`).
- Valores invalidos falham em modo fail-closed.
- Documentos acima do limite sao marcados como falha, preservando versao valida anterior.

## Estados operacionais

`TenantRagConfiguration.last_inventory_status` e `last_index_status`:

- `idle`, `running`, `success`, `partial`, `failed`.

## Rollback operacional

Se necessario pausar rapidamente:

1. Desabilitar sync do tenant:
   ```bash
   python manage.py configure_tenant_rag --tenant <slug> --approved-folder-id <id> --disable-sync
   ```
2. Desabilitar indexacao:
   ```bash
   LIVIA_RAG_INDEXING_ENABLED=False
   ```
3. Nao executar `sync_tenant_rag` em modo exportacao nem `index_tenant_rag` real.
4. Revisar manifesto/staging/chunks/embeddings no admin antes de retomar.

## Custos e cuidados antes da primeira indexacao real

- confirmar migrations aplicadas (`knowledge_base.0006`, `audit.0006`, e `knowledge_base.0007` para retrieval);
- validar provider/modelo/dimensao e `LIVIA_RAG_EMBEDDING_API_KEY`;
- executar primeiro `--dry-run` e revisar contadores;
- habilitar `LIVIA_RAG_INDEXING_ENABLED=True` somente com autorizacao manual;
- para o chat: `LIVIA_RAG_ENABLED=True`, `LIVIA_RAG_DRY_RUN=False` e `--enable-retrieval` no tenant;
- ver detalhes de retrieval em `docs/rag_architecture.md`;
- primeira indexacao cobra embeddings de todos os chunks ativos;
- reindexacoes futuras cobram apenas pending/reindex (ou tudo se a assinatura mudar);
- modelo `text-embedding-3-small` / 1536 dims: custo tipicamente baixo por token, mas depende do volume total de chunks;
- apos indexar, repetir o comando para provar idempotencia (`unchanged` esperados).
