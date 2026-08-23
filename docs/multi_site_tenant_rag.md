# Multi-site onboarding e RAG por tenant

## Fluxo para adicionar um novo site

1. Crie ou atualize o tenant e o perfil da assistente com `tenant_onboard` (`onboard_tenant` permanece compatível):

```bash
.venv/bin/python manage.py tenant_onboard \
  --slug exemplo \
  --name "Empresa Exemplo" \
  --domain exemplo.com.br \
  --origin https://exemplo.com.br \
  --origin https://www.exemplo.com.br \
  --assistant-name "Lívia" \
  --business-domain "segmento e escopo de atendimento da empresa" \
  --short-description "descrição curta, temas atendidos e orientação de discovery" \
  --initial-message "Olá! Sou a Lívia da Empresa Exemplo. Como posso ajudar?" \
  --widget-title "Lívia Empresa Exemplo" \
  --launcher-label "Fale conosco" \
  --primary-color "#2563eb" \
  --position bottom_right \
  --apply
```

2. Importe conhecimento tenant-aware quando houver corpus local:

```bash
.venv/bin/python manage.py import_tenant_knowledge \
  --tenant exemplo \
  --source ./knowledge/exemplo \
  --tag institucional \
  --replace
```

3. Consulte o lifecycle estruturado:

```bash
.venv/bin/python manage.py tenant_knowledge_status --tenant exemplo
```

4. Para RAG semântico, use o lifecycle central e o pipeline existente:

```text
Tenant
  -> KnowledgeLifecycleService
  -> KnowledgeDocument (content_sha256/lifecycle_status)
  -> TenantRagDriveFileManifest / TenantRagDriveTextStaging
  -> TenantRagDocumentChunk
  -> TenantRagChunkEmbedding
  -> retrieve_context / vector search com filtros tenant/chunk/manifest ativos
  -> grounded response
```

5. Reindexe quando o status indicar `STALE`, `IMPORTED` ou `FAILED` corrigido:

```bash
.venv/bin/python manage.py reindex_tenant_knowledge --tenant exemplo --dry-run
.venv/bin/python manage.py reindex_tenant_knowledge --tenant exemplo
```

6. Execute readiness/indexação Google Drive conforme o runbook RAG existente (`configure_tenant_rag`, `sync_tenant_rag`, `index_tenant_rag`, `tenant_rag_operations_readiness`) quando a fonte for Drive. A Fase 32 não habilita Google Drive real.

7. Instale o snippet retornado pelo onboarding no site autorizado.

## Regra operacional

Novo tenant deve exigir dados, perfil e conhecimento, não alteração em `assistant_core/discovery/*.py` nem no widget público.

## Lifecycle

`KnowledgeDocument.lifecycle_status` representa o estado operacional do documento:

- `new`: documento legado ou ainda não processado pelo lifecycle.
- `imported`: documento salvo, mas sem configuração RAG para gerar manifest/chunks.
- `indexing`: reindexação em andamento.
- `indexed`: fingerprint atual corresponde ao índice ativo.
- `stale`: conteúdo/fonte mudou ou ainda precisa processar chunks/embeddings.
- `failed`: última tentativa de indexação falhou.
- `disabled`: documento fora do retrieval sem hard delete.

`content_sha256` é o fingerprint SHA-256 determinístico do texto normalizado que entra no RAG; `indexed_content_sha256` marca a versão efetivamente indexada.

## Isolamento

- `KnowledgeDocument` tem FK de `tenant` e unique `(tenant, slug)`.
- `KnowledgeLifecycleService` sempre recebe tenant e usa queries tenant-scoped.
- O retriever textual usa apenas documentos ativos e utilizáveis pelo lifecycle.
- O RAG semântico filtra embedding, chunk e manifest por tenant, status ativo e manifest utilizável antes de ranquear.
- Documento `disabled`, `failed` ou `stale` não entra silenciosamente no contexto.
- O widget envia `tenant` no payload e `X-Livia-Tenant`; o backend valida mismatch e origin.
