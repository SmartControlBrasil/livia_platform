# Knowledge Base / RAG mínimo da Lívia

A `knowledge_base` guarda documentos curtos por tenant para enriquecer respostas da Lívia sem depender de OpenAI ou embeddings nesta fase.

## Criar documentos

Use o model `KnowledgeDocument` com:

- `tenant`: tenant dono do conteúdo.
- `title`: título curto e descritivo.
- `slug`: identificador único por tenant.
- `content`: texto consultivo curto, seguro e sem promessas comerciais.
- `source_type`: origem do conteúdo, por exemplo `manual`, `seed`, `site`.
- `source_url`: URL de referência quando existir.
- `tags`: lista simples de termos, como `robotics`, `hygibot`, `automation`, `mitsubishi`.
- `status`: use `active` para conteúdo disponível na busca.

Evite colocar preço, prazo, garantia ou especificações não confirmadas.

## Seed demonstrativo

Para criar ou atualizar os documentos iniciais do tenant `smart-control-brasil`:

```bash
.venv/bin/python manage.py seed_demo_knowledge
```

O comando é idempotente: usa `update_or_create` por `tenant + slug`, então pode rodar várias vezes sem duplicar.

## Como o RAG simples funciona

A função principal é:

```python
retrieve_relevant_knowledge(tenant, query, service_area=None, limit=3)
```

Ela busca apenas documentos ativos do tenant informado, normaliza texto sem acentos, expande alguns sinônimos e calcula uma pontuação simples com base em:

- ocorrências no título;
- ocorrências no conteúdo;
- tags;
- afinidade com `service_area` detectada pela discovery.

O retorno é uma lista de `KnowledgeSnippet`, contendo título, trecho curto, URL de origem, score e tags. O documento completo não é enviado para a resposta.

O context builder:

```python
build_knowledge_context(tenant, message, service_area=None)
```

monta um texto curto com até poucos trechos relevantes para a Lívia usar no fluxo consultivo.

## Integração com a Lívia

`LiviaDecisionService` consulta a knowledge base após a discovery identificar intenção e `service_area`. Quando há contexto relevante, a resposta ganha uma ou duas informações úteis antes da pergunta consultiva. Quando não há resultado, o comportamento atual é preservado.

## Limitações atuais

- O retriever público (`retrieve_relevant_knowledge`) continua textual e usa somente `KnowledgeDocument`.
- A fase 4 adicionou embeddings locais por tenant (`TenantRagChunkEmbedding`), ainda desconectados do chat/`/api/chat/`.
- A busca vetorial administrativa (`admin_vector_search`) filtra por tenant antes do cosseno e não é endpoint público.
- SQLite não oferece busca vetorial de produção; pgvector fica para evolução em PostgreSQL.
- Sem parser de PDF complexo.
- Sem dashboard/admin customizado além da inspeção segura dos models.

## Plano futuro

- Conectar o índice vetorial multi-tenant ao retriever público com gate explícito.
- Migrar armazenamento vetorial para pgvector em PostgreSQL quando a escala exigir.
- Adicionar pipeline de ingestão de PDFs e páginas do site.
- Permitir curadoria/admin avançada para cada tenant.
- Usar OpenAI para síntese controlada, mantendo restrições de segurança comercial.
