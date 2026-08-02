# Fase 10 — Observabilidade RAG/IA

## Rota

`/painel/base-de-conhecimento/saude/?tenant=<id>&period=24h|7d|30d`

Capability: `knowledge_base.view` (VIEWER+).

## Arquitetura

```text
TenantRagConfiguration / RagRetrievalEvent / AiUsageEvent / TenantRagOperationRequest
        ↓
knowledge_base/rag/operational_metrics.py
knowledge_base/rag/operational_diagnostics.py
        ↓
operations_portal/rag_health_services.py
        ↓
CLI (ai_usage_report, rag_operational_report) + Portal
```

## Métricas e denominadores

| Métrica | Denominador |
|---------|-------------|
| Retrieval hit rate | hits / retrievals executados (exclui skipped) |
| Grounded success | eventos AiUsageEvent grounded_synthesis com success |
| Falhas IA | falhas / requests AiUsageEvent no período |

## Limitações

- Evidence sufficient/partial/insufficient não persistido em evento dedicado.
- Fallback rate do chat não persistido — não exibido como percentual.
- Custo financeiro: sempre indisponível nesta fase.

## Privacidade

Sem API keys, prompts, chunks integrais, vetores ou stack traces na UI.
