# Painel operacional da Lívia Platform

A rota `/painel/` entrega o painel operacional próprio da Lívia Platform usando o design do template Hando como referência visual. O Django Admin permanece em `/admin/` como backoffice técnico.

## Acesso

- usuário anônimo é redirecionado para `/admin/login/?next=/painel/` ou para a rota específica acessada;
- usuário autenticado sem `is_staff` recebe 403;
- staff comum também recebe 403 nesta fase;
- superuser visualiza a consolidação administrativa de todos os tenants.

A restrição a superuser é intencional: ainda não existe vínculo seguro entre usuário e tenant na modelagem atual. O painel fica preparado para escopo por tenant em fase futura, sem criar relacionamento improvisado.

## Primeira fase

Inclui:

- layout base no estilo Hando;
- sidebar, topbar e footer adaptados para Lívia Platform;
- tema claro/escuro e navegação responsiva usando os scripts do Hando;
- dashboard de visão geral com KPIs reais;
- placeholders internos para áreas ainda não implementadas;
- link para Administração Django.

## Segunda fase

Foram adicionadas as primeiras telas operacionais navegáveis:

- `/painel/conversas/`: lista paginada de conversas com filtros por tenant, estado, qualificação, período de atualização e sessão;
- `/painel/conversas/<id>/`: detalhe da conversa com dados operacionais, timeline de mensagens, lead vinculado e handoff mais recente;
- `/painel/leads/`: lista paginada de rascunhos de leads com filtros por tenant, status, envio ao CRM, falha de dispatch, período e busca textual;
- `/painel/leads/<id>/`: detalhe do lead com dados coletados, necessidade, estado de CRM, erro de integração, conversa vinculada e handoff mais recente;
- `/painel/leads/<id>/reprocessar-crm/`: ação POST com CSRF para reprocessar manualmente um lead em falha ainda não enviado ao CRM.

Os valores internos dos estados continuam iguais no banco. A interface traduz os estados conhecidos para português, por exemplo `discovery` como `Descoberta`, `collect_need` como `Coleta da necessidade` e `qualified` como `Qualificada`.

## Reprocessamento de CRM

A ação de reprocessamento aparece somente no detalhe de `LeadDraft` com `status=failed`, sem `crm_external_id` e sem `sent_to_crm_at`.

Fluxo operacional:

1. Abrir `/painel/leads/<id>/` como superuser.
2. Confirmar que o lead está em falha e que não há envio anterior ao CRM.
3. Clicar em `Reprocessar envio ao CRM`.
4. O painel muda o lead para `qualified`, limpa `crm_error` e chama `CRMDispatchService.dispatch_if_qualified`.
5. Se o dispatch não for tentado por configuração, o painel restaura `failed` e registra a mensagem de erro retornada pelo serviço.

A prevenção contra duplicidade permanece centralizada no serviço de CRM e é reforçada pela UI: leads já enviados, com `crm_external_id` ou com `sent_to_crm_at` não exibem a ação e não chamam o serviço se receberem POST manual.

## Dados exibidos

O dashboard consulta dados reais de Tenant, Conversation, rascunho de lead e HandoffRequest. Estados de CRM, OpenAI, webhooks e notificações de handoff são derivados apenas das flags de settings e nunca exibem tokens, secrets ou URLs completas de integração.

As listas são intencionalmente mais discretas: contatos de leads aparecem mascarados em `/painel/leads/`. O detalhe do lead exibe os dados completos necessários para operação humana autorizada.

## Assets

Os assets do Hando copiados ficam sob `static/operations_portal/hando/`. O runtime não lê arquivos de `./hando/`.
