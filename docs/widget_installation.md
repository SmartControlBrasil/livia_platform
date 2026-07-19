# Instalação do widget da Lívia

A Fase 23 adiciona um pacote público/semipúblico de instalação por tenant, sem dashboard custom e sem expor secrets.

## Página de instalação

Acesse:

~~~text
https://livia.smartcontrolbrasil.com.br/install/<tenant_slug>/
~~~

Exemplos:

~~~text
https://livia.smartcontrolbrasil.com.br/install/smart-control-brasil/
https://livia.smartcontrolbrasil.com.br/install/granimarmores-pitondo/
https://livia.smartcontrolbrasil.com.br/install/caneca-de-garagem/
~~~

A página mostra status do tenant, origins autorizadas, configurações visuais atuais, snippet do widget, instruções curtas e um exemplo de curl para validar a API.

## Pacote JSON

Acesse:

~~~text
https://livia.smartcontrolbrasil.com.br/install/<tenant_slug>.json
~~~

Exemplo de resposta:

~~~json
{
  "tenant": "granimarmores-pitondo",
  "name": "Granimármores Pitondo",
  "is_active": true,
  "domain": "https://www.granimarmorespitondo.com.br",
  "widget_src": "https://livia.smartcontrolbrasil.com.br/widget.js",
  "api_url": "https://livia.smartcontrolbrasil.com.br/api/chat/",
  "snippet": "<script\n  src=\"https://livia.smartcontrolbrasil.com.br/widget.js\"\n  data-tenant=\"granimarmores-pitondo\"\n  data-api-url=\"https://livia.smartcontrolbrasil.com.br/api/chat/\">\n</script>",
  "allowed_origin": "https://www.granimarmorespitondo.com.br",
  "warnings": [],
  "widget_config": {
    "tenant": "granimarmores-pitondo",
    "assistant_name": "Lívia",
    "widget_title": "Lívia",
    "launcher_label": "Fale com a Lívia",
    "initial_message": "Olá! Sou a Lívia. Como posso te ajudar?",
    "primary_color": "#2563eb",
    "position": "bottom_right",
    "placeholder_text": "Digite sua mensagem...",
    "show_branding": true,
    "is_widget_enabled": true
  }
}
~~~

O JSON não inclui tokens, secrets, API keys, webhooks ou configurações internas sensíveis. O bloco `widget_config` contém apenas campos públicos seguros para renderização do widget.

## Como instalar no site

1. Abra a página `/install/<tenant_slug>/`.
2. Copie o snippet do widget.
3. Cole antes de `</body>` no site do tenant.
4. Publique o site.
5. Abra a página publicada e envie uma mensagem curta de teste.

Snippet padrão:

~~~html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="granimarmores-pitondo"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/">
</script>
~~~

## Como testar

Validar carregamento do widget:

~~~bash
curl -i https://livia.smartcontrolbrasil.com.br/widget.js
~~~

Validar API:

~~~bash
curl -i https://livia.smartcontrolbrasil.com.br/api/chat/ \
  -H "Content-Type: application/json" \
  -d "{\"tenant\":\"granimarmores-pitondo\",\"session_id\":\"install-test\",\"message\":\"Olá\"}"
~~~

Validar pacote JSON:

~~~bash
curl -i https://livia.smartcontrolbrasil.com.br/install/granimarmores-pitondo.json
~~~

## Problemas comuns

### Tenant inativo

A página mostra aviso de tenant inativo. Ative o tenant no Django Admin em Tenants > Tenants. Enquanto `is_active=False`, `/api/chat/` retorna resposta controlada e não processa atendimento.

### Origin bloqueado

O origin do site precisa estar cadastrado em `TenantAllowedOrigin` exatamente com o esquema correto, por exemplo `https://www.granimarmorespitondo.com.br`. A lista global antiga não autoriza tenants nesta fase.

### Domínio errado

Corrija o campo `domain` do tenant no admin ou rode novamente o onboarding do tenant. O pacote normaliza domínio sem esquema assumindo `https://`.

### widget.js não carrega

Verifique se `https://livia.smartcontrolbrasil.com.br/widget.js` responde 200 e se o site não bloqueia scripts externos por política de segurança própria.

### API retorna erro

Confira se o snippet usa `data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"`, se o tenant está ativo e se a mensagem de teste não está vazia, longa demais ou bloqueada por rate limit.

## Personalização visual

Veja `docs/widget_customization.md` para campos disponíveis, defaults, endpoint `/api/widget/config/` e exemplos de onboarding com aparência por tenant.
