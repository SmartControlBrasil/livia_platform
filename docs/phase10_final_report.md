# Fase 10 — Relatório final: Central de Saúde RAG/IA

**Data:** 2026-07-29
**Branch:** `chore/postgresql-readiness` @ `9fd5d38` (base)
**Working tree:** alterações Fase 10 **sem commit** (conforme especificação)

---

## 1. Diagnóstico inicial

A auditoria está em `docs/phase10_initial_audit.md`. Resumo:

| Fonte reutilizada | Uso na Fase 10 |
|-------------------|----------------|
| `TenantRagConfiguration` | Snapshot de configuração cadastrada vs efetiva |
| `TenantRagOperationRequest` | Fila, lease, heartbeat, falhas sanitizadas |
| `RagRetrievalEvent` | Métricas agregadas e tabela paginada |
| `AiUsageEvent` | Tokens, latência, erros categorizados |
| `inspect_tenant_embedding_health` | Vector health (dados locais) |
| `inspect_rag_operations_readiness` | Readiness operacional |
| `inspect_rag_vector_readiness` | Readiness de infra vetorial |
| `inspect_environment_safety` | Readiness de ambiente |
| Comandos `rag_operational_report`, `ai_usage_report` | Refatorados para services compartilhados |

**Dados ainda inexistentes:** evidence sufficient/partial/insufficient persistido; fallback rate do chat; custo financeiro confiável.

**Duplicação eliminada:** agregações ORM inline dos commands migradas para `operational_metrics.py`.

---

## 2. Arquitetura implementada

```text
Models / Events / Configuration
        ↓
knowledge_base/rag/operational_metrics.py      (agregações)
knowledge_base/rag/operational_diagnostics.py  (readiness + recomendações)
        ↓
operations_portal/rag_health_services.py       (dashboard + paginação)
        ↓
CLI (ai_usage_report, rag_operational_report) + Portal (knowledge_base_health)
```

Nenhuma view executa subprocess ou chama OpenAI na renderização.

---

## 3. Rota e navegação

| Item | Valor |
|------|-------|
| Rota | `/painel/base-de-conhecimento/saude/` |
| Name | `operations_portal:knowledge_base_health` |
| Query params | `tenant`, `period=24h\|7d\|30d`, `ops_page`, `retrieval_page`, `ai_page` |
| Nav | Botão **Saúde RAG/IA** em `_nav.html` da base de conhecimento |
| Links | Atualização, busca diagnóstica, eventos (sem duplicar sync/index) |

---

## 4. Scorecards

| Card | Valor | Status | Período |
|------|-------|--------|---------|
| Configuração RAG | Ativa/Inativa | success/warning/info | Efetiva agora |
| Vector health | OK / STALE / REINDEX_REQUIRED | success/warning/danger | Instantâneo |
| Operações ativas | pending + running | warning se stale | Fila atual |
| Retrieval | hit % ou "Sem dados" | success/warning/secondary | Período selecionado |
| Tokens IA | total ou "Sem dados" | info/secondary | Período selecionado |
| Falhas IA | contagem ou "Sem dados" | warning/success | Período selecionado |

---

## 5. Readiness

Consolidado via `build_consolidated_readiness`:

| Seção | Estados |
|-------|---------|
| Environment | READY / READY_WITH_WARNINGS / NOT_READY |
| Database | READY / NOT_READY (migrations pendentes) |
| RAG operations | READY / READY_WITH_WARNINGS / NOT_READY |
| Vector | READY / READY_WITH_WARNINGS / NOT_READY |
| Overall | derivado das seções acima |

Overall da página (SAUDÁVEL / ATENÇÃO / BLOQUEADO / SEM DADOS) vem de `classify_overall_health`, alinhado a severidades das recomendações — não trata ausência de tráfego como crítico.

---

## 6. Vector health

Reutiliza `inspect_tenant_embedding_health` + `embedding_coverage_breakdown` via `build_vector_health_summary`. Exibe documentos/chunks/cobertura/incompatibilidades/reindex_required sem chamada externa.

---

## 7. Operações RAG

Resumo agregado (pending/running/succeeded/failed/stale) + tabela paginada com tipo, status, tentativas, heartbeat e erro truncado (60 chars). Operações stale geram recomendação **critical**.

---

## 8. Retrieval metrics

| Métrica | Denominador |
|---------|-------------|
| Hit rate | hits / retrievals executados (exclui skipped) |
| Executados / empty / failed | contagem direta no período |
| Grounded success | AiUsageEvent grounded_synthesis success no período |

Períodos: 24h, 7d (default), 30d. Zero eventos → "Sem dados no período", sem percentuais enganosos.

---

## 9. AI usage

Agregação tenant-scoped: requests, success/failure, tokens, latência mediana/p95, erros agrupados por `error_type` (categoria sanitizada). Custo: sempre indisponível nesta fase.

---

## 10. Diagnósticos

Regras determinísticas em `build_health_recommendations` (sem IA):

| Código | Severidade | Gatilho |
|--------|------------|---------|
| configuration_missing | warning | sem TenantRagConfiguration |
| operations_dry_run | info | dry-run ativo |
| operations_disabled | info | gate global off |
| stale_operations | critical | running com lease expirado |
| embeddings_incompatible | warning | REINDEX_REQUIRED |
| retrieval_empty_elevated | warning | hit rate < 20% com dados |
| ai_failures_recent | warning | falhas AiUsageEvent |
| database_not_ready | critical | migrations pendentes |

---

## 11. RBAC

Reutiliza **`knowledge_base.view`** (VIEWER+). Sem capabilities novas. Views validam tenant + capability via `_resolve_knowledge_base_access`. Outsider → 403.

Matriz de testes `tenants/test_access.py` atualizada para incluir capabilities KB em manager/operator/viewer.

---

## 12. Tenant isolation

Todos os aggregates filtram `tenant=`. Testes cobrem:

- RagRetrievalEvent de outro tenant não aparece nas métricas
- AiUsageEvent de outro tenant não entra no summary
- Configuração/operações scoped ao tenant ativo

---

## 13. Performance

- Agregações via `Count`, `Sum`, `Avg` no ORM
- Paginação (`PAGE_SIZE=12`) para operações, retrieval e AI usage
- Sem carregamento de todos os eventos em memória
- Limites de período validados (`parse_health_period`)

---

## 14. Segurança e privacidade

**Não exibido:** API keys, connection strings, prompts integrais, chunks, vetores, stack traces.

**Exibido:** metadados operacionais, erros truncados/sanitizados, configuração efetiva (provider/model/dimension/threshold).

---

## 15. Arquivos modificados

| Arquivo | Finalidade |
|---------|------------|
| `knowledge_base/rag/operational_metrics.py` | Agregações compartilhadas CLI + portal |
| `knowledge_base/rag/operational_diagnostics.py` | Readiness + recomendações + overall |
| `operations_portal/rag_health_services.py` | Montagem do dashboard |
| `operations_portal/knowledge_base_views.py` | View `knowledge_base_health` |
| `operations_portal/templates/.../health.html` | UI Hando |
| `operations_portal/templates/.../_nav.html` | Link navegação |
| `operations_portal/urls.py` | Rota |
| `operations_portal/test_knowledge_base_health_portal.py` | Testes Fase 10 |
| `assistant_core/.../ai_usage_report.py` | Refactor → operational_metrics |
| `knowledge_base/.../rag_operational_report.py` | Refactor → operational_metrics |
| `tenants/test_access.py` | Matriz RBAC KB |
| `docs/phase10_initial_audit.md` | Auditoria |
| `docs/phase10_rag_ai_observability.md` | Doc operacional |
| `docs/operations_portal.md` | Seção central de saúde |
| `docs/knowledge_base.md` | Seção observabilidade |

---

## 16. Testes

### Validação

```text
python manage.py check → passed (0 issues)
```

### Resultados

| Suíte | Resultado | Skipped |
|-------|-----------|---------|
| **SQLite — completa** (`manage.py test`) | **587 passed** | 11 skipped |
| **Portal — health** (`test_knowledge_base_health_portal`) | 13 passed | 0 |
| **RBAC** (`tenants.test_access`) | passed | 0 |
| **PostgreSQL/pgvector** (`test_rag_pgvector`) | passed | 1 skipped (`requires PostgreSQL + pgvector`) |

### Cobertura spec §25

| Caso | Status |
|------|--------|
| A. Acesso autorizado | ✅ |
| B. Acesso negado | ✅ |
| C. Tenant isolation | ✅ |
| D. Estado sem dados | ✅ |
| E. Configuração efetiva | ✅ |
| F. Vector health saudável | ✅ (via mock + real empty) |
| G. Vector health incompatível | ✅ |
| H. Operação pending | ✅ |
| I. Operação running/heartbeat | ✅ (stale test) |
| J. Operação stale | ✅ |
| K. Retrieval metrics | ✅ |
| L. AI usage | ✅ |
| M. Erro OpenAI categorizado | ✅ |
| N. Períodos 24h/7d/30d | ✅ |
| O. Paginação | ✅ |
| P. Zero denominator | ✅ |
| Q. Query safety | ✅ (tenant filter + isolation tests) |

Skips não mascarados: testes pgvector PostgreSQL dependem de infra local indisponível.

---

## 17. Riscos restantes

### Críticos

Nenhum bloqueador de código identificado. Staging físico continua bloqueado por acesso VPS (Fase 9) — operacional, não de implementação.

### Não críticos

| Risco | Mitigação |
|-------|-----------|
| Evidence sufficiency não persistida | Proxy via hit/status; documentado |
| Fallback rate ausente | Não exibido como percentual |
| Custo financeiro | Indisponível por design |
| Gráficos temporais | Não implementados (opcional; sem lib no painel) |
| Matriz RBAC desatualizada | Corrigida em `tenants/test_access.py` |

---

## 18. Veredito

```text
FASE 10 CONCLUÍDA — GO CONDICIONAL
```

**Condições:**

1. Deploy/staging físico ainda pendente (herança Fase 9) — validação E2E em ambiente real depende de VPS.
2. Testes PostgreSQL+pgvector com 1 skip até infra disponível.
3. Alterações **não commitadas** — aguardando solicitação explícita do operador.

Funcionalmente: central tenant-scoped implementada, RBAC aplicado, isolamento garantido, services compartilhados com CLI, testes verdes (587/587 no SQLite), documentação atualizada.
