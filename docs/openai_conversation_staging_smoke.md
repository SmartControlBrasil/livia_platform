# Smoke OpenAI — Staging (NÃO EXECUTAR AINDA)

Preparado para validação futura com `LIVIA_AI_ENABLED=True` e `AssistantProfile.use_ai=True` em tenant de staging.

**Restrições atuais:** não executar; não alterar staging/produção; não expor API keys.

## Pré-requisitos

- Tenant staging com RAG indexado (HygiBot + overview Xyron)
- `LIVIA_AI_ENABLED=True` (somente no ambiente de teste)
- `LIVIA_AI_DRY_RUN=False`
- `LIVIA_OPENAI_API_KEY` configurada no secret manager
- `AssistantProfile.use_ai=True` no tenant de teste
- `grounded_synthesis_enabled` **não** é mais requisito para conversação OpenAI primária

## Sequência de mensagens (mesma sessão)

| # | Mensagem | Esperado |
|---|----------|----------|
| 1 | `preciso de um robo de limpeza` | Resposta natural grounded em HygiBot; sem pedir contato |
| 2 | `um galpão` | Continuidade de memória; pergunta consultiva sobre ambiente |
| 3 | `3000 m2, piso de concreto` | Ack + detalhe técnico grounded |
| 4 | `ele consegue trabalhar com pessoas circulando?` | Resposta direta à pergunta técnica (limites documentados) |
| 5 | `quero um orçamento` | `collection_active=true`; pedido de nome/empresa |
| 6 | `Grupo Mecanismo` | Coleta continua; próximo campo (telefone/e-mail) |
| 7 | `antes, ele consegue aspirar também?` | Pergunta técnica no meio da coleta **não** quebra state |
| 8 | `<telefone válido>` | Coleta retoma após resposta técnica |

## Critérios de aceite

- [ ] `ai_mode=openai_conversation` na resposta quando IA ativa
- [ ] `observability.ai_fallback_used=false` em turnos bem-sucedidos
- [ ] `observability.ai_grounded=true` quando RAG hit
- [ ] `collection_active=false` nos turnos 1–4
- [ ] `collection_active=true` a partir do turno 5
- [ ] Turno 7 responde técnica **e** retoma coleta no turno 8
- [ ] Nenhuma corrupção de `lead_state` / `session_id`
- [ ] Idempotência: replay do mesmo `request_id` retorna mesma resposta

## Comando sugerido (futuro)

```bash
# Substituir BASE_URL, SESSION_ID e ORIGIN conforme staging
SESSION_ID="staging-openai-smoke-$(date +%Y%m%d%H%M%S)"
# Executar POST /api/chat para cada mensagem da tabela acima
```

## Rollback

Desativar `LIVIA_AI_ENABLED` ou `AssistantProfile.use_ai` — fluxo volta ao determinístico + fallback sem deploy de código.
