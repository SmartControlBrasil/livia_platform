# Onboarding de tenants da Lívia Platform

O fluxo operacional instala a Lívia em múltiplos sites sem depender de cadastro manual em vários pontos. A plataforma continua independente em livia.smartcontrolbrasil.com.br e este fluxo não ativa IA real, dispatch real, Google Drive real ou webhooks reais por conta própria.

## Serviço central

`TenantOnboardingService` é a fonte única para provisionamento comercial. O comando `tenant_onboard` (`onboard_tenant` continua disponível como alias histórico) e a criação pelo `/painel/` convergem para esse serviço. O serviço executa a operação dentro de `transaction.atomic()`, normaliza origins com as mesmas regras públicas do widget, produz status `CREATED`, `UPDATED` ou `UNCHANGED`, retorna readiness, install package e deltas `origins_added`, `origins_existing` e `origins_removed`, e registra auditoria de tenant, origins e conclusão do onboarding.

Defaults seguros para novos tenants:

- `use_ai=False`;
- `widget_enabled=False`, exceto quando habilitado explicitamente;
- side effects reais continuam desabilitados por configuração global;
- origins root e www precisam ser informadas explicitamente, uma por uma.

## Criar tenant pelo portal

1. Acesse `/painel/tenants/` com permissão de gestão.
2. Use `Novo tenant`.
3. Informe slug, nome, domínio/origins e configurações públicas do widget.
4. Salve; o portal chama o mesmo `TenantOnboardingService` usado pelo comando.
5. Abra o detalhe do tenant para consultar readiness, snippet e package de instalação.

## Criar tenant pelo comando

Use `tenant_onboard` para criar ou atualizar tenant, profile, origins, readiness, install package e snippet do widget. O comando antigo `onboard_tenant` permanece compatível.

Modo de execução explícito:

- `--dry-run`: simula sem gravar.
- `--apply`: grava alterações no banco.
- `--allow-update-existing`: obrigatório junto com `--apply` quando o slug já existe.
- `--origin`: pode ser repetido; alias de `--allowed-origin`. Use root e www separadamente quando ambos forem necessários.

~~~bash
.venv/bin/python manage.py tenant_onboard \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --origin "https://granimarmorespitondo.com.br" \
  --origin "https://www.granimarmorespitondo.com.br" \
  --assistant-name "Lívia" \
  --initial-message "Olá, eu sou a Lívia da Granimármores Pitondo. Posso te ajudar com orçamentos, materiais, medidas e atendimento comercial." \
  --primary-goal "Qualificar oportunidades comerciais para marmoraria" \
  --tone "consultivo, direto e profissional" \
  --seed-knowledge \
  --dry-run
~~~

Aplicação explícita:

~~~bash
.venv/bin/python manage.py tenant_onboard \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --origin "https://granimarmorespitondo.com.br" \
  --origin "https://www.granimarmorespitondo.com.br" \
  --assistant-name "Lívia" \
  --initial-message "Olá, eu sou a Lívia da Granimarmores Pitondo. Posso te ajudar com orçamentos, materiais, medidas e atendimento comercial." \
  --primary-goal "Qualificar oportunidades comerciais para marmoraria" \
  --tone "consultivo, direto e profissional" \
  --seed-knowledge \
  --apply \
  --allow-update-existing
~~~

## Exemplos

### Smart Control Brasil

~~~bash
.venv/bin/python manage.py tenant_onboard \
  --slug smart-control-brasil \
  --name "Smart Control Brasil" \
  --domain "https://www.smartcontrolbrasil.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar oportunidades comerciais e técnicas" \
  --tone "consultivo, claro e profissional" \
  --seed-knowledge \
  --dry-run
~~~

### Granimármores Pitondo

~~~bash
.venv/bin/python manage.py tenant_onboard \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --origin "https://granimarmorespitondo.com.br" \
  --origin "https://www.granimarmorespitondo.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar oportunidades comerciais para marmoraria" \
  --tone "consultivo, direto e profissional" \
  --seed-knowledge \
  --dry-run
~~~

### Caneca de Garagem

~~~bash
.venv/bin/python manage.py tenant_onboard \
  --slug canecadegaragem \
  --name "Caneca de Garagem" \
  --domain "https://www.canecadegaragem.com.br" \
  --assistant-name "Lívia" \
  --primary-goal "Qualificar contatos e pedidos comerciais" \
  --tone "cordial, direto e profissional" \
  --seed-knowledge \
  --dry-run
~~~

## Configurar origins permitidas

O comando normaliza `--domain` e pode criar origins explícitas com `--allowed-origin`. `TenantAllowedOrigin` é a fonte principal de autorização do widget; a lista global antiga não deve ser usada como fonte de permissão por tenant.

Exemplo:

~~~bash
.venv/bin/python manage.py tenant_onboard --slug exemplo --name "Exemplo" --origin https://exemplo.com.br --origin https://www.exemplo.com.br --dry-run
~~~

O `--domain` continua aceito e é normalizado para uma origin base. Domínios sem esquema recebem https:// por padrão. Origins com wildcard, path, query string, fragmento ou scheme inválido são rejeitadas. Não há equivalência automática entre root e www.

## Copiar snippet para o site

O comando imprime um snippet neste formato:

~~~html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="granimarmores-pitondo"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"
  defer>
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


## Resultado operacional

O comando imprime `Status`, `Readiness`, `Knowledge`, origins normalizadas, deltas de origins adicionadas/existentes/desativadas, alertas e snippet. `--dry-run` valida e calcula o resultado sem persistir `Tenant`, `AssistantProfile`, origins, knowledge ou audit events de conclusão.

Readiness e install package são gerados pelos serviços existentes `inspect_tenant_site_readiness` e `TenantInstallPackageService`; não há formato paralelo de snippet.
