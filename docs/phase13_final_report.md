# Fase 13 — Relatório final (governança operacional)

## 1. Diagnóstico inicial

Ver `docs/phase13_initial_audit.md`. Integração de suppression após persistência via estados derivados, sem alterar deduplicação por fingerprint.

## 2. Arquitetura implementada

```text
sync / portal / monitoring
    → TenantOperationalAlert (+ SLA, assignee)
    → alert_governance.build_alert_governance_state
    → portal (lista, detalhe, health scorecards)
    → alert_governance_services (ações mutáveis)
    → audit
```

## 3–7. Manutenção, escopos, não silenciáveis, silenciamento, expiração

- `TenantOperationalMaintenanceWindow` com status derivado por tempo
- Escopos: all / categories / rules / resource
- Não silenciáveis centralizados em `alert_governance.py`
- `TenantOperationalAlertSilence` com expiração por `ends_at` (sem job externo)

## 8–9. Atribuição e autoatribuição

FK `assigned_to` → `TenantMembership`. ACK sem responsável autoatribui membership do actor.

## 10–13. SLA, deadlines, pausa, estados derivados

Deadlines persistidos na abertura e reabertura; updates na mesma execução não estendem SLA. Estados: `on_track`, `due_soon`, `breached`, `not_applicable`, `paused`.

## 14–15. Central de Saúde e lista/detalhe

Scorecards de governança em `/painel/base-de-conhecimento/saude/`. Filtros novos na lista de alertas.

## 16–18. RBAC, tenant isolation, auditoria

Capabilities reutilizadas. Testes bloqueiam assignment cross-tenant. Eventos de auditoria registrados.

## 19. Concorrência

`select_for_update` + transações; teste de concorrência ACK/atribuição marcado skip em SQLite.

## 20. Readiness

Governança exposta via scorecards na Central de Saúde (separado de application readiness).

## 21. Performance

Índices em `tenant+assigned_to`, `tenant+ack_due_at`, `tenant+resolution_due_at`, janelas e silenciamentos.

## 22. Arquivos modificados (principais)

| Área | Arquivos |
|------|----------|
| Modelos | `knowledge_base/models.py` |
| Política | `knowledge_base/rag/alert_governance.py` |
| Serviços | `alert_governance_services.py`, `operational_alert_sync.py`, `operational_monitoring.py` |
| Portal | `operations_portal/knowledge_base_views.py`, `operational_alert_services.py`, templates, `urls.py` |
| Settings | `config/settings.py` |
| Audit | `audit/models.py` |
| Migrations | `knowledge_base/0015_operational_governance.py`, `audit/0012_operational_governance.py` |
| Testes | `operations_portal/test_operational_governance_portal.py` |
| Docs | `docs/phase13_*.md`, atualizações em portal/knowledge_base/phase12 |

## 23. Migrations

`0015_operational_governance` (knowledge_base), `0012_operational_governance` (audit).

## 24. Testes

| Suíte | Resultado |
|-------|-----------|
| `manage.py check` | passed |
| Fase 13 (`test_operational_governance_portal`) | 22 passed, 1 skipped (concorrência PostgreSQL) |
| Fase 11 + 12 regressão | passed |
| SQLite completa | **638 passed, 12 skipped** |

## 25. Pendências PostgreSQL

Concorrência real (`select_for_update` paralelo), advisory locks de monitoramento e validação de índices em staging físico permanecem pendentes.

## 26. Riscos restantes

- Filtros de manutenção/silenciamento em lista usam avaliação em memória quando filtros derivados estão ativos (volume baixo por tenant; otimização futura possível)
- Formulário de manutenção usa `datetime-local` (timezone do browser); operadores devem confirmar horário aware
- Timer systemd continua desabilitado por design

## 27. Veredito

```text
FASE 13 CONCLUÍDA — GO CONDICIONAL
```

Governança operacional implementada e testada em SQLite. GO pleno depende de validação PostgreSQL/concorrência em staging quando disponível.
