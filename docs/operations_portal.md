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


## Terceira fase

A visão geral em `/painel/` passou a ser um dashboard analítico com dados reais e seletor de período.

Períodos aceitos:

- últimos 7 dias: `?period=7`;
- últimos 30 dias: `?period=30`;
- últimos 90 dias: `?period=90`.

O padrão é 30 dias. Qualquer valor inválido volta para 30 dias. As consultas usam o timezone ativo da aplicação, configurado para `America/Sao_Paulo` em produção.

### KPIs

Os cards analíticos exibem:

- conversas criadas no período;
- leads criados no período;
- leads qualificados no período, considerando leads criados no período cujo status atual é `qualified`;
- leads enviados ao CRM no período, considerando leads criados no período cujo status atual é `sent_to_crm`;
- leads com falha no período, considerando leads criados no período cujo status atual é `failed`;
- handoffs pendentes atuais e handoffs pendentes criados no período;
- tenants ativos como total geral;
- conversas e leads totais identificados como `Total geral`.

Fórmulas:

- taxa de qualificação = leads qualificados / leads criados no período;
- taxa de envio ao CRM = leads enviados ao CRM / leads criados no período;
- quando o divisor é zero, a taxa exibida é `0%`.

Não há comparação com período anterior nesta fase.

### Gráficos

- `Conversas por dia`: conversas novas agrupadas por `created_at`, com dias sem registro preenchidos com zero.
- `Geração e envio de leads`: leads criados por `created_at` e envios por `sent_to_crm_at`. Como não existe campo de data exata de qualificação, o gráfico não inventa série de qualificação diária.
- `Funil comercial`: leads criados no período agrupados pelo status atual. As categorias são mutuamente exclusivas: rascunho, qualificado, enviado ao CRM e falha de envio.
- `Conversas por etapa`: conversas criadas no período agrupadas por `lead_state`, com rótulos traduzidos apenas na apresentação.
- `Volume por tenant`: top 10 tenants por volume combinado de conversas e leads criados no período. Quando há mais de 10 tenants com atividade, o card indica `Top 10`.

### Segurança dos datasets

Os gráficos recebem dados por `json_script` do Django. Os datasets contêm apenas datas, rótulos, quantidades e estados operacionais. Não são incluídos nomes de pessoas, e-mails, telefones, mensagens ou `session_id` nos datasets dos gráficos.

Os assets de gráfico usam ApexCharts copiado localmente de `./hando/hando/static/libs/apexcharts/apexcharts.min.js` para `operations_portal/static/operations_portal/hando/libs/apexcharts/apexcharts.min.js`. Não há CDN nem dependência de Node/Gulp/Bun em produção.


## Quarta fase

A área `/painel/handoffs/` substitui o placeholder de Handoffs por uma gestão operacional somente para superusers.

Rotas:

- `/painel/handoffs/`: lista paginada de handoffs;
- `/painel/handoffs/<id>/`: detalhe do handoff;
- `/painel/handoffs/<id>/status/`: ação POST com CSRF para transição de status.

Campos reais usados de `HandoffRequest`:

- status: `pending`, `sent`, `resolved`, `cancelled`;
- motivo: `explicit_request`, `qualified_lead`, `technical_complexity`, `support_request`, `emergency_or_urgent`, `manual`;
- prioridade: `low`, `normal`, `high`, `urgent`;
- contato: `visitor_name`, `visitor_company`, `visitor_email`, `visitor_phone`;
- contexto: `summary`, `source_page`, `metadata`, conversa e lead relacionados.

A listagem ordena handoffs pendentes e notificados antes dos terminais, maior prioridade primeiro, depois criação mais recente. Os filtros disponíveis são tenant, status, prioridade, motivo, período e busca por sessão, contato ou lead relacionado. Os filtros são preservados na paginação.

### Fluxo de status

As transições permitidas no painel são:

- `pending` -> `sent`, `resolved` ou `cancelled`;
- `sent` -> `resolved` ou `cancelled`;
- `resolved` e `cancelled` são terminais no painel.

`sent` usa o método existente `HandoffService.mark_sent`. `resolved` usa `HandoffService.mark_resolved`, preenchendo `resolved_at`. `cancelled` usa o choice existente do model, alinhado à ação já existente no Django Admin. Não há exclusão de handoff, edição de mensagens ou mudança automática de prioridade.

### Notificações

O painel mostra somente estados seguros das notificações de handoff:

- `Desligadas`;
- `Dry-run`;
- `Ativas`;
- `Configuração incompleta`.

Nenhum destinatário, token, SMTP, cabeçalho ou valor de `.env` é exibido. Reenvio manual não foi implementado porque o serviço atual (`HandoffNotificationService`) não oferece operação idempotente de reenvio real.

### Privacidade

Na lista, e-mail e telefone ficam mascarados. O detalhe mostra contato completo apenas para superuser autenticado. Mensagens da timeline e metadata são renderizadas sem `safe`, preservando escape de HTML do usuário.

## Dados exibidos

O dashboard consulta dados reais de Tenant, Conversation, rascunho de lead e HandoffRequest. Estados de CRM, OpenAI, webhooks e notificações de handoff são derivados apenas das flags de settings e nunca exibem tokens, secrets ou URLs completas de integração.

As listas são intencionalmente mais discretas: contatos de leads aparecem mascarados em `/painel/leads/`. O detalhe do lead exibe os dados completos necessários para operação humana autorizada.

## Assets

Os assets do Hando copiados ficam sob `static/operations_portal/hando/`. O runtime não lê arquivos de `./hando/`.
