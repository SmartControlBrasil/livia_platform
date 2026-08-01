# Matriz de Side Effects Externos

Baseada no código em `livia-platform` (Fase 18).

Legenda staging GP esperado:

```text
REAL     = chamada externa real permitida
DRY-RUN  = pipeline executa sem HTTP real / mock id
OFF      = desabilitado fail-closed
GP-ONLY  = restrito por allowlist de tenant
```

---

## Matriz

| Componente | Arquivo principal | Flag(s) | Staging GP esperado | Comportamento real no código |
|---|---|---|---|---|
| **OpenAI chat (decision rewrite)** | `integrations/openai/client.py` | `LIVIA_AI_ENABLED`, `LIVIA_AI_DRY_RUN`, API key | REAL (GP) | HTTP POST `/v1/chat/completions` quando enabled + não dry_run |
| **OpenAI grounded synthesis** | `assistant_core/services/grounded_response.py` | + `LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST` | REAL **GP-ONLY** | Mesmo client; gate `is_grounded_synthesis_allowed` |
| **OpenAI embeddings (RAG)** | `knowledge_base/rag/embeddings.py` | `LIVIA_RAG_EMBEDDING_PROVIDER=openai`, API key | REAL **GP-ONLY** | HTTP POST `/v1/embeddings` em retrieval/index |
| **Smart360 CRM** | `leads/services/crm_dispatch.py` → outbox | `SMART360_LEAD_DISPATCH_*`, token/URL | **DRY-RUN** | `DRY_RUN=True` → mock external_id, sem HTTP real |
| **Webhooks tenant** | `integrations/webhooks/service.py` | `LIVIA_WEBHOOKS_*`, config tenant | OFF / DRY-RUN | Dry-run retorna 202 sem POST |
| **Handoff notification** | `leads/services/handoff_notification.py` | `LIVIA_HANDOFF_NOTIFICATIONS_*` | OFF / DRY-RUN | Transporte real não implementado |
| **Handoff WhatsApp (visitante)** | `leads/services/handoff.py` | `AssistantProfile.human_handoff_*` | OFF (staging) | Retorna URL wa.me na resposta API — não envia mensagem |
| **Outbox processor** | `integrations/management/commands/process_outbox.py` | `--execute` | OFF por padrão | Sem `--execute` = dry-run JSON |
| **Google Drive RAG sync** | `knowledge_base/rag/sync.py` | credencial readonly | REAL (read-only) | Export texto; não altera Drive |
| **E-mail externo** | — | — | OFF | Não implementado como transporte handoff |

---

## Gates de segurança (Fase 18)

| Gate | Implementação |
|---|---|
| `fake` embedding em staging/prod | `knowledge_base/rag/embeddings.py` → `EmbeddingConfigurationError` |
| CRM real em staging | `config/environment_safety.py` + `config/checks.py` (`livia.E001`) |
| Webhooks/handoff dry-run staging | Checks `livia.E003`, `livia.E004` |
| GP-only RAG/grounded | `assistant_core/services/ai_feature_gates.py` |

---

## Validação staging (comandos)

```bash
python manage.py environment_readiness --tenant granimarmores-pitondo
python manage.py check   # falha se LIVIA_ENVIRONMENT=staging e CRM dry-run false
```

---

## Telemetria (sem PII/prompt)

| Evento | Persistência |
|---|---|
| Chat/grounded tokens | `assistant_core.models.AiUsageEvent` |
| Embedding tokens | `AiUsageEvent` operation=`embedding` |
| Retrieval | `RagRetrievalEvent` |
| CRM dispatch | logs `crm_dispatch_*` + outbox |

Relatório: `python manage.py ai_usage_report --tenant granimarmores-pitondo`
