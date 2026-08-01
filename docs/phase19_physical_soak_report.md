# Fase 19 — Soak físico (staging GP)

**Data:** 2026-07-31  
**Script:** `scripts/phase18_staging_soak.py` → `scripts/phase17_staging_soak.py`  
**Endpoint físico de staging:** **indisponível** (host DNS ausente; serviço não provisionado).

---

## 1. Execução nesta sessão

| Item | Status |
|---|---|
| Soak contra URL HTTPS staging dedicada | **NÃO EXECUTADO** |
| Soak contra IP/porta backend staging | **NÃO EXECUTADO** |
| `phase18_staging_soak.py` no notebook | **NÃO REEXECUTADO** nesta sessão F19 |

Motivo: Fase 19 exige soak no **host físico isolado** após provisionamento (§26 do brief). Sem staging operacional, executar soak local seria repetir Fase 18 sem satisfazer o gate físico.

---

## 2. Baseline de referência (Fase 18 — staging-like local, **não** físico)

Fonte: `docs/phase18_staging_validation.md` / `docs/phase18_final_report.md`.

Perfil: PostgreSQL local + overrides staging em subprocess (`LIVIA_ENVIRONMENT=staging`, `SMART360_LEAD_DISPATCH_DRY_RUN=True`, etc.).

| Métrica | Valor F18 |
|---|---|
| Interações | 32 |
| Falhas críticas | **0** |
| Grounded | 21/32 |
| Median E2E | ~2038 ms |
| p95 E2E | ~2607 ms |
| OpenAI errors | 0 (críticos) |

**Telemetria pós-soak F18 (local, janela 1 dia):**

```text
tokens total ≈ 33474
requests ≈ 151
```

Isso **não** comprova telemetria no servidor de staging físico.

---

## 3. Critérios do soak físico (pendentes)

Quando staging existir, será necessário **adaptar** `scripts/phase17_staging_soak.py` (hoje usa `django.test.Client` in-process) para HTTP contra o host físico, ou rodar o soak no servidor com Gunicorn local + proxy. Exemplo conceitual pós-adaptação:

```bash
export LIVIA_SOAK_BASE_URL='https://<host-staging-real>'
export LIVIA_ENVIRONMENT=staging
export SMART360_LEAD_DISPATCH_DRY_RUN=True
python scripts/phase18_staging_soak.py
```

Registrar obrigatoriamente:

```text
interações
falhas críticas          → gate: 0
retrieval hits / empty
grounded / partial / insufficient / fallback
OpenAI errors
median / p95 latency
tokens (via ai_usage_report pós-soak)
```

Gate mínimo:

```text
0 falhas críticas
qualquer alucinação factual crítica → NO-GO
```

---

## 4. Pré-requisitos não atendidos antes do soak

| Pré-requisito | Status F19 |
|---|---|
| SMART360 dry-run comprovado **no servidor** | Não |
| Handoff externo desabilitado no servidor | Não verificado |
| `/health/?readiness=1` → READY no staging | Não |
| `database_readiness` → READY no staging | Não |
| Origin GP configurada **no DB staging** | Não |
| TLS válido no host staging | Não |

**Decisão:** soak físico **abortado** (não iniciado).

---

## 5. Testes complementares F19 (não executados)

Por dependência do endpoint físico:

- Idempotência física (replay HTTP externo)
- Isolamento tenant físico (origins inválidas, slug manipulado)
- Regressão completa no código deployado no staging
- `ai_usage_report --tenant granimarmores-pitondo` pós-soak **no DB staging**

---

## 6. Conclusão

```text
SOAK FÍSICO FASE 19 — NÃO REALIZADO
PILOTO GP — BLOQUEADO ATÉ STAGING FÍSICO + SOAK 0 FALHAS CRÍTICAS
```
