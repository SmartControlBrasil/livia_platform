# Fase 18 — Relatório AI Usage

Tenant: `granimarmores-pitondo`
Janela: 1 dia (pós-migration + soak staging profile)

---

## Implementação

| Item | Detalhe |
|---|---|
| Modelo | `assistant_core.models.AiUsageEvent` |
| Migration | `assistant_core/migrations/0001_ai_usage_event.py` |
| Captura chat | `integrations/openai/client.py` → `usage` da API |
| Persistência grounded | `assistant_core/services/grounded_response.py` |
| Persistência embedding | `knowledge_base/rag/conversation_retrieval.py` |
| Comando | `python manage.py ai_usage_report --tenant … [--days N] [--json]` |

Campos persistidos (sem prompt/PII):

```text
tenant, operation, model, success, error_type
prompt_tokens, completion_tokens, total_tokens, latency_ms, metadata
```

Operações:

```text
grounded_synthesis
embedding
```

---

## Resultado após soak Fase 18 (staging-like, perfil staging)

Comando:

```bash
export LIVIA_ENVIRONMENT=staging SMART360_LEAD_DISPATCH_DRY_RUN=True
python manage.py ai_usage_report --tenant granimarmores-pitondo --days 1
```

```text
requests: 151
success: 151 | failure: 0
tokens: prompt=31268 completion=2206 total=33474
latency ms: avg=662.8 median=475 p95=1799

by operation:
  embedding: ~131 req, ~1285 tokens (retrieval queries acumuladas)
  grounded_synthesis: presente após soak completo (32 interações)
```

**Nota:** totais incluem execuções anteriores parciais (301 SSL) + soak final bem-sucedido no mesmo dia.

---

## Custo

```text
ESTIMATED COST: não calculado (sem preços hardcoded)
```

Métricas factuais disponíveis: modelo, requests, tokens, latência.

---

## Integração operacional

`rag_operational_report` atualizado:

```text
openai_token_usage_persisted: true (quando existem AiUsageEvent)
```

---

## Gaps restantes

- Tokens do path `livia_decision` rewrite legado (não GP allowlisted) — não prioritário
- Dashboard `/painel/` — adiado
- `evidence_status` ainda só em logs
