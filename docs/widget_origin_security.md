# Segurança de origins do widget

O widget público valida o `Origin` do navegador contra origins autorizadas por tenant antes de liberar `/api/widget/config/` e `/api/chat/`.

## Ameaça Mitigada

A proteção reduz uso indevido do widget de um tenant em sites não autorizados. Ela bloqueia antes de criar conversa, mensagem, lead, handoff, webhook log ou evento operacional.

CORS não é autenticação: clientes fora do navegador podem falsificar headers. A validação serve para proteger o fluxo web do widget e deve ser combinada com rate limit, spam guard e monitoramento.

## Formato Aceito

Origins devem ser exatas e canônicas:

- `https://www.exemplo.com.br`
- `https://exemplo.com.br`
- `http://localhost:8000`

Regras:

- somente `http` ou `https`;
- sem path, query string ou fragment;
- sem credenciais;
- host e scheme em lowercase;
- porta explícita preservada;
- sem wildcard `*`;
- sem correspondência por sufixo.

Cadastre domínio com e sem `www` quando ambos hospedarem o widget.

## Produção E Desenvolvimento

Com `DEBUG=False`, config e chat operam fail-closed:

- tenant sem origin ativa é bloqueado;
- `Origin` ausente é bloqueado;
- origin malformada ou não autorizada retorna 403;
- `Access-Control-Allow-Origin` nunca usa `*`.

Em desenvolvimento, origins locais são permitidas somente pela lista `LIVIA_DEV_ALLOWED_WIDGET_ORIGINS`. Chamadas sem `Origin` exigem `LIVIA_ALLOW_ORIGINLESS_PUBLIC_API=True`; a flag não deve ficar ativa em produção.

## Preflight

O widget envia `X-Livia-Tenant: <slug>`. O preflight usa esse header ou `tenant` na query string para localizar o tenant, validar a origin e só então devolver CORS.

Se o tenant não existe, está inativo ou a origin falha, o backend retorna 403 e não reflete `Origin`.

## Onboarding E Admin

Use `--allowed-origin` no onboarding:

```bash
.venv/bin/python manage.py onboard_tenant --slug exemplo --name "Exemplo" --domain https://www.exemplo.com.br --allowed-origin https://www.exemplo.com.br
```

O argumento pode ser repetido. O Django Admin também permite gerenciar `TenantAllowedOrigin`, somente por superuser nesta fase.

## Logs E Readiness

Bloqueios públicos geram logs curtos com tenant, host e motivo, sem payloads completos ou secrets.

A verificação operacional aponta tenants ativos sem origin ativa, origins inválidas, duplicatas lógicas, widget ativo sem origin, lista global antiga em produção e `LIVIA_ALLOW_ORIGINLESS_PUBLIC_API` ativa em produção.

## Troubleshooting 403

1. Confira se o site envia `Origin`.
2. Confira se o snippet usa o tenant correto.
3. Cadastre a origin exata, incluindo scheme e porta.
4. Cadastre variantes com e sem `www` separadamente.
5. Verifique se o tenant, profile e origin estão ativos.
