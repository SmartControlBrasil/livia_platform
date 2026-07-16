# Personalização do widget por tenant

A Fase 24 adiciona personalização visual e comportamental básica do widget usando campos seguros do `AssistantProfile`. O snippet instalado continua igual: o `widget.js` lê `data-tenant`, busca a configuração pública e aplica os valores em runtime.

## Campos disponíveis

- `widget_title`: título do cabeçalho. Se vazio, usa o nome do perfil.
- `launcher_label`: texto do botão lançador. Default: `Fale com a Lívia`.
- `initial_message`: primeira mensagem exibida no chat. Já existia no perfil.
- `primary_color`: cor principal do widget em hex `#RGB` ou `#RRGGBB`. Default: `#2563eb`.
- `position`: `bottom_right` ou `bottom_left`. Default: `bottom_right`.
- `show_branding`: mostra ou oculta o rodapé `Atendimento por Lívia`. Default: `True`.
- `collect_contact_hint`: campo reservado para dica operacional de coleta de contato. Ainda não é exposto no endpoint público.
- `placeholder_text`: placeholder do input. Default: `Digite sua mensagem...`.
- `is_widget_enabled`: liga/desliga o widget do tenant. Default: `True`.

## Configuração pelo admin

No Django Admin, abra `Tenants > Assistant profiles`. A seção `Widget` permite editar título, label, cor, posição, branding, placeholder e status do widget.

A validação básica rejeita cores fora de `#RGB` ou `#RRGGBB` e posições diferentes de `bottom_right` ou `bottom_left`.

## Configuração pelo onboarding

Exemplo:

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug smart-control-brasil \
  --name "Smart Control Brasil" \
  --domain smartcontrolbrasil.com.br \
  --widget-title "Lívia Smart Control" \
  --launcher-label "Fale com a Lívia" \
  --primary-color "#2563eb" \
  --position bottom_right \
  --placeholder-text "Digite sua dúvida..."
~~~

Para desativar o widget durante o onboarding:

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug caneca-de-garagem \
  --name "Caneca de Garagem" \
  --domain canecadegaragem.com.br \
  --disable-widget
~~~

## Endpoint público de configuração

O widget busca:

~~~text
GET /api/widget/config/?tenant=<tenant_slug>
~~~

Exemplo de resposta:

~~~json
{
  "tenant": "smart-control-brasil",
  "assistant_name": "Lívia",
  "widget_title": "Lívia Smart Control",
  "launcher_label": "Fale com a Lívia",
  "initial_message": "Olá! Sou a Lívia da Smart Control Brasil. Como posso ajudar?",
  "primary_color": "#2563eb",
  "position": "bottom_right",
  "placeholder_text": "Digite sua dúvida...",
  "show_branding": true,
  "is_widget_enabled": true
}
~~~

O endpoint não expõe tokens, secrets, webhooks, chaves de API ou campos internos sensíveis. Se o tenant não existir, estiver inativo ou não tiver profile, a resposta é controlada com `is_widget_enabled: false`.

## Fallback do widget

Se a busca de configuração falhar, o widget continua carregando com defaults locais:

- título `Lívia`;
- botão `Fale com a Lívia`;
- cor `#2563eb`;
- posição `bottom_right`;
- placeholder `Digite sua mensagem...`;
- branding visível;
- widget habilitado.

## Exemplos iniciais

Smart Control Brasil:

~~~bash
.venv/bin/python manage.py onboard_tenant --slug smart-control-brasil --name "Smart Control Brasil" --domain smartcontrolbrasil.com.br --widget-title "Lívia Smart Control" --launcher-label "Fale com a Lívia" --primary-color "#2563eb" --position bottom_right
~~~

Granimármores Pitondo:

~~~bash
.venv/bin/python manage.py onboard_tenant --slug granimarmores-pitondo --name "Granimármores Pitondo" --domain granimarmorespitondo.com.br --widget-title "Lívia Granimármores" --launcher-label "Solicitar orçamento" --primary-color "#475569" --position bottom_left
~~~

Caneca de Garagem:

~~~bash
.venv/bin/python manage.py onboard_tenant --slug caneca-de-garagem --name "Caneca de Garagem" --domain canecadegaragem.com.br --widget-title "Lívia Caneca de Garagem" --launcher-label "Falar sobre canecas" --primary-color "#0f766e" --position bottom_right
~~~
