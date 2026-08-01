# Fase 18 — Validação Staging

Modo: **staging-like local** com perfil `LIVIA_ENVIRONMENT=staging`  
**Staging físico (`livia.smartcontrolbrasil.com.br`): não executado nesta sessão**

---

## Pré-requisitos soak

| Check | Resultado |
|---|---|
| `environment_readiness --tenant granimarmores-pitondo` | **READY** |
| `vector health` | **OK** (19 compatible, reindex 0) |
| `SMART360_LEAD_DISPATCH_DRY_RUN` | **True** (gate) |
| GP allowlists | rag + grounded **True** |

`database_readiness`: **NOT READY** por integridade local (`active_tenants_without_active_origin=1`, `originless_public_api` com DEBUG off) — tarefa de onboarding staging, não bloqueio de config safety.

---

## Soak E2E

Script: `scripts/phase18_staging_soak.py` (delega `phase17_staging_soak.py` com defaults staging)

```text
Interações: 32
Falhas críticas: 0
grounded: 21/32
retrieval_empty: 8
Latência E2E: median 2038 ms | p95 2607 ms
```

Comparativo Fase 17 (development profile):

| Métrica | Fase 17 | Fase 18 (staging profile) |
|---|---|---|
| Falhas críticas | 0 | 0 |
| grounded | 21/32 | 21/32 |
| median latency | 2025 ms | 2038 ms |
| p95 | 2687 ms | 2607 ms |

Sem regressão crítica.

JSON: `docs/phase17_soak_results.json` (atualizado pelo soak)

---

## Corpus / vector / retrieval

Baseline mantido:

```text
9 documentos | 19 chunks | 0 cross-tenant
provider=openai | model=text-embedding-3-small | dim=1536
```

---

## CRM dry-run

- Gate staging impede boot/check com `SMART360_LEAD_DISPATCH_DRY_RUN=False`
- Código: `CRMDispatchService` com `dry_run=True` usa mock external_id (`dry-run-{tenant}-{session}`)
- Outbox real dispatch requer `process_outbox --execute` + flags — **não executado**

Evidência automatizada: `leads/tests.py`, `integrations/tests.py` (dry-run paths)

---

## Idempotência

Soak `idem-replay`: reply idêntica, latência ~17 ms, sem crescimento de mensagens.

---

## Tenant isolation

GP allowlisted → grounded quando KB hit  
Outro tenant / origin inválida → fail-closed (403)

---

## Widget

Validação visual em host staging: **não realizada** (backend-only nesta sessão).  
API `/api/chat/` validada via Django test Client (mesmo entrypoint).

---

## OpenAI failures

Cobertura unitária: timeout, empty response (`integrations/tests.py`, `test_grounded_response.py`).  
Provider real não degradado intencionalmente.

---

## Comandos executados

```bash
python manage.py migrate assistant_core
python manage.py environment_readiness --tenant granimarmores-pitondo
python scripts/phase18_staging_soak.py
python manage.py ai_usage_report --tenant granimarmores-pitondo --days 1
python manage.py rag_vector_health --tenant granimarmores-pitondo
```
