# Runbook — Granimármores Pitondo

## 1) Identificação do tenant

- `slug`: `granimarmores-pitondo`
- `nome`: `Granimármores Pitondo`
- `objetivo`: atendimento comercial inicial, qualificação e handoff para humano

## 2) Domínio principal

- Principal documentado: `https://www.granimarmorespitondo.com.br`
- Alternativo atualmente permitido no ambiente local: `https://granimarmorespitondo.com.br`

## 3) Origins esperadas

- `https://www.granimarmorespitondo.com.br`
- `https://granimarmorespitondo.com.br` (somente se o site também responder nesse host)

Observações:

- Sem wildcard.
- Sem path, query, fragment ou credenciais.
- Porta só quando necessária e explicitamente cadastrada.

## 4) Comando de onboarding (modo seguro)

Primeiro simule:

```bash
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain https://www.granimarmorespitondo.com.br \
  --assistant-name "Lívia Granimármores" \
  --tone "Profissional, acessível e objetiva, em português brasileiro." \
  --primary-goal "Qualificar oportunidades comerciais de marmoraria e encaminhar orçamento para atendimento humano." \
  --initial-message "Olá! Sou a Lívia da Granimármores Pitondo. Posso ajudar com seu projeto de mármore ou granito e encaminhar seu orçamento com nosso atendimento." \
  --allowed-origin https://www.granimarmorespitondo.com.br \
  --allowed-origin https://granimarmorespitondo.com.br \
  --dry-run
```

Depois aplique:

```bash
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain https://www.granimarmorespitondo.com.br \
  --assistant-name "Lívia Granimármores" \
  --tone "Profissional, acessível e objetiva, em português brasileiro." \
  --primary-goal "Qualificar oportunidades comerciais de marmoraria e encaminhar orçamento para atendimento humano." \
  --initial-message "Olá! Sou a Lívia da Granimármores Pitondo. Posso ajudar com seu projeto de mármore ou granito e encaminhar seu orçamento com nosso atendimento." \
  --allowed-origin https://www.granimarmorespitondo.com.br \
  --allowed-origin https://granimarmorespitondo.com.br \
  --apply \
  --allow-update-existing
```

## 5) Comando de readiness

```bash
.venv/bin/python manage.py tenant_site_readiness --tenant=granimarmores-pitondo
.venv/bin/python manage.py tenant_site_readiness --tenant=granimarmores-pitondo --json
```

## 5.1) Side effect readiness

```bash
.venv/bin/python manage.py tenant_side_effect_readiness --tenant=granimarmores-pitondo
.venv/bin/python manage.py tenant_side_effect_readiness --tenant=granimarmores-pitondo --json
```

Para a fase inicial comercial, o resultado esperado é `OVERALL: SAFE`.

## 6) URL do pacote de instalação

- HTML: `/install/granimarmores-pitondo/`
- JSON: `/install/granimarmores-pitondo.json`

## 7) Snippet oficial

```html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="granimarmores-pitondo"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/"
  defer>
</script>
```

## 8) Local recomendado no HTML

- Inserir imediatamente antes de `</body>`.

## 9) Smoke antes da publicação

1. Validar `/install/granimarmores-pitondo/` com status de readiness aceitável.
2. Validar `/api/widget/config/?tenant=granimarmores-pitondo` com origin permitida.
3. Validar bloqueio com origin não autorizada.
4. Validar chat com `request_id` e replay idempotente.
5. Executar smoke local com rollback:

```bash
.venv/bin/python manage.py tenant_chat_smoke \
  --tenant=granimarmores-pitondo \
  --scenario=commercial
```

## 10) Smoke após a publicação

1. Abrir o site real na origin cadastrada.
2. Confirmar carregamento visual do widget.
3. Enviar mensagem comercial curta.
4. Solicitar handoff humano e confirmar resposta apropriada.

## 11) Sinais esperados nos logs

- Bloqueio de origin inválida: evento `livia_public_origin_blocked`.
- Processamento de request idempotente: eventos `livia_chat_request_reserved` e `livia_chat_request_completed`.

## 12) Como confirmar criação da conversa

- Conferir `Conversation` para `tenant=granimarmores-pitondo`.
- Conferir `Message` vinculada à conversa.

## 13) Como confirmar `LeadDraft`

- Verificar `LeadDraft` para o tenant após intenção comercial clara.
- Confirmar que retry com mesmo `request_id` não duplica lead.

## 14) Como confirmar handoff

- Verificar `HandoffRequest` para o tenant após pedido de atendimento humano.
- Confirmar que o payload inclui contexto conversacional suficiente.

## 15) Rollback imediato

1. Remover snippet do HTML.
2. Publicar novamente o site.
3. Validar ausência do widget na página.

## 16) Remoção do snippet

- Remover apenas o bloco `<script ...></script>` da integração.

## 17) Desativação do tenant

- Admin: `Tenant.is_active=False` para bloquear atendimento imediatamente.

## 18) Desativação da origin

- Admin: `TenantAllowedOrigin.is_active=False` para bloquear origem específica.

## 19) Riscos e cuidados

- Não habilitar integrações externas reais durante validação local.
- Não alterar `.env` nem segredos.
- Evitar sobrescrever campos de perfil não intencionais.
- Verificar diferença entre host `www` e host raiz antes de reduzir a lista de origins.
- Tratar qualquer status `REAL_ENABLED` em `tenant_side_effect_readiness` como bloqueador para esta fase.

## 20) Dependências ainda de staging/produção

- Confirmação do host canônico final em produção (`www` vs raiz) e política de redirecionamento no servidor web.
- Smoke end-to-end no ambiente de staging com domínio final publicado.

## Apêndice — Matriz de avaliação comercial

| Pergunta | Comportamento esperado |
|---|---|
| Quais tipos de projetos vocês fazem? | `ANSWER` |
| Vocês trabalham com cozinhas? | `ANSWER` |
| Como solicito um orçamento? | `QUALIFY` |
| Onde fica a empresa? | `ANSWER` |
| Preciso ter as medidas para pedir orçamento? | `QUALIFY` |
| Vocês podem informar o preço agora? | `HANDOFF` |
| Quero falar com um atendente. | `HANDOFF` |
| Vocês trabalham com material X não documentado? | `INSUFFICIENT_EVIDENCE` |
| Atendem cidade Y não documentada? | `INSUFFICIENT_EVIDENCE` |
| Qual é o prazo de instalação? | `HANDOFF` |
