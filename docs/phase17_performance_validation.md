# Fase 17 — Validação de performance (mínima)

## Escopo

Auditoria leve pós-suíte verde. **Nenhuma otimização ou migration de índice adicionada** — sem evidência de `EXPLAIN ANALYZE` com volume representativo nesta fase.

## Tempos de suíte (referência local)

| Suíte | Testes | Duração aprox. |
|-------|--------|----------------|
| PostgreSQL completa | 701 OK, 3 skipped | ~164 s |
| SQLite completa | 701 OK, 23 skipped | ~140 s |
| RAG focados PG | 82 OK, 2 skipped | ~3 s |
| Concorrência + analytics PG | 23 OK | ~8 s |

## Queries críticas

`EXPLAIN ANALYZE` **não executado** em produção de volume nesta fase. Prioridades futuras (quando houver dataset representativo):

- Analytics por período (agregações + percentis)
- Fila operacional ordenada por prioridade/SLA
- Contagem de notificações não lidas
- Agregação de alertas por fingerprint

## Índices

Nenhuma migration de índice adicional criada. Índices das migrations `0013`–`0017` aplicados e validados via migrations do zero.

## N+1

Sem investigação sistemática de query count no portal nesta fase. Testes de portal passam na suíte completa sem timeout.
