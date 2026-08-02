# Fase 18 — Plano de commits (proposta)

**Status:** preparação apenas — **nenhum `git add` / `commit` / `push` executado.**

## Princípios

1. Migrations no mesmo commit do model/feature correspondente.
2. Migrations `audit/001N` acompanham a fase operacional que adiciona actions.
3. Commits pequenos o suficiente para revisão; não fragmentar dependências circulares.

---

## Commit 1 — Fundação compartilhada

**Mensagem proposta:** `chore: adiciona settings e guardrails operacionais compartilhados`

| Tipo | Arquivos |
|------|----------|
| Config | `config/settings.py`, `config/environment_safety.py`, `config/tests.py`, `.env.example` |
| Portal base | `operations_portal/access.py`, `operations_portal/selectors.py`, `operations_portal/urls.py` (parcial se necessário), nav templates |
| Docs | `docs/environment_side_effect_matrix.md` |

**Risco:** baixo — sem migrations.

---

## Commit 2 — Fase 10 observabilidade + Fase 11 alertas

**Mensagem proposta:** `feat: adiciona observabilidade RAG e alertas operacionais`

| Tipo | Arquivos |
|------|----------|
| Models | `knowledge_base/models.py` (TenantOperationalAlert + campos base) |
| Migrations KB | `knowledge_base/migrations/0013_operational_alerts.py` |
| Migrations audit | `audit/migrations/0010_operational_alerts.py` |
| Services | `operational_alert_sync.py`, `operational_alert_rules.py`, `operational_alert_runbooks.py`, `alert_thresholds.py`, `operational_diagnostics.py`, `operational_metrics.py` |
| Commands | `sync_operational_alerts.py`, `rag_operational_report.py` |
| Portal | `operational_alert_services.py`, alerts views/templates, `test_operational_alerts_portal.py` |
| Audit | `audit/models.py` (actions) |
| Assistant | `ai_usage_report.py` |
| Docs | `phase10_*`, `phase11_*`, `knowledge_base.md`, `operations_portal.md` |

**Testes:** alert sync, portal alerts, RAG operational report.

---

## Commit 3 — Fase 12 monitoramento

**Mensagem proposta:** `feat: adiciona monitoramento operacional automatizado`

| Tipo | Arquivos |
|------|----------|
| Migrations KB | `0014_operational_monitoring.py` |
| Migrations audit | `0011_operational_monitoring.py` |
| Services | `operational_monitoring.py` |
| Commands | `process_operational_monitoring.py`, `prune_operational_monitoring_runs.py` |
| Portal | monitoring views/tests, health templates |
| Docs | `phase12_*` |

---

## Commit 4 — Fase 13 governança

**Mensagem proposta:** `feat: adiciona governança operacional (silences e maintenance)`

| Tipo | Arquivos |
|------|----------|
| Migrations KB | `0015_operational_governance.py` |
| Migrations audit | `0012_operational_governance.py` |
| Services | `alert_governance.py`, `alert_governance_services.py` |
| Portal | maintenance templates, governance tests |
| Docs | `phase13_*` |

---

## Commit 5 — Fase 14 fila operacional

**Mensagem proposta:** `feat: adiciona fila operacional e escalonamento`

| Tipo | Arquivos |
|------|----------|
| Migrations KB | `0016_operational_work_queue.py` |
| Migrations audit | `0013_operational_work_queue.py` |
| Services | `operational_work_queue.py`, `operational_work_queue_services.py` |
| Commands | `operational_work_queue_report.py` |
| Portal | work_queue views/services/templates/tests |
| Docs | `phase14_*` |

---

## Commit 6 — Fase 15 notificações

**Mensagem proposta:** `feat: adiciona notificações operacionais`

| Tipo | Arquivos |
|------|----------|
| Migrations KB | `0017_operational_notifications.py` (**índices reconciliados Fase 18**) |
| Migrations audit | `0014_operational_notifications.py` |
| Services | `operational_notification_*.py` (7 módulos) |
| Commands | `process_operational_notifications.py`, `prune_operational_notifications.py` |
| Portal | notification views/services/templates/tests |
| Docs | `phase15_*` |

---

## Commit 7 — Fase 16 analytics

**Mensagem proposta:** `feat: adiciona analytics operacional`

| Tipo | Arquivos |
|------|----------|
| Migrations audit | `0015_operational_analytics.py` |
| Services | `operational_analytics.py` |
| Commands | `operational_analytics_report.py` |
| Portal | analytics views/services/templates/tests |
| Docs | `phase16_*` |

---

## Commit 8 — Fase 17 PostgreSQL / pgvector

**Mensagem proposta:** `test: valida pgvector e concorrência no PostgreSQL`

| Tipo | Arquivos |
|------|----------|
| Tests | `knowledge_base/testing/`, `test_rag_*` (alterações dimensão), `test_rag_embedding_dimension_regression.py` |
| Tests PG | `test_operational_postgresql_concurrency.py`, `test_operational_analytics_postgresql.py` |
| Scripts | `scripts/run_postgresql_validation.sh` |
| Docs | `phase17_*`, `docs/deploy/sqlite_to_postgresql.md` |

**Nota:** reverts de patches experimentais já aplicados em `models.py` / `rag/*` — incluir diff correspondente neste commit ou no commit 2 se preferir agrupar por domínio RAG.

---

## Commit 9 — Deploy staging

**Mensagem proposta:** `chore: adiciona templates systemd de staging operacional`

| Arquivos | `deploy/staging/livia-operational-*.service`, `*.timer`, `staging_physical_deployment.md` |

**Risco:** baixo — templates não habilitados.

---

## Commit 10 — Documentação índice e Fase 18

**Mensagem proposta:** `docs: consolida índice de fases e relatório de reconciliação`

| Arquivos | `phase_index.md`, `phase18_*` |

---

## Dependências entre commits

```text
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
9 independente após 3 e 6 (commands existem)
10 após todos
```

## Arquivos essenciais — nenhum bloqueador

Todos os módulos importados em `operations_portal/urls.py` possuem arquivos untracked correspondentes no inventário.
