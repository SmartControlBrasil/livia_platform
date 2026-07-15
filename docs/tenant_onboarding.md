# Onboarding de tenants da Lívia Platform

A Fase 19 cria um fluxo operacional para instalar a Lívia em múltiplos sites sem depender de cadastro manual em vários pontos. A plataforma continua independente em livia.smartcontrolbrasil.com.br e este fluxo não ativa IA real, dispatch real ou notificações reais por conta própria.

## Criar tenant pelo admin

1. Acesse /admin/ com usuário staff/superuser.
2. Crie o registro em Tenants > Tenants com name, slug, domain e is_active=True.
3. Crie ou revise Tenants > Assistant profiles para o tenant.
4. Cadastre conteúdo inicial em Knowledge base > Knowledge documents.
5. Copie o snippet exibido no detalhe do tenant ou gere pelo comando abaixo.

## Criar tenant pelo comando

Use onboard_tenant para criar ou atualizar tenant, profile, knowledge inicial opcional e snippet do widget.

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain "https://www.granimarmorespitondo.com.br" \
  --assistant-name "Lívia" \
  --initial-message "Olá, eu sou a Lívia da Granimármores Pitondo. Posso te ajudar com orçamentos, materiais, medidas e atendimento comercial." \
  --primary-goal "Qualificar oportunidades comerciais para marmoraria" \
  --tone "consultivo, direto e profissional" \
  --seed-knowledge
~~~

Use --dry-run para conferir o resultado sem gravar nada no banco.

## Exemplos

### Smart Control Brasil

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug smart-control-brasil \
  --name "Smart Control Brasil" \
  --domain "https://www.smartcontrolbrasil.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar oportunidades comerciais e técnicas" \
  --tone "consultivo, claro e profissional" \
  --seed-knowledge
~~~

### Granimármores Pitondo

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain "https://www.granimarmorespitondo.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar oportunidades comerciais para marmoraria" \
  --tone "consultivo, direto e profissional" \
  --seed-knowledge
~~~

### Caneca de Garagem

~~~bash
.venv/bin/python manage.py onboard_tenant \
  --slug canecadegaragem \
  --name "Caneca de Garagem" \
  --domain "https://www.canecadegaragem.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar contatos e pedidos comerciais" \
  --tone "cordial, direto e profissional" \
  --seed-knowledge
~~~

## Configurar origins permitidas

O comando normaliza --domain e imprime o Allowed origin. Quando CORS estiver restrito, inclua esse valor em LIVIA_ALLOWED_WIDGET_ORIGINS, separado por vírgula quando houver mais de um site.

Exemplo:

~~~env
LIVIA_ALLOWED_WIDGET_ORIGINS=https://www.smartcontrolbrasil.com.br,https://www.granimarmorespitondo.com.br
~~~

Domínios sem esquema recebem https:// por padrão. Domínios http:// ou localhost geram alertas para evitar uso acidental em produção.

## Copiar snippet para o site

O comando imprime um snippet neste formato:

~~~html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="granimarmores-pitondo"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/">
</script>
~~~

Cole o snippet no site do cliente, preferencialmente antes de </body>. O data-tenant precisa bater com o slug cadastrado e ativo.

## Validar widget e API

1. Abra https://livia.smartcontrolbrasil.com.br/widget.js e confirme que o JavaScript responde.
2. No site do cliente, abra o console do navegador e confirme que o script carregou sem bloqueio de CORS.
3. Envie uma mensagem de teste pelo widget e confira /api/chat/ no Network.
4. No admin, consulte Conversations > Conversations e Conversations > Messages para confirmar que a conversa foi registrada no tenant correto.

## Ativar IA por tenant com segurança

O onboarding pode gravar AssistantProfile.use_ai=True com --use-ai, mas isso não ativa IA real sozinho. A camada de IA só roda quando as duas condições forem verdadeiras:

1. LIVIA_AI_ENABLED=True globalmente.
2. AssistantProfile.use_ai=True no tenant.

Para validação operacional, mantenha LIVIA_AI_ENABLED=False ou LIVIA_AI_DRY_RUN=True até concluir os testes. Não preencha ou habilite LIVIA_OPENAI_API_KEY em produção sem janela de validação.

## Rodar seed de knowledge

Use --seed-knowledge no onboarding para criar um documento base:

- título: Sobre {name};
- source_type: manual;
- tags: institucional, onboarding;
- status ativo.

O seed é idempotente: repetir o comando atualiza o documento base sem duplicar. Depois do onboarding, complete a base com informações reais do cliente antes de depender do retrieval em atendimento real.
