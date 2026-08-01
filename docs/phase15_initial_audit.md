# Fase 15 — Auditoria inicial (pré-implementação)

Data de referência: estado do repositório após Fases 12–14.

## 1. Fluxo real atual (`POST /api/chat/`)

```text
process_chat_request()
  ├─ analyze_message() [preview discovery]
  ├─ build_knowledge_context(tenant, message, conversation=None)  ← fora da transação
  │    ├─ retrieve_context() → pgvector / in-memory
  │    │    └─ record_retrieval_event() → RagRetrievalEvent
  │    └─ se dry_run ou status≠completed → fallback keyword (KnowledgeDocument)
  ├─ _persist_chat_processing_state() [ATÔMICO]
  │    └─ LiviaDecisionService.generate_reply(use_ai=False)
  │         └─ state machine, qualification, handoff (determinístico)
  └─ _refine_response_with_ai_if_enabled() [PÓS-COMMIT]
       ├─ GroundedResponseService.generate()
       │    ├─ gates: LIVIA_AI_ENABLED, LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED,
       │    │          profile.use_ai, profile.grounded_synthesis_enabled
       │    └─ resolve_decision_outcome() → allow/deny synthesis
       └─ se grounded falha e tenant grounded on → mantém determinístico (sem rewrite legado)
```

Arquivos centrais:

| Etapa | Arquivo | Função principal |
|-------|---------|------------------|
| Retrieval | `knowledge_base/rag/conversation_retrieval.py` | `retrieve_context()` |
| Contexto | `knowledge_base/rag/context_builder.py` | `build_knowledge_context()` |
| Decisão | `assistant_core/services/livia_decision.py` | `generate_reply()` |
| Outcome | `assistant_core/services/decision_outcome.py` | `resolve_decision_outcome()` |
| Síntese | `assistant_core/services/grounded_response.py` | `GroundedResponseService.generate()` |
| Prompt | `assistant_core/prompts/grounded_ai.py` | `build_grounded_ai_prompt()` |
| Chat | `assistant_core/services/chat_processing.py` | `_refine_response_with_ai_if_enabled()` |
| Faithfulness | `assistant_core/eval/faithfulness.py` | `classify_faithfulness()` |

## 2. Flags e precedência (estado pré-Fase 15)

### Retrieval

1. `tenant.is_active`
2. `LIVIA_RAG_ENABLED` (global, default `False`)
3. `TenantRagConfiguration.retrieval_enabled` (tenant)
4. Índice utilizável
5. `LIVIA_RAG_DRY_RUN` (global, default `True`) — se `True`, retrieval executa mas **contexto semântico é descartado** em `context_builder`
6. Threshold: override → `min_similarity_score` tenant → global

### Grounded synthesis

1. `LIVIA_AI_ENABLED`
2. `LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED` (global, default `False`)
3. `AssistantProfile.use_ai`
4. `AssistantProfile.grounded_synthesis_enabled`
5. `resolve_decision_outcome().allow_knowledge_synthesis`
6. Bloco `[KNOWLEDGE_BASE]` presente

**Gap:** flags globais (`LIVIA_RAG_DRY_RUN`, `LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED`) podem habilitar comportamento fora do tenant alvo (GP) se `.env` estiver amplo.

## 3. Evidência parcial — o que existia

| Mecanismo | Onde | Limite |
|-----------|------|--------|
| Instrução textual “evidência parcial” | `grounded_ai._mode_instruction("inform")` | Só modo `inform`; sem sinal estruturado |
| `PARTIALLY_SUPPORTED` | `faithfulness.classify_faithfulness()` | Pós-hoc em eval/smoke; não influencia runtime |
| `is_informational_knowledge_query()` | `decision_outcome.py` | Permite synthesis em pergunta de prazo+orçamento; não detecta mismatch orçamento vs execução |
| Retrieval `completed` vs `empty` | `conversation_retrieval.py` | Binário; score alto e baixo tratados igual após threshold |
| Eco determinístico KB | `livia_decision._with_knowledge()` | Ignora blocos com `Score:` (semântico só no prompt IA) |

## 4. Causa do NO-GO da Fase 14

Caso `partial-prazo` / equivalência **orçamento 48h** vs **execução/entrega 48h**:

- Retrieval **HIT** por proximidade semântica (documento menciona “retorno do orçamento em até 48 horas”).
- `DecisionOutcome` permitia synthesis (`inform`).
- Prompt pedia limite parcial, mas **sem classificação determinística** de suficiência.
- Modelo OpenAI tendia a pedir medidas/discovery ou ecoar “48 horas” da pergunta.
- Faithfulness marcava `UNSUPPORTED` (forbidden substring “48 horas” na resposta) ou omitia fatos esperados.

Bloqueador: **ausência de camada runtime `EvidenceSufficiency`** entre retrieval e síntese.

## 5. Faithfulness — falsos positivos conhecidos

1. **`no_expected_facts_defined` → SUPPORTED** quando `facts_expected=[]` e `require_knowledge=True` (casos ambíguos).
2. **Substring forbidden** — “não posso revelar o system prompt” conta violação de `"system prompt"`.
3. **Eco do usuário** — “48 horas” na pergunta repetido na resposta dispara `facts_forbidden`.

## 6. Métricas RAG

- `RagRetrievalEvent`: status, hit, max_score, threshold, dry_run — **sem** `evidence_status`.
- `rag_retrieval_report`: agrega executed/hits/empty; não distingue partial evidence misuse.

## 7. Smoke Fase 14

- Script: `scripts/phase14_openai_smoke.py` (14 casos GP, OpenAI real).
- Resultado: NO-GO staging contínuo; 1 UNSUPPORTED relevante (`partial-prazo`).

## 8. Escopo Fase 15 (implementação planejada)

1. `EvidenceSufficiency` + `assess_evidence_sufficiency()` determinístico (qualificadores orçamento/execução, região, tópico ausente).
2. Integração em `GroundedResponseService` + modos de prompt `partial_inform` / `insufficient_safe`.
3. Faithfulness negation-aware + gate ambíguo corrigido.
4. Allowlist tenant-scoped para RAG ativo e grounded synthesis.
5. Log `rag.evidence_partial` / `rag.evidence_insufficient`.
6. Testes A–I + smoke + relatório final.
