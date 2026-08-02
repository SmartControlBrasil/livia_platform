# Fase 11 — Relatório final: Alertas operacionais RAG/IA

**Data:** 2026-08-01
**Branch:** `chore/postgresql-readiness`
**Working tree:** alterações Fase 11 **sem commit** (conforme especificação)

---

## 1. Diagnóstico inicial

Ver `docs/phase11_initial_audit.md`. Reutilizados diagnósticos da Fase 10 (`operational_metrics`, `operational_diagnostics`, `rag_health_services`) sem duplicar regras em views ou templates.

## 2. Arquitetura

```text
Diagnóstico (build_rag_health_dashboard)
        ↓
operational_alert_rules.evaluate_alert_candidates
        ↓
operational_alert_sync.sync_operational_alerts
        ↓
TenantOperationalAlert (persistido)
        ↓
Portal (lista/detalhe/ack/resolve) + CLI sync_operational_alerts
```

## 3. Modelo de alerta

`TenantOperationalAlert` — migration `0013_operational_alerts`.

Constraint: `(tenant, fingerprint)` único.

## 4. Categorias

`configuration`, `environment`, `database`, `vector_health`, `rag_operations`, `retrieval`, `grounded_ai`, `openai_provider`, `token_usage`, `tenant_isolation`, `integration_safety`.

## 5. Severidades

Alinhadas à Fase 10: `info`, `warning`, `critical`. Alertas automáticos usam principalmente `warning` e `critical`.

## 6. Status e transições

| De | Para | Como |
|----|------|------|
| open | acknowledged | POST portal (operate) |
| open/acknowledged | resolved | POST manual ou auto-resolve |
| resolved | open | sync quando condição reaparece |

Transições inválidas levantam `OperationalAlertError`.

## 7. Fingerprint

`{rule_id}` ou `{rule_id}:{source_reference}` — ver `docs/phase11_operational_alerts.md`.

## 8. Deduplicação

Mesmo fingerprint → atualiza `last_seen_at` e `occurrence_count`. Concorrência protegida por unique constraint + retry em `IntegrityError`.

## 9. Resolução automática

Quando candidato desaparece do snapshot, alerta open/acknowledged é resolvido com `resolution_source=auto` e nota padrão.

## 10. Reconhecimento

Registra `acknowledged_by`, `acknowledged_at` e audit `operational_alert.acknowledged`.

## 11. Resolução manual

Nota obrigatória (≤500 chars). Próximo sync pode reabrir se condição persistir.

## 12. Runbooks

Matriz em `operational_alert_runbooks.py` — templates renderizam `recommended_action` sem duplicar texto.

## 13. Central de Saúde

- Card de alertas abertos
- POST **Atualizar diagnóstico e alertas**
- Badge no menu Base de Conhecimento

## 14. Lista e detalhe

- Filtros: status, severity, category, period
- Paginação (`PAGE_SIZE=12`)
- Links para saúde, operação RAG, configuração, busca diagnóstica

## 15. RBAC

| Ação | Capability |
|------|------------|
| Ver | `knowledge_base.view` |
| Sync / ACK / Resolve | `knowledge_base.operate` |

## 16. Tenant isolation

Querysets filtrados por tenant; testes cobrem listagem e sync cross-tenant.

## 17. Auditoria

6 actions novas em `audit/models.py` + migration `0010_operational_alerts`.

## 18. Concorrência

`transaction.atomic` + `select_for_update` + unique `(tenant, fingerprint)` + fallback em `IntegrityError`.

## 19. Performance

Listagem usa queries paginadas simples; diagnóstico pesado só no sync explícito.

## 20. CLI

```bash
python manage.py sync_operational_alerts --tenant <slug> [--dry-run] [--json]
python manage.py sync_operational_alerts --all-tenants --dry-run
```

## 21. Arquivos principais

| Arquivo | Finalidade |
|---------|------------|
| `knowledge_base/models.py` | Modelo |
| `knowledge_base/rag/alert_thresholds.py` | Thresholds |
| `knowledge_base/rag/operational_alert_runbooks.py` | Runbooks |
| `knowledge_base/rag/operational_alert_rules.py` | Regras |
| `knowledge_base/rag/operational_alert_sync.py` | Sync/ack/resolve |
| `knowledge_base/management/commands/sync_operational_alerts.py` | CLI |
| `operations_portal/operational_alert_services.py` | Listagem/detalhe |
| `operations_portal/knowledge_base_views.py` | Views portal |
| `operations_portal/templates/.../alerts.html` | UI lista |
| `operations_portal/templates/.../alert_detail.html` | UI detalhe |
| `operations_portal/test_operational_alerts_portal.py` | Testes |

## 22. Migrations

- `knowledge_base/migrations/0013_operational_alerts.py`
- `audit/migrations/0010_operational_alerts.py`

## 23. Testes

| Suíte | Resultado |
|-------|-----------|
| `manage.py check` | passed |
| Fase 11 (`test_operational_alerts_portal`) | 18 passed |
| Suíte completa SQLite | **605 passed**, 11 skipped |
| PostgreSQL/pgvector | 1 skipped (herança Fase 10) |

Cobertura spec §30: criação, dedup, occurrence, auto-resolve, reopen, ack, resolve, transição inválida, tenant isolation, RBAC, concorrência, sanitização, empty state, filtros, paginação, audit, threshold, CLI, portal sync.

## 24. Riscos restantes

| Risco | Severidade |
|-------|------------|
| Sem worker periódico de sync | Baixa — sync manual/CLI por design |
| Staging VPS bloqueado | Operacional (herança Fase 9) |
| Concorrência SQLite limitada | Baixa — proteção completa em PostgreSQL |

## 25. Veredito

```text
FASE 11 CONCLUÍDA — GO CONDICIONAL
```

Condições: staging físico pendente; 11 skips herdados; alterações não commitadas.

Funcionalmente: fluxo completo detecção → alerta → reconhecimento → resolução → auditoria implementado com testes verdes.
