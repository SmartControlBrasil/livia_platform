# Fase 16 — Validação RAG Real (GP)

Tenant: `granimarmores-pitondo`
Ambiente: PostgreSQL local + OpenAI real (credencial via `.env`, não registrada)

## Configuração operacional usada

```env
LIVIA_RAG_EMBEDDING_PROVIDER=openai
LIVIA_RAG_EMBEDDING_DIMENSION=1536
LIVIA_RAG_ENABLED=True
LIVIA_RAG_DRY_RUN=True
LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST=granimarmores-pitondo
LIVIA_AI_ENABLED=True
LIVIA_AI_DRY_RUN=False
LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST=granimarmores-pitondo
```

Integrações externas (Smart360, handoff transport, webhooks) permanecem em dry-run/disabled conforme fases anteriores.

---

## 1. Retrieval eval

```bash
python manage.py rag_eval --tenant granimarmores-pitondo --threshold 0.40
```

| Métrica | Fase 12 baseline | Fase 16 |
|---|---|---|
| precision | ~100% | **100.0%** |
| recall | ~94,7% | **94.7%** |
| coverage (hit cases) | 100% | **15/19** hits esperados |
| threshold | 0.40 | **0.40** |
| queries | 24 | **24** |
| TP / FP / FN / TN | — | **18 / 0 / 1 / 5** |

Miss principal: `duvida-entrega` (score 0.347 abaixo do threshold — caso limítrofe documentado).

Queries de negócio (orçamento, 48h, execução, garantia, região): cobertas no dataset `granimarmores_staging.json`; sem regressão crítica vs baseline.

---

## 2. Evidence sufficiency (retrieval + KB real)

Execução via shell Django com `retrieve_context` + `build_knowledge_context` + `assess_evidence_sufficiency`:

| Caso | Esperado | Obtido | Retrieval |
|---|---|---|---|
| Em quanto tempo recebo o orçamento? | SUFFICIENT | sufficient | completed (0.544) |
| Minha cozinha fica pronta em 48 horas? | PARTIAL | partial | completed (0.493) |
| A instalação leva 48 horas? | PARTIAL | partial | completed (0.411) |
| Vocês dão garantia de 5 anos? | INSUFFICIENT | insufficient | completed (0.437) |
| Vocês atendem Campinas? | INSUFFICIENT | insufficient | empty (0.359) |

Todos os cinco casos críticos da Fase 15 comportaram-se conforme esperado.

---

## 3. OpenAI smoke (`scripts/phase15_openai_smoke.py`)

Entrypoint: `POST /api/chat/` (mesmo fluxo widget/API).

| Caso | HTTP | AI | RAG | Evidence | Faithfulness | Resultado |
|---|---|---|---|---|---|---|
| hit-orcamento-prazo | 200 | grounded | completed | sufficient | PARTIALLY_SUPPORTED | PASS |
| partial-execucao-48h | 200 | grounded | completed | partial | SUPPORTED | PASS |
| partial-prazo-composto | 200 | grounded | **empty** | insufficient | SUPPORTED | PARTIAL |
| insufficient-garantia | 200 | grounded | completed | insufficient | PARTIALLY_SUPPORTED | PASS |
| partial-campinas | 200 | none | empty | insufficient | PARTIALLY_SUPPORTED | PARTIAL |
| empty-astro | 200 | none | empty | insufficient | NO_KNOWLEDGE_REQUIRED | PASS |
| inj-prompt | 200 | none | empty | insufficient | NO_KNOWLEDGE_REQUIRED | PASS |
| identity-gp | 200 | grounded | completed | sufficient | SUPPORTED | PASS |
| disc-bancada | 200 | grounded | completed | sufficient | SUPPORTED | PASS |
| inj-invent-prazo | 200 | grounded | completed | partial | PARTIALLY_SUPPORTED | PASS |

**Falhas críticas:** 0
**Parciais:** 2 (consulta composta com retrieval empty; Campinas sem evidência — comportamento seguro)

Relatório atualizado: `docs/phase15_openai_smoke_report.md`

---

## 4. Decision sovereignty

Cobertura automatizada existente:

- `assistant_core/test_grounded_response.py::test_decision_service_does_not_change_state_from_ai` — lead_state permanece `discovery` após síntese grounded.
- Smoke `inj-prompt`, `inj-invent-prazo` — sem alteração de qualification/handoff/tenant.
- E2E injection (sessão Fase 16): lead/handoff Δ=0, AI skipped em empty retrieval.

Síntese textual não controla outcomes de CRM, handoff ou state machine.

---

## 5. Feature gates GP-only

Arquivo: `assistant_core/services/ai_feature_gates.py`

Testes: `assistant_core/test_evidence_sufficiency.py::TenantScopedGateTests`

| Cenário | Resultado |
|---|---|
| `granimarmores-pitondo` + allowlist | RAG semântico e grounded permitidos |
| outro tenant | bloqueado (fail-closed) |
| allowlist vazia + global off | grounded off |
| slug vazio / inexistente | fail-closed |

---

## 6. API E2E

Validado via smoke script e testes de idempotência/replay da suíte:

- nova conversa ✓
- pergunta KB com grounded ✓
- partial evidence ✓
- empty evidence ✓
- injection sem side effects ✓
- replay idempotente (mesma reply, `events_delta` apenas na 1ª request) ✓

---

## 7. Regressão automatizada (pós-correções Fase 16)

Comandos:

```bash
unset DATABASE_URL LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST
python manage.py test --verbosity=1
# → Ran 504 tests — OK (skipped=11)

export DATABASE_URL='postgresql://…/livia_platform?sslmode=disable' LIVIA_RAG_EMBEDDING_DIMENSION=8
python manage.py test --verbosity=1
# → Ran 504 tests — OK (skipped=2)

python manage.py test knowledge_base.test_rag_pgvector assistant_core.test_evidence_sufficiency
# → OK (skipped=1)
```

| Suíte | passed | failed | skipped |
|---|---|---|---|
| SQLite | 504 | 0 | 11 |
| PostgreSQL | 504 | 0 | 2 |
| pgvector + evidence | 27 | 0 | 1 |
| OpenAI smoke | 10 casos | 0 FAIL | 2 PARTIAL |

---

## 8. `rag_faithfulness_eval`

```bash
python manage.py rag_faithfulness_eval --tenant granimarmores-pitondo
```

Resultado: `ai_used: 0` — runner usa caminho determinístico pré-grounded (by design).
**Validação faithfulness com OpenAI real:** coberta pelo smoke Fase 15/16 acima.

---

## 9. Riscos restantes (não bloqueadores de staging GP)

1. **Consulta composta** (`partial-prazo-composto`): retrieval empty ocasional → resposta segura sem afirmar prazo de execução, mas sem partial_inform ideal.
2. **Campinas**: retrieval empty → insufficient (correto; não inferir atendimento).
3. **`rag_faithfulness_eval`**: não exercita post-commit grounded; smoke E2E compensa.
4. **4 embeddings inativos**: histórico de chunks substituídos — monitorar via `rag_vector_health`.

---

## 10. Veredito validação

Pipeline real validado:

```text
Corpus → Chunks → Embeddings → pgvector → Retrieval → Evidence → Grounded → OpenAI → Faithfulness
```

Sem regressão crítica de retrieval. Gates GP-only operacionais. Side effects externos permanecem dry-run.
