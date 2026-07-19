# Auditoria operacional

O app `audit` registra eventos explícitos de rastreabilidade para ações operacionais sensíveis da Lívia Platform.

## Eventos registrados

- `handoff.status_changed`: mudança válida de status de `HandoffRequest` pelo portal.
- `lead.crm_dispatch_retried`: tentativa de reprocessamento de `LeadDraft` para CRM pelo portal.
- `assistant_profile.updated`: alteração de configurações de `AssistantProfile` pelo portal ou Admin.
- `tenant.created` e `tenant.updated`: criação e alteração de `Tenant` pelo Admin.
- `knowledge_document.created` e `knowledge_document.updated`: criação e alteração de `KnowledgeDocument` pelo Admin.
- `webhook_config.created` e `webhook_config.updated`: criação e alteração de `TenantWebhookConfig` pelo Admin.

## Campos armazenados

Cada `AuditEvent` guarda tenant, ator, ação, tipo lógico do objeto, id textual, representação curta, dados anteriores, dados novos, metadados, IP quando disponível e data de criação.

As alterações de atualização registram apenas campos relevantes alterados. Criações registram um snapshot seguro dos campos operacionais necessários.

## Política de dados sensíveis

O serviço `audit.services.record_audit_event` sanitiza dados antes de persistir:

- mascara chaves como `password`, `token`, `secret`, `api_key`, `authorization` e `transcript`;
- limita textos longos;
- normaliza tipos comuns para JSON;
- substitui valores não serializáveis por marcador seguro;
- não registra conteúdo completo de conversas, mensagens completas, chaves OpenAI, tokens M2M, secrets de webhook ou payloads completos.

Falhas secundárias de serialização não derrubam a operação principal de auditoria. Falhas reais de banco não são silenciadas.

## Consulta pelo Admin

`AuditEvent` está disponível no Django Admin somente para visualização. A tela permite filtrar por ação, tenant e data de criação, além de buscar por tipo do objeto, id, representação e ator.

Criação, alteração e exclusão manual de eventos pelo Admin são bloqueadas.

## Limitações atuais

- Actions em massa do Admin que usam `queryset.update()` não geram eventos individuais.
- A extração de IP usa endereços válidos em `X-Forwarded-For` quando presentes e cai para `REMOTE_ADDR`.
- A auditoria cobre somente os eventos listados acima; não há registro genérico por signals.

## Como adicionar novas ações auditáveis

1. Adicione uma constante e um item em `AuditEvent.Action`.
2. Escolha o ponto explícito da operação, como uma view, service ou `ModelAdmin.save_model`.
3. Capture o estado anterior antes de salvar.
4. Salve a operação principal.
5. Capture o estado novo e chame `record_audit_event`.
6. Inclua apenas campos úteis para rastreabilidade e nunca payloads completos ou segredos.
7. Adicione testes cobrindo sucesso, dados sensíveis e casos inválidos sem evento.
