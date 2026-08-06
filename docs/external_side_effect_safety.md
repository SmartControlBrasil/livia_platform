# External Side Effect Safety

Este documento define os gates operacionais para impedir disparo acidental de integrações externas.

## Objetivo

Garantir comportamento **fail-closed** para side effects externos:

- OpenAI chat
- OpenAI embeddings
- Google Drive sync
- Smart360 lead dispatch
- Webhook delivery
- Email notification
- WhatsApp handoff (backend)

## Serviço central

Arquivo: `integrations/side_effect_policy.py`

Contrato:

- `evaluate_side_effect_policy(...)` retorna:
  - `BLOCKED`
  - `DRY_RUN`
  - `REAL_ENABLED`
- `log_side_effect_decision(...)` registra evento estruturado seguro:
  - `tenant_id`
  - `integration`
  - `decision`
  - `code`
  - `environment`
  - `correlation_id`
  - `conversation_id`
  - `lead_id`

Sem secrets, sem payload integral, sem telefone completo.

## Matriz de integrações e gates

| Componente | Gate principal | Dry-run | Real |
|---|---|---|---|
| `OPENAI_CHAT` | `LIVIA_AI_ENABLED` | `LIVIA_AI_DRY_RUN=True` | requer `LIVIA_AI_DRY_RUN=False` |
| `OPENAI_EMBEDDING` | `LIVIA_RAG_ENABLED` + provider openai | n/a (provider chama API quando ativo) | requer API key e RAG ativo |
| `GOOGLE_DRIVE_SYNC` | service account + comandos RAG | n/a | depende de comando/worker |
| `SMART360_LEAD_DISPATCH` | `SMART360_LEAD_DISPATCH_ENABLED` | `SMART360_LEAD_DISPATCH_DRY_RUN=True` | exige `SMART360_LEAD_DISPATCH_REAL_ENABLED=True` + env/tenant permitidos + config |
| `WEBHOOK_DELIVERY` | `LIVIA_WEBHOOKS_ENABLED` | `LIVIA_WEBHOOKS_DRY_RUN=True` | exige `LIVIA_WEBHOOKS_REAL_ENABLED=True` + env/tenant permitidos |
| `EMAIL_NOTIFICATION` | `LIVIA_*_NOTIFICATIONS_ENABLED` | `LIVIA_*_DRY_RUN=True` | apenas quando dry-run desligado |
| `WHATSAPP_HANDOFF` | backend não envia mensagem | n/a | bloqueado no backend (link client-side) |

## Novos defaults de segurança

Sem alterar `.env`, os defaults em código e `.env.example` permanecem fail-closed:

- `SMART360_LEAD_DISPATCH_REAL_ENABLED=False`
- `SMART360_LEAD_DISPATCH_REAL_ALLOWED_ENVS=production`
- `SMART360_LEAD_DISPATCH_REAL_TENANT_ALLOWLIST=`
- `LIVIA_WEBHOOKS_REAL_ENABLED=False`
- `LIVIA_WEBHOOKS_REAL_ALLOWED_ENVS=production`
- `LIVIA_WEBHOOKS_REAL_TENANT_ALLOWLIST=`

## Comando de readiness por tenant

```bash
.venv/bin/python manage.py tenant_side_effect_readiness --tenant=<slug>
.venv/bin/python manage.py tenant_side_effect_readiness --tenant=<slug> --json
```

Critério:

- `SAFE`: nenhum side effect em `REAL_ENABLED`.
- `UNSAFE`: existe pelo menos um side effect em `REAL_ENABLED`.

Para a fase comercial inicial da Granimármores, `UNSAFE` deve bloquear avanço.

## Smoke local seguro

```bash
.venv/bin/python manage.py tenant_chat_smoke \
  --tenant=granimarmores-pitondo \
  --scenario=commercial
```

Comportamento:

- usa client interno do Django;
- rollback por padrão;
- bloqueia chamadas externas por patch defensivo;
- valida fluxo chat → qualificação → lead → handoff → outbox;
- permite `--persist` apenas quando explicitamente solicitado.

## Diferença entre estados

- `BLOCKED`: execução externa não permitida.
- `DRY_RUN`: caminho permitido, sem request externo real.
- `REAL_ENABLED`: execução externa real autorizada (nesta fase, tratado como inseguro para Granimármores).

## Como habilitar no futuro (controlado)

1. Validar readiness do tenant.
2. Habilitar explicitamente `*_REAL_ENABLED=True`.
3. Restringir por `*_REAL_ALLOWED_ENVS` e `*_REAL_TENANT_ALLOWLIST`.
4. Manter observabilidade estruturada.
5. Executar smoke em staging antes de produção.

## Rollback operacional

Se detectar risco:

1. Voltar `*_REAL_ENABLED=False`.
2. Forçar `*_DRY_RUN=True`.
3. Revalidar com `tenant_side_effect_readiness`.
4. Suspender execução de workers que processam side effects externos.

## Regra desta fase

Não alterar `.env` nem habilitar integrações reais para smoke comercial local.
