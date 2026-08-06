# Integração do widget em sites clientes

Este documento descreve o fluxo operacional para instalar a Lívia Platform em sites de clientes com segurança, repetibilidade e validação de prontidão por tenant.

## 1. Pré-requisitos

- Tenant cadastrado e ativo no Django Admin.
- `AssistantProfile` configurado e com widget habilitado.
- Pelo menos uma origin ativa em `TenantAllowedOrigin`.
- Ambiente da plataforma com defaults fail-closed preservados (`LIVIA_AI_ENABLED=False`, integrações reais desligadas, dry-run ativo).
- Site do cliente publicado em uma origin exatamente igual à cadastrada (scheme + host + porta quando aplicável).

## 2. Cadastro do tenant

Use o Django Admin (`Tenants > Tenants`) ou o comando de onboarding existente:

```bash
python manage.py onboard_tenant \
  --slug cliente-exemplo \
  --name "Cliente Exemplo" \
  --domain https://www.cliente-exemplo.com.br \
  --allowed-origin https://www.cliente-exemplo.com.br \
  --dry-run
```

Aplicação explícita:

```bash
python manage.py onboard_tenant \
  --slug cliente-exemplo \
  --name "Cliente Exemplo" \
  --domain https://www.cliente-exemplo.com.br \
  --allowed-origin https://www.cliente-exemplo.com.br \
  --apply \
  --allow-update-existing
```

Campos mínimos:

- `slug`: identificador público usado no snippet (`data-tenant`).
- `name`: nome exibido na página de instalação.
- `domain`: referência operacional; não substitui o cadastro de origins.
- `is_active=True`.

## 3. Cadastro das origins

Cadastre origins em `Tenants > Tenant allowed origins`.

Regras:

- Formato permitido: `http://host` ou `https://host` (+ porta opcional).
- Não use path, query, fragment, credenciais ou wildcard (`*`).
- Cada tenant possui lista própria; não há autorização global compartilhada.
- O navegador envia o header `Origin`; a plataforma compara exatamente com a lista do tenant.

Exemplos válidos:

```text
https://www.cliente-exemplo.com.br
https://app.cliente-exemplo.com.br
http://localhost:8000
```

## 4. Configuração do assistente

No Django Admin (`Tenants > Assistant profiles`):

- `name`: nome público do assistente.
- `is_active=True`.
- `is_widget_enabled=True`.
- Campos visuais (`widget_title`, `launcher_label`, `primary_color`, `position`, `placeholder_text`).

O endpoint público `/api/widget/config/` expõe apenas campos seguros para renderização.

## 5. Execução do readiness

Comando recomendado:

```bash
python manage.py tenant_site_readiness --tenant=<slug>
```

Saída JSON:

```bash
python manage.py tenant_site_readiness --tenant=<slug> --json
```

Estados possíveis:

- `READY`: tenant apto para instalação.
- `WARNING`: instalável, mas com avisos (ex.: `use_ai` ativo com IA global desligada).
- `NOT_READY`: bloqueios que impedem instalação segura.

Exit code:

- `0` para `READY` ou `WARNING`.
- diferente de `0` para `NOT_READY`.

## 6. Obtenção do snippet

Página HTML:

```text
/install/<tenant_slug>/
```

Pacote JSON:

```text
/install/<tenant_slug>.json
```

Contrato oficial do snippet:

```html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="slug-do-tenant"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"
  defer>
</script>
```

Notas:

- `data-tenant` é obrigatório.
- `data-api-url` permanece suportado por retrocompatibilidade; se omitido, o widget deriva `/api/chat/` a partir de `widget.js`.
- `defer` é recomendado e suportado.
- O widget evita inicialização duplicada por tenant.

## 7. Instalação no HTML

1. Abra `/install/<slug>/`.
2. Copie o snippet (botão "Copiar snippet" ou texto do `<pre>`).
3. Cole antes de `</body>`.
4. Publique o site.

## 8. Teste no navegador

1. Abra o site publicado em uma origin cadastrada.
2. Confirme o botão/flutuante da Lívia.
3. Envie uma mensagem curta.
4. Verifique resposta segura mesmo com IA real desligada.

Teste complementar via curl (requer origin cadastrada):

```bash
curl -i https://livia.smartcontrolbrasil.com.br/api/chat/ \
  -H "Content-Type: application/json" \
  -H "Origin: https://www.cliente-exemplo.com.br" \
  -H "X-Livia-Tenant: cliente-exemplo" \
  -d '{"tenant":"cliente-exemplo","session_id":"install-test","message":"Olá"}'
```

## 9. Erros comuns

| Sintoma | Causa provável | Ação |
|--------|----------------|------|
| Widget não aparece | origin não cadastrada | Cadastre a origin exata do site |
| `/api/widget/config/` 403 | origin bloqueada | Corrija scheme/host/porta |
| Tenant inativo | `is_active=False` | Ative o tenant |
| Perfil inativo | `AssistantProfile.is_active=False` | Ative o perfil |
| Widget desabilitado | `is_widget_enabled=False` | Habilite no perfil |
| Snippet sem resposta | tenant/origin inválidos | Rode `tenant_site_readiness` |

## 10. Rollback ou remoção do widget

Remova o `<script>` do HTML do cliente e republique.

Opcionalmente:

- desative `is_widget_enabled` no perfil;
- desative o tenant;
- desative origins no admin.

Nenhuma alteração destrutiva é necessária no banco para rollback do embed.

## 11. Política de segurança de origins

- Fail-closed: origin ausente ou não autorizada bloqueia chat e config.
- Sem wildcard global.
- Sem `Access-Control-Allow-Origin: *`.
- Header `X-Livia-Tenant` validado contra payload quando aplicável.
- Endpoint de chat não aceita URL arbitrária enviada pelo navegador; usa contrato fixo do widget/snippet.
- Pacote de instalação e comando de readiness não expõem secrets, tokens ou credenciais.

## 12. Diferenças entre development, staging e production

| Aspecto | development | staging | production |
|--------|-------------|---------|------------|
| Origins HTTP | permitidas com aviso | aviso recomendando HTTPS | aviso recomendando HTTPS |
| localhost | permitido com `DEBUG=True` e policy explícita | evitar | evitar |
| IA real | desligada por default | desligada por default | somente quando explicitamente habilitada |
| Integrações CRM/e-mail/webhooks | dry-run / off | dry-run / off | conforme governança operacional |
| Snippet/base URL | pode usar host local em testes | URL pública de staging | URL pública de produção |

## Documentos relacionados

- `docs/widget_installation.md` — visão anterior do pacote `/install/`
- `docs/widget_embed.md` — embed e endpoints públicos
- `docs/widget_origin_security.md` — detalhes de validação de origin
- `docs/widget_customization.md` — personalização visual por tenant
