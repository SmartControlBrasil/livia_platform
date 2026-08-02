# Fase 17 — Validação de concorrência PostgreSQL

## Módulos executados

```bash
DATABASE_URL='postgresql://…@127.0.0.1:55432/livia_platform?sslmode=disable' \
.venv/bin/python manage.py test \
  operations_portal.test_operational_postgresql_concurrency \
  operations_portal.test_operational_analytics_postgresql \
  --verbosity=2
```

**Resultado:** OK (incluído na suíte completa de 701 testes).

## Cobertura validada

| Área | Mecanismo | Status |
|------|-----------|--------|
| Alert sync concorrente | `transaction.atomic` + savepoint + retry em `IntegrityError` | OK |
| Fila operacional | `select_for_update` em duas fases (sem `FOR UPDATE` em outer join nullable) | OK |
| Notification worker | `select_for_update(skip_locked=True)` | OK |
| Monitoring batch | advisory lock PostgreSQL | OK |
| Dedupe fingerprint | criação concorrente → 1 alerta | OK |
| Lease / stale recovery | worker antigo não sobrescreve após perda de lease | OK |
| Analytics PG | percentis, `TruncDate` com timezone | OK |

## Observações

- Testes de concorrência usam `TransactionTestCase` + conexões independentes (threads), não serialização falsa em `TestCase`.
- `OperationalAlertSyncTests.test_concurrent_sync_does_not_duplicate` permanece skipped no PG genérico; cobertura real em `test_operational_postgresql_concurrency.py`.

## TruncDate / timezone

`operational_analytics.py` usa `TruncDate(..., tzinfo=timezone.get_current_timezone())` — validado em `test_operational_analytics_postgresql.py` com eventos próximos à meia-noite (`America/Sao_Paulo` / UTC).
