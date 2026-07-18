# Smart360 Lead Dispatch

## Visão geral

Fluxo de envio de LeadDraft qualificado da Lívia Platform para o Smart360 Growth Engine.

Fluxo operacional:

LeadDraft qualified
-> CRMDispatchService
-> Smart360GrowthClient
-> LeadDraft sent_to_crm ou failed

Por padrão, o envio real permanece desligado. O modo dry-run continua sendo o caminho seguro para validar o piloto sem chamar o endpoint real.

## Status do LeadDraft

- draft: lead em construção.
- qualified: lead possui dados mínimos e pode ser enviado.
- sent_to_crm: lead já foi despachado ou simulado com sucesso.
- failed: houve falha durante o despacho e o lead pode ser reprocessado manualmente no Admin.

## Payload LeadIngestPayload

Campos enviados ao contrato de integração:

- tenant_slug
- name
- company
- email
- phone
- city
- need_summary
- notes
- source_page
- conversation_id

## Regras de segurança

- dry-run é o padrão.
- dispatch real só roda com SMART360_LEAD_DISPATCH_ENABLED=True, SMART360_LEAD_DISPATCH_DRY_RUN=False, SMART360_BASE_URL e SMART360_M2M_TOKEN preenchidos.
- LeadDraft sent_to_crm, com crm_external_id ou com sent_to_crm_at não é reenviado.
- LeadDraft não qualificado não é enviado.
- Falha registra crm_error sem expor token ou payload bruto.
- Reprocessamento manual pelo Admin aceita somente LeadDraft failed sem crm_external_id e sem sent_to_crm_at.
- Cada payload preserva tenant_slug; não há lookup cruzado entre tenants.

## Logs seguros

Pode logar:

- event
- lead_draft_id
- tenant_slug
- status
- crm_external_id de sucesso

Nunca logar:

- token Smart360
- payload bruto
- telefone completo
- e-mail completo
- necessidade completa
- dados sensíveis de cliente

Eventos esperados:

- crm_dispatch_attempt
- crm_dispatch_success_dry_run
- crm_dispatch_success_real
- crm_dispatch_failure_dry_run
- crm_dispatch_failure_real
- crm_dispatch_failure_missing_config
- crm_dispatch_ignored_disabled
- crm_dispatch_ignored_not_qualified
- crm_dispatch_ignored_already_sent

## Configuração por ambiente

Variáveis:

~~~env
SMART360_BASE_URL=https://www.smartcontrolbrasil.com.br
SMART360_M2M_TOKEN=
SMART360_LEAD_DISPATCH_ENABLED=False
SMART360_LEAD_DISPATCH_DRY_RUN=True
~~~

## Ativar primeiro em dry-run

1. Confirme que o token já está na VPS, mas não o exponha em terminal compartilhado ou documentação.
2. Configure no ambiente do serviço:

~~~env
SMART360_BASE_URL=https://www.smartcontrolbrasil.com.br
SMART360_LEAD_DISPATCH_ENABLED=False
SMART360_LEAD_DISPATCH_DRY_RUN=True
~~~

3. Reinicie apenas livia-platform.service.
4. Gere ou selecione um LeadDraft qualificado no Admin.
5. Acione o fluxo normal ou use a action de reprocessamento se o LeadDraft estiver failed.
6. Valide no Admin que o LeadDraft foi para sent_to_crm, recebeu crm_external_id com prefixo dry-run- e preencheu sent_to_crm_at.
7. Confira logs do serviço procurando crm_dispatch_attempt e crm_dispatch_success_dry_run.

## Ativar modo real

Execute somente depois do dry-run validado.

1. Confirme com o responsável pelo Smart360 que o endpoint real está pronto para receber leads.
2. Configure no ambiente do serviço:

~~~env
SMART360_BASE_URL=https://www.smartcontrolbrasil.com.br
SMART360_LEAD_DISPATCH_ENABLED=True
SMART360_LEAD_DISPATCH_DRY_RUN=False
SMART360_M2M_TOKEN=<valor configurado somente na VPS>
~~~

3. Reinicie apenas livia-platform.service.
4. Envie um único LeadDraft qualificado de baixo risco.
5. Valide no Admin que o status mudou para sent_to_crm, com crm_external_id real e sent_to_crm_at preenchido.
6. Confira logs procurando crm_dispatch_attempt e crm_dispatch_success_real.
7. Se falhar, volte para:

~~~env
SMART360_LEAD_DISPATCH_ENABLED=False
SMART360_LEAD_DISPATCH_DRY_RUN=True
~~~

8. Reprocesse manualmente apenas LeadDrafts failed depois de corrigir a causa.
