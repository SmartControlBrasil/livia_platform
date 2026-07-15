# Webhooks da Lívia Platform

A camada de webhooks envia eventos operacionais por tenant para canais externos, como N8N, sem substituir o dispatch Smart360 e sem ativar envio real por padrão.

## Settings

~~~env
LIVIA_WEBHOOKS_ENABLED=False
LIVIA_WEBHOOKS_DRY_RUN=True
LIVIA_WEBHOOK_TIMEOUT_SECONDS=6
~~~

Com os defaults acima, nenhuma requisição real é enviada. Quando houver configs ativas, a plataforma pode registrar logs como skipped se os webhooks estiverem globalmente desabilitados.

## Criar webhook pelo admin

1. Acesse /admin/.
2. Abra Integrations > Tenant webhook configs.
3. Crie uma config com tenant, nome, tipo de evento e target_url.
4. Mantenha dry_run=True para validação inicial.
5. Preencha secret_token apenas se o destino exigir autenticação. O token não aparece nas listagens nem nos payload previews.

Tipos de evento:

- handoff_created;
- lead_qualified;
- conversation_summary, reservado para fase futura;
- all.

Configs com is_active=False não enviam e não geram entrega.

## Exemplo para N8N

No N8N, crie um Webhook node com método POST e copie a URL para target_url. Durante validação, deixe:

~~~env
LIVIA_WEBHOOKS_ENABLED=True
LIVIA_WEBHOOKS_DRY_RUN=True
~~~

Depois confira Integrations > Webhook delivery logs. Para envio real, use uma janela controlada e altere:

~~~env
LIVIA_WEBHOOKS_ENABLED=True
LIVIA_WEBHOOKS_DRY_RUN=False
~~~

A config específica também precisa estar com dry_run=False.

## Payload handoff_created

~~~json
{
  "tenant_slug": "smart-control-brasil",
  "event_type": "handoff_created",
  "handoff_id": 123,
  "status": "pending",
  "reason": "explicit_request",
  "priority": "normal",
  "visitor_name": "Maria",
  "visitor_company": "ACME",
  "visitor_phone": "11999999999",
  "visitor_email": "maria@example.com",
  "summary": "Resumo seguro do atendimento",
  "source_page": "https://example.com/origem",
  "created_at": "2026-07-15T10:00:00-03:00"
}
~~~

## Payload lead_qualified

~~~json
{
  "tenant_slug": "smart-control-brasil",
  "event_type": "lead_qualified",
  "lead_id": 456,
  "name": "Maria",
  "company": "ACME",
  "phone": "11999999999",
  "email": "maria@example.com",
  "service_area": "automation",
  "status": "qualified",
  "need_summary": "Preciso de automação industrial",
  "source_page": "https://example.com/origem",
  "created_at": "2026-07-15T10:00:00-03:00"
}
~~~

## Headers enviados

Em envio real, a Lívia envia:

- Content-Type: application/json;
- X-Livia-Event;
- X-Livia-Tenant;
- Authorization: Bearer ..., se houver secret_token;
- X-Livia-Signature, se houver secret_token.

Tokens não são gravados nos logs de entrega.

## Modo dry-run

Há dois níveis de dry-run:

1. Global: LIVIA_WEBHOOKS_DRY_RUN=True.
2. Por config: TenantWebhookConfig.dry_run=True.

Se qualquer um estiver ativo, a plataforma não faz POST real e cria WebhookDeliveryLog com status dry_run.

## Logs

Consulte Integrations > Webhook delivery logs. Os logs registram tenant, config, evento, status, status_code, erro curto e payload_preview truncado. O preview não inclui tokens, prompts de IA, metadata completa nem transcript completo.

Status possíveis:

- skipped: global disabled ou evento duplicado já entregue;
- dry_run: simulação sem POST;
- sent: POST real 2xx;
- failed: erro HTTP, timeout ou falha de rede.

## Rollback

Para interromper envios reais rapidamente:

1. Defina LIVIA_WEBHOOKS_ENABLED=False; ou
2. Defina LIVIA_WEBHOOKS_DRY_RUN=True; ou
3. Desative configs específicas com is_active=False no admin.

Essas ações não afetam Smart360, IA, handoff interno ou a resposta ao usuário.
