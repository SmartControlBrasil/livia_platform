# Fase 18 — Inventário do working tree

**Branch:** `chore/postgresql-readiness`
**HEAD:** `9fd5d38`
**Data:** 2026-08-02

## Resumo

| Categoria | Quantidade |
|-----------|------------|
| Entradas `git status --short` | 125 |
| Modificados (`M`) | 32 |
| Untracked (`??`) | 93 |
| Deletados | 0 |
| Renomeados | 0 |

## Arquivos modificados (32)

| Arquivo | Classificação | Fase |
|---------|---------------|------|
| `.env.example` | configuração | shared |
| `assistant_core/management/commands/ai_usage_report.py` | código produtivo | Fase 10 |
| `audit/models.py` | código produtivo | Fase 11–16 |
| `config/environment_safety.py` | configuração | shared |
| `config/settings.py` | configuração | shared |
| `config/tests.py` | testes | shared |
| `docs/deploy/sqlite_to_postgresql.md` | documentação | Fase 17 |
| `docs/deploy/staging_physical_deployment.md` | documentação | deploy |
| `docs/environment_side_effect_matrix.md` | documentação | shared |
| `docs/knowledge_base.md` | documentação | shared |
| `docs/operations_portal.md` | documentação | shared |
| `docs/phase15_final_report.md` | documentação | Fase 15 |
| `docs/phase15_initial_audit.md` | documentação | Fase 15 |
| `docs/phase16_final_report.md` | documentação | Fase 16 |
| `docs/phase17_final_report.md` | documentação | Fase 17 |
| `knowledge_base/management/commands/rag_operational_report.py` | código produtivo | Fase 10 |
| `knowledge_base/models.py` | código produtivo + migrations implícitas | Fases 11–16 |
| `knowledge_base/test_rag_conversation.py` | testes | Fase 17 |
| `knowledge_base/test_rag_embedding_profile.py` | testes | Fase 17 |
| `knowledge_base/test_rag_eval.py` | testes | Fase 17 |
| `knowledge_base/test_rag_indexing.py` | testes | Fase 17 |
| `knowledge_base/test_rag_pgvector.py` | testes | Fase 17 |
| `operations_portal/access.py` | código produtivo | shared operational |
| `operations_portal/knowledge_base_views.py` | código produtivo | Fases 11–16 |
| `operations_portal/selectors.py` | código produtivo | shared operational |
| `operations_portal/templates/.../dashboard.html` | templates | shared |
| `operations_portal/templates/.../knowledge_base/_nav.html` | templates | shared |
| `operations_portal/templates/.../sidebar.html` | templates | shared |
| `operations_portal/templates/.../topbar.html` | templates | shared |
| `operations_portal/urls.py` | código produtivo | shared operational |
| `operations_portal/views.py` | código produtivo | shared operational |
| `tenants/test_access.py` | testes | shared |

## Arquivos untracked por categoria

### Migrations (11)

- `audit/migrations/0010` … `0015` — choices de `AuditEvent.action` por fase
- `knowledge_base/migrations/0013` … `0017` — models operacionais + notificações

### Código produtivo — knowledge_base/rag (18)

Serviços operacionais: alert sync, monitoring, governance, work queue, notifications, analytics, diagnostics, metrics.

### Management commands (7)

`sync_operational_alerts`, `process_operational_monitoring`, `process_operational_notifications`, reports, prune commands.

### Portal — views/services/templates (20+)

Analytics, alerts, health, maintenance, notifications, work queue + templates HTML.

### Testes (12)

Portal operacional, concorrência PostgreSQL, analytics PG, regressão dimensão RAG.

### Deploy (4)

Systemd service/timer para monitoring e notifications (staging templates).

### Scripts (1)

`scripts/run_postgresql_validation.sh`

### Documentação (20+)

Relatórios e guias Fases 10–17.

### Test helpers (2)

`knowledge_base/testing/rag_dimensions.py`, `test_rag_embedding_dimension_regression.py`

## Arquivos sensíveis

| Arquivo | Status |
|---------|--------|
| `.env` | ignorado pelo Git (não no status) |
| `.env.example` | modificado — apenas placeholders fictícios |
| Credenciais em docs | exemplos locais/staging (`CHANGE_ME`, `livia_local`) — aceitável |
| Dumps/logs/sqlite | nenhum untracked detectado |

**Nenhum secret real versionável identificado.**

## Arquivos experimentais / temporários

Nenhum `*.orig`, `*.rej`, `*.tmp`, dump ou banco SQLite untracked.
`__pycache__/` coberto por `.gitignore`.

## Correção aplicada nesta fase

- `knowledge_base/migrations/0017_operational_notifications.py` — nomes de índice alinhados ao Django (Opção A)
- `scripts/run_postgresql_validation.sh` — `makemigrations --check` bloqueante restaurado
