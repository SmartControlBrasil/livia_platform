# Multi-site onboarding e RAG por tenant

## Fluxo para adicionar um novo site

1. Crie ou atualize o tenant e o perfil da assistente com `onboard_tenant`:

```bash
.venv/bin/python manage.py onboard_tenant \
  --slug exemplo \
  --name "Empresa Exemplo" \
  --domain exemplo.com.br \
  --allowed-origin https://www.exemplo.com.br \
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

3. Para RAG semântico, use o pipeline já existente:

```text
Tenant
  -> KnowledgeDocument
  -> TenantRagDriveTextStaging / sync quando origem for Drive
  -> TenantRagDocumentChunk
  -> TenantRagChunkEmbedding
  -> retrieve_context / vector search com filtros tenant-scoped
```

4. Execute readiness/indexação conforme o runbook RAG existente (`configure_tenant_rag`, `sync_tenant_rag`, `index_tenant_rag`, `tenant_rag_operations_readiness`).

5. Instale o snippet retornado pelo onboarding no site autorizado.

## Regra operacional

Novo tenant deve exigir dados, perfil e conhecimento, não alteração em `assistant_core/discovery/*.py` nem no widget público.

## Isolamento

- `KnowledgeDocument` tem FK de `tenant` e unique `(tenant, slug)`.
- O retriever textual filtra `KnowledgeDocument.objects.filter(tenant=tenant, status=ACTIVE)`.
- O RAG semântico filtra `TenantRagConfiguration`, chunks e embeddings por tenant antes de ranquear.
- O widget envia `tenant` no payload e `X-Livia-Tenant`; o backend valida mismatch e origin.
