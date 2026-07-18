# Painel operacional da Lívia Platform

A rota /painel/ entrega a primeira fase do painel operacional próprio da Lívia Platform usando o design do template Hando como referência visual. O Django Admin permanece em /admin/ como backoffice técnico.

## Acesso

- usuário anônimo é redirecionado para login;
- usuário autenticado sem is_staff recebe 403;
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

## Dados exibidos

O dashboard consulta dados reais de Tenant, Conversation, rascunho de lead e HandoffRequest. Estados de CRM, OpenAI, webhooks e notificações de handoff são derivados apenas das flags de settings e nunca exibem tokens ou secrets.

## Assets

Os assets do Hando copiados ficam sob static/operations_portal/hando/. O runtime não lê arquivos de ./hando/.
