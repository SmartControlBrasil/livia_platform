# Segurança operacional da Lívia Platform

A Fase 20 adiciona controles mínimos para uso multi-tenant em produção sem ativar IA real, dispatch real ou notificações reais.

## Settings disponíveis

~~~env
LIVIA_MAX_MESSAGE_LENGTH=1200
LIVIA_CHAT_RATE_LIMIT_ENABLED=True
LIVIA_CHAT_RATE_LIMIT_REQUESTS=20
LIVIA_CHAT_RATE_LIMIT_WINDOW_SECONDS=300
LIVIA_SPAM_GUARD_ENABLED=True
# Origins do widget são cadastradas por tenant em TenantAllowedOrigin
~~~

O middleware não é mais permissivo quando a lista global está vazia. A autorização é feita por `TenantAllowedOrigin` e falha fechada em produção.

## Origins do widget

Cadastre somente os domínios que podem hospedar o widget em `TenantAllowedOrigin`. A comparação é exata após normalização, incluindo scheme e porta.

Exemplo recomendado:

~~~env
# Origins do widget são cadastradas por tenant em TenantAllowedOrigin
~~~

Requisições públicas sem `Origin` são bloqueadas por padrão. Chamadas técnicas exigem `LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=True`, que não deve ficar ativo em produção.

## Tenant inativo

Se o tenant existir com is_active=False, /api/chat/ retorna resposta controlada e não cria Conversation, Message, LeadDraft ou HandoffRequest.

Resposta operacional:

~~~text
Este atendimento não está disponível no momento.
~~~

## Limite de mensagem

LIVIA_MAX_MESSAGE_LENGTH limita o tamanho da mensagem antes de qualquer gravação. Mensagens vazias, inválidas ou só com espaços são rejeitadas. Mensagens acima do limite recebem resposta curta e não são salvas.

## Rate limit

O rate limit usa cache Django com chave por tenant + IP. O IP vem do primeiro endereço em HTTP_X_FORWARDED_FOR ou, se ausente, de REMOTE_ADDR. Se nenhum valor existir, usa unknown.

Defaults:

- 20 mensagens;
- janela de 300 segundos;
- controle habilitado.

Quando excedido, /api/chat/ retorna 429 com resposta controlada.

## Anti-spam

O spam guard bloqueia mensagens com muitos links, termos óbvios de spam em inglês, repetição excessiva de caracteres ou muitos caracteres especiais com pouco texto útil. A regra é conservadora para não bloquear leads reais em português.

Quando bloqueado, a plataforma não cria lead nem handoff. A implementação rejeita antes de criar conversa ou mensagens.

## Healthcheck

GET /health/ retorna JSON simples sem consultar banco profundamente e sem expor secrets.

~~~json
{"status":"ok","service":"livia-platform"}
~~~

## Logs seguros

A plataforma registra eventos mínimos para tenant inativo, rate limit, spam, mensagem longa e origin bloqueada. Os logs não incluem API keys, tokens, SECRET_KEY, mensagem completa de spam ou mensagem longa.

## Curls de validação

Healthcheck:

~~~bash
curl -i https://livia.smartcontrolbrasil.com.br/health/
~~~

Tenant inativo ou inexistente:

~~~bash
curl -i https://livia.smartcontrolbrasil.com.br/api/chat/ \
  -H "Content-Type: application/json" \
  -d tenant:tenant-inativo
~~~

Rate limit, repetindo várias vezes dentro da janela:

~~~bash
for i in $(seq 1 25); do
  curl -s https://livia.smartcontrolbrasil.com.br/api/chat/ \
    -H "Content-Type: application/json" \
    -d "{"tenant":"smart-control-brasil","session_id":"curl-rate-$i","message":"Olá"}"
  echo
done
~~~

CORS permitido:

~~~bash
curl -i -X OPTIONS https://livia.smartcontrolbrasil.com.br/api/chat/ \
  -H "Origin: https://www.smartcontrolbrasil.com.br" \
  -H "Access-Control-Request-Method: POST"
~~~

CORS bloqueado:

~~~bash
curl -i -X OPTIONS https://livia.smartcontrolbrasil.com.br/api/chat/ \
  -H "Origin: https://example.invalid" \
  -H "Access-Control-Request-Method: POST"
~~~

## Idempotência do chat público

O endpoint `/api/chat/` exige `request_id` UUID por mensagem depois de tenant e origin validados. A tabela `ChatRequest` guarda fingerprint, status e payload público de resposta para replay seguro, sem persistir o texto da mensagem no log de idempotência. Monitore `chat_request_report` e readiness para requests abandonados, falhas recentes, SQLite em produção e timeout inválido.

## Outbox transacional

Monitore `outbox_report` para eventos vencidos, retries atrasados, locks abandonados e dead letters. `process_outbox` sem `--execute` é dry-run. Requeue manual pelo Admin é restrito a superuser e auditado.
