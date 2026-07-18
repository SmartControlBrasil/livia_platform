# Operação administrativa da Lívia Platform

A operação mínima da Lívia Platform usa o Django Admin nativo. Esta fase não cria dashboard customizado, não ativa IA real e não ativa dispatch real automaticamente.

## Acesso

Use /admin/ com um usuário staff/superuser do Django. Os modelos principais aparecem organizados pelos apps existentes: Tenants, Conversations, Leads e Knowledge base.

## Criar tenant

1. Abra Tenants > Tenants.
2. Crie um registro com name, slug, domain e is_active=True.
3. Use um slug estável, pois o widget e a API dependem dele para rotear conversas.

Actions disponíveis:

- marcar tenants selecionados como ativos;
- marcar tenants selecionados como inativos.

## Criar AssistantProfile

1. Abra Tenants > Assistant profiles.
2. Selecione o tenant.
3. Configure name, initial_message, tone, primary_goal e is_active.
4. Mantenha use_ai=False por padrão.

Actions disponíveis:

- ativar IA nos perfis selecionados;
- desativar IA nos perfis selecionados.

## Cadastrar knowledge docs

1. Abra Knowledge base > Knowledge documents.
2. Escolha o tenant.
3. Preencha title, slug, content, source_type, source_url se existir, tags e status.
4. Use status=active apenas para conteúdo que pode ser usado pela Lívia.

Actions disponíveis:

- ativar documentos selecionados;
- arquivar documentos selecionados.

Boas práticas para tags: use termos como automation, robotics, maintenance, software_web, nomes de produtos e palavras comuns do visitante.

## Consultar conversas e mensagens

Abra Conversations > Conversations para filtrar por tenant, lead_state e qualificação. A listagem também mostra o status do LeadDraft relacionado e o handoff mais recente. O detalhe da conversa exibe mensagens em linha.

Abra Conversations > Messages para buscar conteúdo específico. A listagem mostra apenas um resumo curto do conteúdo para evitar dumps grandes.

## Consultar leads

Abra Leads > Lead drafts para acompanhar nome, empresa, telefone, e-mail, status, área de serviço calculada, estado de dispatch CRM, crm_external_id e sent_to_crm_at.

A área de serviço é derivada do resumo da necessidade, sem criar campo novo no banco. Use os filtros por tenant, status, service area e data de envio para triagem.

Action disponível:

- reprocessar envio ao CRM dos LeadDrafts com falha.

A action reprocessa somente LeadDraft failed sem crm_external_id e sem sent_to_crm_at; leads já enviados ou inconsistentes são ignorados para impedir dispatch duplicado.

## Consultar handoffs

Abra Conversations > Handoff requests para ver pedidos de atendimento humano. A listagem mostra conversa, LeadDraft relacionado, status, motivo, prioridade, contato e resumo curto. Filtros disponíveis: status, motivo, prioridade e tenant.

Action disponível:

- marcar handoffs selecionados como resolvidos;
- marcar handoffs selecionados como cancelados.

Campos como metadata, datas de criação/atualização e resolved_at ficam em leitura para preservar trilha operacional.

## Ativar IA por profile com segurança

A IA opcional exige duas chaves ao mesmo tempo:

1. Flag global LIVIA_AI_ENABLED=True.
2. AssistantProfile.use_ai=True no tenant desejado.

Para validação sem custo, mantenha:

~~~env
LIVIA_AI_ENABLED=True
LIVIA_AI_DRY_RUN=True
~~~

Com dry-run ativo, a plataforma não faz chamada real à OpenAI.

## Manter IA desligada globalmente em produção

O modo mais seguro para produção é:

~~~env
LIVIA_AI_ENABLED=False
LIVIA_AI_DRY_RUN=True
LIVIA_OPENAI_API_KEY=
~~~

Mesmo que algum perfil esteja com use_ai=True, a camada de IA não roda enquanto LIVIA_AI_ENABLED=False.

## Dispatch CRM

Esta operação administrativa não ativa dispatch real automaticamente. O fluxo seguro para o piloto é validar primeiro em dry-run e só depois ativar modo real.

Dry-run recomendado:

~~~env
SMART360_LEAD_DISPATCH_ENABLED=False
SMART360_LEAD_DISPATCH_DRY_RUN=True
~~~

Modo real, somente após validação operacional:

~~~env
SMART360_LEAD_DISPATCH_ENABLED=True
SMART360_LEAD_DISPATCH_DRY_RUN=False
SMART360_BASE_URL=https://www.smartcontrolbrasil.com.br
SMART360_M2M_TOKEN=<configurado somente na VPS>
~~~

Procedimento detalhado: docs/integrations/smart360_lead_dispatch.md.

## Variáveis operacionais

A lista completa de variáveis existentes fica em .env.example. Não coloque tokens reais na documentação, no repositório ou em prints de terminal.
