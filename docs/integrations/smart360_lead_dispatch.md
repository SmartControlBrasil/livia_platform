# Smart360 Lead Dispatch

## Visão geral

Fluxo de preparação de envio de leads qualificados da Lívia Platform para o Smart360 Growth Engine.

Fluxo atual:

LeadDraft qualified
-> crm_dispatch.py
-> Smart360GrowthClient dry_run=True
-> LeadDraft sent_to_crm ou failed

Por enquanto, o envio é apenas simulado em dry_run=True. Nenhum endpoint real do Smart360 é chamado.

## Status do LeadDraft

- draft: lead em construção.
- qualified: lead possui dados mínimos.
- sent_to_crm: lead foi preparado/despachado com sucesso em dry-run.
- failed: houve falha durante o despacho.

## Payload LeadIngestPayload

Campos previstos:

- tenant_slug
- name
- company
- email
- phone
- city
- need_summary
- source_page
- conversation_id

## Regras atuais

- dry_run=True é o padrão.
- O Smart360 real não é chamado.
- LeadDraft qualified pode ser despachado em dry-run.
- Sucesso muda status para sent_to_crm.
- Sucesso preenche crm_external_id mockado.
- Sucesso preenche sent_to_crm_at.
- Falha muda status para failed.
- Falha preenche crm_error.
- Lead sent_to_crm não deve ser reenviado.
- Lead não qualified não deve ser despachado.

## Segurança de logs

Pode logar:

- lead_draft_id
- tenant_slug
- status
- crm_external_id mockado
- tipo do evento

Nunca logar:

- telefone completo
- e-mail completo
- necessidade completa
- tokens
- payload bruto
- dados sensíveis de cliente

## Próximos passos

- Adicionar SMART360_BASE_URL.
- Adicionar SMART360_M2M_TOKEN.
- Criar feature flag para envio real.
- Manter envio real desligado por padrão.
- Validar contrato real com o endpoint do Smart360 Growth Engine.
- Adicionar testes para modo real usando mock HTTP.
