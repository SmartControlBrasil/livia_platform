# Runbook — RAG staging controlado (Fase 12)

Fluxo operacional para validar retrieval em **um tenant** (ex.: `granimarmores-pitondo`) sem deploy em produção.

## Precedência de threshold

```text
override de comando/eval (temporário)
    ↓
TenantRagConfiguration.min_similarity_score
    ↓
LIVIA_RAG_MIN_SIMILARITY_SCORE (global / .env)
```

- O `.env` define o default da plataforma.
- O tenant define ajuste operacional específico (`null` = usa global).
- Não calibrar o global com base em um único cliente.
- Não calibrar só por hit-rate; priorizar precision/recall e source accuracy.
- Não reutilizar automaticamente o threshold de um tenant em outro.

```bash
python manage.py configure_tenant_rag \
  --tenant granimarmores-pitondo \
  --min-similarity-score 0.35

# remover override:
python manage.py configure_tenant_rag \
  --tenant granimarmores-pitondo \
  --clear-min-similarity-score
```

`rag_vector_health` exibe `threshold_default`, `threshold_tenant` e `threshold_effective`.

Calibração candidata (GP local Fase 12): `min_similarity_score=0.40` (melhor precision residual vs 0.35, sem cair em recall de 0.43).

## Resposta determinística vs RAG semântico

Com `LIVIA_AI` desligado, o bloco semântico `[KNOWLEDGE_BASE]` (com `Score:`) **não** é ecoado na reply ao visitante — fica só para o prompt de IA. O retriever textual (`KnowledgeDocument`) ainda pode contribuir com hints curtos.

## Fase 13 — Síntese grounded

Flags:

```text
LIVIA_AI_ENABLED=True
LIVIA_AI_GROUNDED_SYNTHESIS_ENABLED=True   # global
AssistantProfile.grounded_synthesis_enabled=True  # tenant
AssistantProfile.use_ai=True
LIVIA_RAG_DRY_RUN=False
retrieval_enabled=True
```

Perfil conversacional (multi-tenant):

```bash
python manage.py configure_assistant_profile \
  --tenant granimarmores-pitondo \
  --business-name "Granimármores Pitondo" \
  --business-domain "marmoraria, pedras naturais e projetos sob medida" \
  --enable-ai \
  --enable-grounded-synthesis
```

Eval de faithfulness (sem LLM judge):

```bash
python manage.py rag_faithfulness_eval \
  --tenant granimarmores-pitondo \
  --dataset knowledge_base/rag/eval/datasets/granimarmores_staging.json
```

Casos com `"response_eval": true` no dataset definem `facts_expected` / `facts_forbidden`.

## Context budget

O limite `LIVIA_RAG_MAX_CONTEXT_CHARS` aplica-se ao **texto selecionado dos chunks** (`selected_chars`), não ao bloco formatado com headers (`formatted_context_chars`).

Métricas:

```text
retrieved_chars          — candidatos acima do threshold (pré-seleção)
selected_raw_chars       — soma dos textos brutos escolhidos (pré-truncamento)
selected_chars           — texto efetivamente selecionado (pós-budget)
formatted_context_chars  — bloco [KNOWLEDGE_BASE] formatado
chunks_discarded_by_budget
```

## 1. Health inicial

```bash
python manage.py rag_vector_health --tenant granimarmores-pitondo
```

## 2. Readiness

```bash
python manage.py database_readiness
```

## 3. Inventário → export → chunks → reindex

Ver fases 10–11. Fixtures técnicas não entram na avaliação semântica.

## 4. Calibrar threshold por tenant

```bash
python manage.py rag_eval --tenant <slug> \
  --dataset knowledge_base/rag/eval/datasets/granimarmores_staging.json

python manage.py rag_eval --tenant <slug> --compare-thresholds 0.20,0.25,0.30,0.35,0.40
python manage.py rag_eval --tenant <slug> --compare-max-chunks 3,5
```

Sem `--threshold`, o eval usa o threshold efetivo do tenant.

## 5. Dry-run

```text
LIVIA_RAG_ENABLED=True
LIVIA_RAG_DRY_RUN=True
retrieval_enabled=True  # só no tenant alvo
```

## 6. Ativar (Fase B) — só com quality gate

```text
LIVIA_RAG_DRY_RUN=False
retrieval_enabled=True  # só granimarmores-pitondo
```

Registrar: profile, threshold efetivo, candidate limit, max chunks, context budget, chunks per manifest.

## 7. Monitorar e rollback

```bash
python manage.py rag_retrieval_report --tenant <slug> --days 1
```

O relatório separa `dry_run` vs `active` e `threshold_source`.

Rollback: `retrieval_enabled=False` (sem apagar índice).

## Critérios NO-GO absolutos

Cross-tenant, fallback quebrado, HTTP 500 só por retrieval, replay reexecutando retrieval, profile/schema mismatch, injection controlando regras (qualification/handoff/tenant).
