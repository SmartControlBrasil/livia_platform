# Fase 16 — Analytics Operacional

## Visão geral

Camada tenant-scoped de indicadores operacionais derivados de alertas, governança, fila, notificações e monitoramento — **sem IA preditiva** e **sem snapshots diários persistidos**.

## Service central

`knowledge_base/rag/operational_analytics.py`

```python
build_operational_analytics(tenant, period, filters)
build_operational_health_summary(tenant)
export_analytics_csv_rows(tenant, period)
```

## Períodos

| Código | Janela |
|--------|--------|
| `24h` | 24 horas |
| `7d` | 7 dias (default) |
| `30d` | 30 dias |
| `90d` | 90 dias |

## Métricas principais

- **Volume** — criados/reconhecidos/resolvidos/reabertos no período
- **Backlog** — estoque atual (≠ volume)
- **Age buckets** — envelhecimento centralizado
- **MTTA / MTTR** — medianas e percentis (amostra mínima configurável)
- **SLA ACK/resolução** — compliance com denominador explícito
- **Recorrência** — `reopen_count` (≠ `occurrence_count`)
- **Escalonamento** — níveis, triggers, auto/manual
- **Capacity** — workload score P1–P4 por membership
- **Notificações** — read rate in-app; e-mail/webhook separados como dry-run
- **Monitoramento** — runs por tenant
- **Tendências** — séries diárias + backlog líquido

## Workload score

```text
score = P1×4 + P2×3 + P3×2 + P4×1  (defaults configuráveis)
```

Estados: `idle`, `normal`, `attention`, `overload`.

**Não** é avaliação individual de desempenho.

## Portal

| Rota | RBAC |
|------|------|
| `/painel/operacoes/analytics/` | `knowledge_base.view` |
| `/painel/operacoes/analytics/exportar/` | `knowledge_base.configure` |

Capacidade detalhada por responsável visível apenas para gestores (`configure`).

## CLI

```bash
python manage.py operational_analytics_report --tenant <slug> --period 7d [--as-json]
```

## Settings

`LIVIA_OPERATIONAL_ANALYTICS_MIN_SAMPLE` (default 3)
`LIVIA_OPERATIONAL_ANALYTICS_WORKLOAD_P1..P4`
`LIVIA_OPERATIONAL_ANALYTICS_CAPACITY_*`

## Limitações

- Backlog histórico ao fim do dia não persistido
- SLA pausado em manutenção via governança runtime (deadline não estendido)
- Percentis via Python (SQLite); PostgreSQL pode evoluir para `percentile_cont`

## Migrations

Nenhuma migration de modelo nesta fase.
Audit: `0015_operational_analytics` (`operational_analytics.exported`).
