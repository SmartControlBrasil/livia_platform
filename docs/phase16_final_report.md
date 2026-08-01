# Fase 16 — Relatório Final

Tenant de referência: `granimarmores-pitondo`  
Escopo: restaurar índice vetorial, validar RAG real end-to-end, emitir gate de staging contínuo GP.  
**Sem deploy produção. Sem commit/push.**

---

## 1. Estado inicial

Fase 15 encerrou com:

```text
FASE 15 CONCLUÍDA — GO CONDICIONAL
STAGING CONTÍNUO GP — NÃO AUTORIZADO
```

Bloqueador reportado: `rag_vector_health → REINDEX_REQUIRED`, 19 embeddings incompatíveis, retrieval skipped, grounded synthesis não validada contra KB semântica real.

---

## 2. Causa raiz

Os 19 embeddings **não estavam corrompidos**. Estavam corretos (`openai`, `text-embedding-3-small`, dim=1536). O status `REINDEX_REQUIRED` aparecia quando o health rodava com **`LIVIA_RAG_EMBEDDING_PROVIDER=fake`** (residual de shells de teste PostgreSQL), comparando profile fake contra embeddings OpenAI reais → `wrong_model: 19`.

Com `provider=openai`:

```text
Status: OK
compatible: 19
reindex_required: 0
coverage: 100%
```

Detalhes: `docs/phase16_reindex_report.md`

---

## 3. Corpus GP

| Métrica | Valor |
|---|---|
| Manifests Drive ativos | 9 |
| Chunks ativos | 19 |
| Embeddings ativos | 19 (+ 4 inativos históricos) |
| Cross-tenant | **0** |
| Pasta aprovada | `1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm` |

Conteúdo exclusivo GP (prefixo `GP —`), sem Smart Control ou outro tenant.

---

## 4. Reindex

Executado:

```bash
python manage.py index_tenant_rag --tenant granimarmores-pitondo --dry-run
```

Resultado: `unchanged=19`, `pending=0` — **reindex real não necessário**.

---

## 5. Vector health (antes / depois)

| Condição | Status | Incompatíveis |
|---|---|---|
| Antes (provider=fake no shell) | REINDEX_REQUIRED | 19 wrong_model |
| Depois (provider=openai) | **OK** | 0 |

---

## 6. Retrieval

```bash
python manage.py rag_eval --tenant granimarmores-pitondo --threshold 0.40
```

| Métrica | Valor |
|---|---|
| precision | 100.0% |
| recall | 94.7% |
| TP / FP / FN / TN | 18 / 0 / 1 / 5 |
| threshold | 0.40 |
| queries | 24 |

Alinhado ao baseline Fase 12. Detalhes: `docs/phase16_rag_validation_report.md`

---

## 7. Evidence sufficiency

Cinco casos críticos Fase 15 validados contra chunks reais — todos OK (sufficient / partial / insufficient conforme esperado).

Correções aplicadas:

- Marcador `"em quanto tempo"` para timeline de orçamento
- Distinção quote vs execução/instalação
- Injeção RAG GP em `LIVIA_RAG_DRY_RUN=True` via allowlist

---

## 8. OpenAI smoke

Script: `scripts/phase15_openai_smoke.py`  
Casos: 10 | **FAIL: 0** | **PARTIAL: 2**

Parciais aceitáveis:

- `partial-prazo-composto` — retrieval empty em consulta composta; resposta segura
- `partial-campinas` — sem evidência de atendimento; fail-closed

Faithfulness: eco de prazo/execução na pergunta do usuário não gera falso positivo após ajuste em `faithfulness.py`.

---

## 9. Decision sovereignty

Confirmado: síntese grounded não altera qualification, lead creation, handoff, tenant, conversation state ou CRM dispatch. Testes automatizados + smoke de injection.

---

## 10. Feature gates

`assistant_core/services/ai_feature_gates.py` — GP allowlisted; demais tenants fail-closed. Testes em `test_evidence_sufficiency.py`.

---

## 11. API E2E

`POST /api/chat/` validado via smoke e testes de replay/idempotência. Pipeline completo: retrieval → evidence → grounded → resposta.

---

## 12. Testes automatizados

| Backend | Resultado |
|---|---|
| SQLite | 504 OK, 11 skipped |
| PostgreSQL | 504 OK, 2 skipped |
| pgvector + evidence | 27 OK, 1 skipped |
| OpenAI smoke | 0 FAIL |

Correção de regressão: testes grounded e dry-run RAG isolados de allowlists do `.env` local.

---

## 13. Riscos restantes

**Críticos:** nenhum bloqueador identificado para staging GP contínuo.

**Não críticos:**

- Consulta composta com retrieval empty ocasional
- `rag_faithfulness_eval` não usa caminho grounded completo (smoke compensa)
- Embeddings inativos históricos (4) — monitorar health

---

## 14. Veredito

Todos os critérios críticos da seção 15 do brief foram atendidos:

- [x] vector health saudável
- [x] corpus GP sem vazamento cross-tenant
- [x] retrieval funcional (baseline mantido)
- [x] partial evidence funcionando
- [x] negação não suportada protegida
- [x] numeric/qualifier mismatch protegido
- [x] injection protegida
- [x] decision sovereignty preservada
- [x] feature gates GP-only
- [x] OpenAI smoke crítico aprovado (0 FAIL)
- [x] API E2E aprovada
- [x] suíte automatizada verde

```text
FASE 16 CONCLUÍDA — GO
```

```text
STAGING CONTÍNUO GP: AUTORIZADO
```

**Não avançar para produção nesta fase** (conforme brief).  
**Não commit / push** (conforme instrução).

---

## Anexos

- `docs/phase16_reindex_report.md`
- `docs/phase16_rag_validation_report.md`
- `docs/phase15_openai_smoke_report.md`

## Comandos executados (referência)

```bash
python manage.py rag_vector_health --tenant granimarmores-pitondo
python manage.py index_tenant_rag --tenant granimarmores-pitondo --dry-run
python manage.py rag_eval --tenant granimarmores-pitondo --threshold 0.40
python scripts/phase15_openai_smoke.py
python manage.py rag_faithfulness_eval --tenant granimarmores-pitondo
python manage.py test --verbosity=1   # SQLite e PostgreSQL
```
