# Fase 15 — Relatório técnico final

## Veredito

```text
FASE 15 CONCLUÍDA — GO CONDICIONAL
STAGING CONTÍNUO GP: NÃO AUTORIZADO
```

**Implementação:** evidence sufficiency, faithfulness, tenant-scoped gates e testes — **aprovados**.

**Operação:** smoke OpenAI real bloqueado por **`REINDEX_REQUIRED`** no PostgreSQL local (`coverage_incompatible_embedding: 19`). Reindex + re-smoke obrigatórios antes de staging contínuo.

---

## 1. Diagnóstico inicial

Ver `docs/phase15_initial_audit.md`.

Pipeline pré-Fase 15: retrieval binário → `DecisionOutcome` → grounded sem classificação de suficiência factual. Partial evidence dependia só de instrução de prompt + LLM.

---

## 2. Causa do NO-GO da Fase 14

Proximidade semântica entre pergunta de **execução/instalação em 48h** e documento de **retorno de orçamento em 48h**. Sem camada determinística, a síntese:

- pedia discovery genérica, ou
- ecoava “48 horas” da pergunta,

violando faithfulness (equivalência factual indevida).

---

## 3. Arquitetura implementada

```text
User message
    ↓
Deterministic decision / state machine
    ↓
DecisionOutcome (operacional)
    ↓
Retrieval → KNOWLEDGE_BASE
    ↓
assess_evidence_sufficiency()  ← NOVO (determinístico)
    ↓
    ├─ SUFFICIENT → synthesis_mode normal
    ├─ PARTIAL → partial_inform + log rag.evidence_partial
    └─ INSUFFICIENT → skip grounded + log rag.evidence_insufficient
    ↓
GroundedResponseService (se permitido)
    ↓
Response (fallback determinístico se grounded skipped)
```

---

## 4. Arquivos criados

| Arquivo | Finalidade |
|---------|------------|
| `docs/phase15_initial_audit.md` | Auditoria pré-implementação |
| `assistant_core/eval/evidence_sufficiency.py` | Modelo e regras de suficiência |
| `assistant_core/services/ai_feature_gates.py` | Gates tenant-scoped RAG/grounded |
| `assistant_core/test_evidence_sufficiency.py` | Testes A–H + gates |
| `scripts/phase15_openai_smoke.py` | Smoke crítico Fase 15 |
| `docs/phase15_openai_smoke_report.md` | Resultado smoke (infra limitada) |
| `docs/phase15_final_report.md` | Este relatório |

---

## 5. Arquivos modificados

| Arquivo | Mudança |
|---------|---------|
| `assistant_core/services/decision_outcome.py` | Campos `evidence_sufficiency`, `evidence_reason` |
| `assistant_core/services/grounded_response.py` | Assessment + logs + modos partial/insufficient |
| `assistant_core/prompts/grounded_ai.py` | EVIDENCE RULES + modos `partial_inform` / `insufficient_safe` |
| `assistant_core/eval/faithfulness.py` | Negation-aware forbidden; ambíguo → PARTIALLY |
| `assistant_core/services/chat_processing.py` | Block rewrite legado via allowlist tenant |
| `knowledge_base/rag/context_builder.py` | RAG context via allowlist em dry_run |
| `knowledge_base/rag/conversation_retrieval.py` | Referência `chunk:{id}` no bloco KB |
| `config/settings.py` | `LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST`, `LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST` |
| `.env.example` | Documentação das allowlists |

---

## 6. Evidence sufficiency

Enum `EvidenceSufficiency`: `sufficient` | `partial` | `insufficient`.

Regras determinísticas (multi-tenant):

- **Quote vs execution:** KB cita prazo de orçamento; pergunta sobre instalação/obras → `PARTIAL`
- **Mesmo número, eixo diferente:** 48h orçamento vs 48h execução → `PARTIAL`
- **Região:** pergunta Campinas, KB só São Paulo → `PARTIAL`
- **Tópico ausente:** garantia não documentada → `INSUFFICIENT` (sem negar)
- **Default com KB:** `SUFFICIENT`

Funções: `assess_evidence_sufficiency()`, `effective_synthesis_mode()`, `parse_chunk_ids_from_context()`.

---

## 7. Partial evidence (runtime)

- `PARTIAL` → `partial_inform` no prompt + `rag.evidence_partial` (tenant, reason, category, chunk_ids, score)
- Resposta deve: afirmar só eixo suportado + declarar limite
- `INSUFFICIENT` → grounded **skipped** (`insufficient_evidence`); mantém reply determinística (fail-closed)

---

## 8. Grounded prompt

Adicionado bloco **EVIDENCE RULES** (8 regras): qualificadores, números, ausência ≠ negação, subordinação ao `DecisionOutcome`.

Modos novos: `partial_inform`, `insufficient_safe`.

---

## 9. Faithfulness

- Forbidden com detecção de **negação** (“não posso revelar system prompt”)
- Eco seguro de “48 horas” em respostas parciais
- `facts_expected=[]` + `require_knowledge` → **PARTIALLY_SUPPORTED** (não SUPPORTED automático)
- Parâmetro `allow_partial_ok` para casos partial evidence

---

## 10. Tenant-scoped flags

Configuração recomendada GP (fail-closed global):

```env
LIVIA_RAG_ENABLED=True
LIVIA_RAG_DRY_RUN=True
LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST=granimarmores-pitondo

LIVIA_AI_ENABLED=True
LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED=False
LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST=granimarmores-pitondo
```

Demais tenants: comportamento preservado (sem allowlist = sem RAG semântico em dry_run; sem grounded sem flag global).

Helpers: `is_rag_semantic_context_active()`, `is_grounded_synthesis_allowed()`.

---

## 11. Testes automatizados

| Suite | Resultado |
|-------|-----------|
| SQLite | **504 OK**, 11 skipped |
| PostgreSQL | **504 OK**, 2 skipped |
| Novos (`test_evidence_sufficiency`) | 12 casos A–H + gates |

Casos cobertos: quote suficiente, execução partial, garantia insufficient, 48h mismatch, região, negation faithfulness, allowlist, partial grounded integration, insufficient skip.

`makemigrations --check`: alteração espúria sugerida em vector field (ambiente); **sem migration funcional Fase 15**.

---

## 12. Smoke OpenAI

Executado com allowlists GP. **Limitado por infra:**

```text
rag_vector_health → REINDEX_REQUIRED (19 embeddings incompatíveis)
retrieval → skipped (no_usable_index)
grounded → não exercitado end-to-end no smoke
```

Resultado smoke: maioria `ai=none` (correto com rewrite bloqueado), faithfulness variável sem KB real.

Ver `docs/phase15_openai_smoke_report.md`.

**Ação antes de staging:** `index_tenant_rag` / reindex + re-smoke com `LIVIA_RAG_DRY_RUN=False` só via allowlist GP.

---

## 13. Riscos restantes

| Risco | Severidade |
|-------|------------|
| Regras determinísticas não cobrem 100% dos qualificadores | Média |
| LLM ainda pode violar partial_inform sob edge cases | Média |
| DB local REINDEX_REQUIRED impede validação E2E agora | Alta (operacional) |
| Assessment offline no smoke script usa KB sintética para evidence | Baixa (script only) |

---

## 14. Critérios GO / staging

| Critério | Status |
|----------|--------|
| Suíte verde | OK |
| Partial evidence protegido (unit) | OK |
| Faithfulness gates | OK |
| Tenant-scoped flags | OK |
| Replay / soberania (sem regressão) | OK (suítes existentes) |
| Smoke real crítico com RAG ativo | **PENDENTE (reindex)** |
| Staging contínuo GP | **NÃO AUTORIZADO** |

---

## 15. Recomendação Fase 16

1. Reindex embeddings GP (`rag_vector_health` → OK).
2. Re-smoke Fase 15 com RAG+grounded ativos (10 casos críticos).
3. Se partial-prazo/execução passarem → **GO STAGING GP**.
4. Expandir regras de qualificador só com evidência de eval (sem hardcode Pitondo).

---

## 16. Chamadas OpenAI no smoke

Mínimas (~0 completions grounded nesta execução por `no_usable_index`). Execução anterior com rewrite legado: ~10 completions (pré-fix chat_processing).

Nenhum commit, push ou deploy realizados.
