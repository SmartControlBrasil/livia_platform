# Fase 17 — Relatório Final

---

## Resposta à pergunta de evolução

| Antes (Fase 16) | Agora (Fase 17) |
|---|---|
| “O RAG funciona?” | “O RAG se mantém confiável em uso contínuo?” |
| Validação pontual (health, eval, smoke) | **32 interações** multi-conversa, multi-turn, idempotência, isolamento |
| Risco `fake` provider documentado | **Guard fail-closed** implementado + testes |
| Métricas fragmentadas | **`rag_operational_report`** consolidado |

---

## 1. Estado inicial

Fase 16: GO + staging contínuo GP autorizado. Índice saudável, baseline retrieval, pipeline E2E validado.

---

## 2. Configuração usada

Staging-like local, GP-only via allowlists. Sem deploy staging/produção.

---

## 3. Proteção contra provider fake

Implementada em `load_embedding_config()` — 4 testes novos, 508 testes totais verdes.

---

## 4. Soak test

`scripts/phase17_staging_soak.py` — **32 interações, 0 falhas críticas**.  
Detalhes: `docs/phase17_staging_run_report.md`, dados: `docs/phase17_soak_results.json`.

---

## 5. Multi-turn RAG

Follow-ups vagos pós-KB → retrieval empty + fallback seguro. Instrução do usuário (“obras levam 48h”) **não** vira evidência.

---

## 6. Consultas compostas

`cmp-orc-entrega`: partial evidence + grounded — seguro e informativo.

---

## 7. OpenAI failures

Fallback determinístico coberto por testes unitários; pipeline não quebra.

---

## 8. Latência

E2E median **2025 ms**, p95 **2687 ms**. Replay idempotente **17 ms**.

---

## 9. Usage/tokens

Não persistido — gap documentado.

---

## 10. Observabilidade

Novo `rag_operational_report`; gaps portal/tokens para fase posterior.

---

## 11. Tenant isolation

GP grounded; outro tenant bloqueado (403 origin / gates). **0 cross-tenant.**

---

## 12. Idempotência

Replay preserva reply e contagem de mensagens; OpenAI não reexecutado indevidamente.

---

## 13. Discovery

KB durante discovery não interrompe state machine.

---

## 14. Handoff

Transport real desativado; texto adversarial não cria handoff.

---

## 15. CRM dry-run

Código suporta dry-run; **`.env` local pode ter `SMART360_LEAD_DISPATCH_DRY_RUN=False`** — corrigir antes de staging real.

---

## 16. Segurança

Injection, prompt extraction, tenant override, fake deadline multi-turn — comportamento fail-closed no soak.

---

## 17. Testes automatizados

| Suíte | Resultado |
|---|---|
| SQLite | 508 OK, 11 skipped |
| PostgreSQL | 508 OK, 2 skipped |
| Guard fake | 4 OK |
| pgvector + evidence (Fase 16) | 27 OK |

Skips: fixtures/env-specific (documentados em suítes anteriores).

---

## 18. Riscos restantes

**Críticos para piloto produção:**

1. Validação apenas **staging-like local** — sem staging físico dedicado
2. CRM dry-run não garantido no `.env` atual
3. Tokens/custo não monitoráveis por tenant

**Não críticos:**

- Follow-up multi-turn vago → fallback genérico (seguro, UX melhorável)
- Portal sem dashboard RAG
- `evidence_status` só em logs

---

## 19. Critérios Fase 17 (gate Fase 18)

| Critério | Status |
|---|---|
| vector health estável | ✓ OK |
| nenhum vazamento cross-tenant | ✓ |
| state machine preservada | ✓ |
| partial evidence seguro | ✓ |
| OpenAI failure fallback | ✓ (tests) |
| idempotency preservada | ✓ |
| feature gates GP-only | ✓ |
| multi-turn grounded seguro | ✓ |
| latência conhecida | ✓ |
| observabilidade mínima | ✓ (`rag_operational_report`) |
| soak sem falha crítica | ✓ |
| suíte automatizada verde | ✓ |

---

## 20. Veredito final

```text
FASE 17 CONCLUÍDA — GO CONDICIONAL
```

Condições do GO condicional:

- Executar mesmo soak em **staging físico** quando disponível
- Forçar `SMART360_LEAD_DISPATCH_DRY_RUN=True` no ambiente de staging
- Manter `LIVIA_RAG_EMBEDDING_PROVIDER=openai` em operação

```text
PILOTO DE PRODUÇÃO GP: NÃO AUTORIZADO
```

Motivo: uso contínuo confiável **demonstrado localmente**, mas piloto produção exige staging real validado + CRM dry-run garantido + monitoramento de tokens. Nenhum rollout produção nesta fase.

---

## Anexos

- `docs/phase17_staging_run_report.md`
- `docs/phase17_observability_report.md`
- `docs/phase17_soak_results.json`
- Código: `knowledge_base/rag/embeddings.py`, `knowledge_base/management/commands/rag_operational_report.py`, `scripts/phase17_staging_soak.py`

Sem commit / push (conforme instrução).
