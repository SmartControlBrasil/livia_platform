# Fase 20 — Relatório Final

**Data:** 2026-07-31
**Branch:** `chore/postgresql-readiness` @ `97309b1`
**Escopo:** artefatos locais reproduzíveis — **sem VPS, sem deploy, sem commit/push**

---

## 1. Diagnóstico inicial

### Lacunas identificadas (pré-Fase 20)

| Lacuna | Situação |
|---|---|
| Template `.env` staging dedicado | Apenas comentários em `.env.example` |
| Unit systemd staging | Documentação conceitual em `docs/phase19_staging_provisioning.md` |
| Vhost OpenLiteSpeed staging | Inexistente versionado |
| Gate pré-deploy automatizado | Comandos manuais dispersos |
| Gate pós-deploy HTTP | `phase19_http_soak.py` (soak completo, não smoke infra) |
| Relatório sanitizado de deploy | Parcial via `environment_readiness` / `database_readiness` |
| Proteção explícita prod vs staging (8011/8012) | Documentada oralmente, não em runbook único |

### Estado VPS conhecido (não alterado nesta fase)

Produção: `/home/livia.smartcontrolbrasil.com.br/livia_platform`, `main`, porta **8011**, SQLite.
Staging parcial: `/home/staging-livia.smartcontrolbrasil.com.br/livia_platform`, commit **97309b1**, porta **8012**, DB `livia_staging` — `.env`/systemd/DNS pendentes.

---

## 2. Arquivos criados

| Arquivo |
|---|
| `deploy/staging/livia-staging.env.example` |
| `deploy/staging/livia-staging.service` |
| `deploy/staging/openlitespeed-vhost.conf.example` |
| `tenants/services/staging_deployment.py` |
| `tenants/services/staging_postdeploy.py` |
| `tenants/management/commands/staging_deployment_report.py` |
| `scripts/staging_predeploy_check.py` |
| `scripts/staging_postdeploy_check.py` |
| `tenants/test_staging_deployment.py` |
| `docs/deploy/staging_physical_deployment.md` |

---

## 3. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `docs/environment_side_effect_matrix.md` | Seção Fase 20 + artefatos |
| `scripts/phase19_http_soak.py` | Respeita `LIVIA_SOAK_ORIGIN` |
| `scripts/phase18_staging_soak.py` | (Fase 19B) delegação HTTP quando `LIVIA_SOAK_BASE_URL` |

---

## 4. Arquitetura implementada

```text
deploy/staging/*           → templates operador (env, systemd, vhost)
tenants/services/staging_deployment.py  → parsing .env, gates, predeploy
tenants/services/staging_postdeploy.py  → checks HTTP read-only
scripts/staging_predeploy_check.py      → CLI exit 0/1/2
scripts/staging_postdeploy_check.py     → CLI + --chat-smoke opcional
manage.py staging_deployment_report     → relatório JSON/texto sanitizado
```

**Princípios:**

- Scripts pré-deploy **não** executam migrate, sync Drive, embeddings, OpenAI, CRM.
- URLs e relatórios **sanitizam** credenciais.
- Fail-closed staging preservado (checks Fase 18 reutilizados).

---

## 5. Proteções contra produção

Documentado em `docs/deploy/staging_physical_deployment.md`:

```text
Produção 8011 / SQLite / main / livia-platform.service → NÃO ALTERAR
Staging   8012 / livia_staging / chore/postgresql-readiness / livia-staging.service
```

Pré-deploy falha se:

- `DATABASE_URL` apontar SQLite ou host externo
- nome DB ≠ `livia_staging`
- provider `fake` ou allowlist ampla
- CRM/webhooks/handoff sem dry-run
- `.env` world-readable

---

## 6. Testes executados

```bash
git diff --check                    → OK
python manage.py check              → OK
python manage.py makemigrations --check --dry-run → No changes detected
python manage.py test tenants.test_staging_deployment → 18 OK
python manage.py test               → OK (suíte completa verde)
```

---

## 7. Resultados

| Critério Fase 20 | Status |
|---|---|
| Templates sem segredos | ✓ |
| Scripts não destrutivos | ✓ |
| Migrations inesperadas | ✓ nenhuma |
| Testes verdes | ✓ |
| Runbook reproduzível | ✓ |
| Produção protegida documentada | ✓ |
| Integrações default off/dry-run | ✓ no template |
| Deploy executado | ✗ fora de escopo |
| Staging operacional na VPS | ✗ pendente operador |

---

## 8. Pendências (operador na VPS)

1. Concluir `.env` a partir de `deploy/staging/livia-staging.env.example`
2. `staging_predeploy_check.py` → PASS
3. `migrate`, onboarding GP, RAG index
4. Instalar `livia-staging.service` + vhost + DNS + TLS
5. `staging_postdeploy_check.py` + `phase19_http_soak.py`
6. `ai_usage_report` pós-soak
7. Reemitir gate piloto GP

---

## 9. Riscos residuais

| Risco | Mitigação |
|---|---|
| Operador apontar `DATABASE_URL` para produção | Pré-deploy valida nome/host |
| Hardening systemd bloquear service account | `ProtectHome=false`; trade-offs documentados |
| `LIVIA_RAG_VECTOR_BACKEND=pgvector` vs código `postgres_pgvector` | Template usa `postgres_pgvector`; pré-deploy aceita alias `pgvector` com WARN |
| Staging concluído ≠ piloto prod | Runbook declara explicitamente |

---

## 10. Veredito

```text
FASE 20 CONCLUÍDA — GO PARA DEPLOY CONTROLADO EM STAGING
```

Motivo: artefatos, gates, testes e documentação reproduzível entregues localmente; nenhuma migration inesperada; produção explicitamente isolada; integrações permanecem off/dry-run no template.

**Nota:** GO aplica-se ao **processo de deploy controlado em staging**, não autoriza piloto de produção GP até gates Fases 19–20 operacionais na VPS.

Sem commit / push / deploy (conforme instrução).
