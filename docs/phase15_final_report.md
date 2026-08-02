# Fase 15 — Relatório final

## 1. Diagnóstico inicial

Ver `docs/phase15_initial_audit.md`. Outbox genérica de integrações não cobre in-app/read/preferences; criada outbox dedicada reutilizando padrões de claim/retry.

## 2. Arquitetura

```text
Evento → Policy → Destinatários → on_commit enqueue → Notification outbox → Worker → Canal → Auditoria
```

## 3. Modelo de notificação

`TenantOperationalNotification` com campos de status, dedupe, destino seguro (route + object id), retry e metadata sanitizada.

## 4. Canais

| Canal | Fase 15 |
|-------|---------|
| IN_APP | Ativo |
| EMAIL | Dry-run |
| WEBHOOK | Dry-run estrutural |

## 5. Eventos notificáveis

13 tipos centralizados em `operational_notification_events.py`.

## 6. Destinatários

Membership tenant-scoped por evento (responsável, gestão, operadores P1/P2).

## 7. Preferências

`TenantOperationalNotificationPreference` por membership; defaults conservadores.

## 8. Políticas obrigatórias

Eventos críticos/SLA/escalonamento/responsável invalidado não silenciáveis via opt-out in-app.

## 9. Deduplicação

Constraint unique em `deduplication_key`; inclui `reopen_count` e nível de escalonamento.

## 10. Ciclo de reabertura

Novo `reopen_count` permite nova notificação equivalente.

## 11. Outbox

Enqueue via `transaction.on_commit` (não síncrono no request).

## 12. Canal in-app

Entrega = persistência + status `delivered`; leitura → `read`.

## 13. Badge

Contagem indexada por tenant + membership em `portal_template_context`.

## 14. Leitura

POST individual (auditada) e mark-all (sem audit em massa — decisão documentada).

## 15. Email dry-run

Template `knowledge_base/emails/operational_notification.txt`; sem transporte real.

## 16. Digest

Estrutura via `digest_frequency`; agendamento via `scheduled_at`.

## 17. Quiet hours

Adia e-mail; in-app persiste.

## 18. Worker

`process_operational_notifications` one-shot com claim `select_for_update skip_locked`.

## 19. Concorrência

Padrão alinhado ao outbox; teste PostgreSQL **skipped** em SQLite.

## 20. Retry

Apenas canais externos; backoff configurável; dead-letter = `failed`.

## 21. Cancelamento

Membership inativa cancela pendências.

## 22. Manutenção e silenciamento

Reduz ruído; não bloqueia SLA/escalonamento/responsável invalidado.

## 23. Ownership e escalonamento

Hooks em claim/transfer/assign/escalate/owner invalidated.

## 24. Observabilidade

`operational_notification_metrics.py` + worker runs + seção na Central de Saúde.

## 25. Readiness

`operational_notification_readiness.py` + gate staging e-mail.

## 26. RBAC

`knowledge_base.view`; sem leitura cross-user.

## 27. Tenant isolation

Testado — listagem, mark-read, badge, worker `--tenant`.

## 28. Segurança

Sem secrets/PII; destino via route enum; staging proíbe e-mail real.

## 29. Performance

Índices tenant/recipient/status/scheduled_at/dedupe.

## 30. Retenção

`prune_operational_notifications` — 90/180 dias.

## 31. Systemd

Templates versionados, não habilitados.

## 32. Arquivos principais

| Área | Arquivos |
|------|----------|
| Modelos | `knowledge_base/models.py`, migration `0017` |
| Core | `knowledge_base/rag/operational_notification_*.py` |
| Portal | `operations_portal/notification_*.py`, templates `notifications/` |
| Audit | `audit/models.py`, migration `0014` |
| CLI | `process_operational_notifications`, `prune_operational_notifications` |
| Deploy | `deploy/staging/livia-operational-notifications.*` |

## 33. Migrations

- `knowledge_base/0017_operational_notifications`
- `audit/0014_operational_notifications`

## 34. Testes

```text
Fase 15: 23 tests (22 passed, 1 skipped PostgreSQL concurrency)
Fase 14: 14 passed, 1 skipped
Suíte SQLite: 675 passed, 14 skipped
manage.py check: passed
```

## 35. Pendências PostgreSQL

Validar concorrência real (`skip_locked`, dedupe paralelo, worker paralelo) em PostgreSQL físico.

## 36. Riscos restantes

- Digest diário/semanal sem scheduler ativo (depende de timer futuro).
- E-mail real não implementado (por design).
- Filtros de fila in-app em conjuntos muito grandes podem precisar otimização.

## 37. Veredito

```text
FASE 15 CONCLUÍDA — GO CONDICIONAL
```

Condições:

```text
validação PostgreSQL de concorrência do worker ainda pendente
staging físico / timer systemd ainda não provisionados
```

Nenhum commit ou push realizado.
