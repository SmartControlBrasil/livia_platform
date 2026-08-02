# Índice documental de fases — Lívia Platform

Este índice elimina ambiguidade entre trilhas paralelas que reutilizam números de fase.

## Trilha operacional do portal (canônica Fases 10–18)

| Fase | Tema | Documentos principais |
|------|------|----------------------|
| 10 | Observabilidade RAG/IA | `phase10_*`, `phase10_rag_ai_observability.md` |
| 11 | Alertas operacionais | `phase11_*`, `phase11_operational_alerts.md` |
| 12 | Monitoramento | `phase12_*`, `phase12_operational_monitoring.md` |
| 13 | Governança (silences, maintenance) | `phase13_*`, `phase13_operational_governance.md` |
| 14 | Fila operacional / SLA | `phase14_*`, `phase14_operational_work_queue.md` |
| 15 | Notificações | `phase15_*`, `phase15_operational_notifications.md` |
| 16 | Analytics operacional | `phase16_*`, `phase16_operational_analytics.md` |
| 17 | Validação PostgreSQL / pgvector | `phase17_*`, `deploy/sqlite_to_postgresql.md` |
| 18 | Reconciliação migrations / commits | `phase18_*` |

## Trilha RAG / grounded (numeração antiga)

Documentos como `phase16_rag_validation_report.md`, `phase17_staging_run_report.md` referem-se a **soak/staging RAG**, não à trilha operacional acima. Consultar cabeçalho de cada doc para contexto.

## Trilha staging / deploy

- `docs/deploy/staging_physical_deployment.md`
- `docs/deploy/sqlite_to_postgresql.md`
- `deploy/staging/*.service`, `*.timer`

## Trilha PostgreSQL readiness

Branch: `chore/postgresql-readiness`
HEAD publicado até Fase 10–16 parcial: `9fd5d38`
