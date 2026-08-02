# Fase 15 — Central de Notificações Operacionais

## Visão geral

Camada tenant-scoped de notificações operacionais com:

- outbox dedicada (`TenantOperationalNotification`);
- canal **IN_APP** ativo;
- **EMAIL/WEBHOOK** estruturais em dry-run;
- política centralizada determinística;
- preferências por membership;
- deduplicação por ciclo de alerta;
- worker one-shot;
- portal `/painel/notificacoes/`.

## Fluxo

```text
Evento operacional (sync/governança/fila)
        ↓
transaction.on_commit → enqueue
        ↓
TenantOperationalNotification (pending)
        ↓
process_operational_notifications
        ↓
IN_APP delivered / EMAIL dry-run / retry externo
        ↓
Auditoria
```

## Modelos

| Modelo | Função |
|--------|--------|
| `TenantOperationalNotification` | Outbox + registro in-app |
| `TenantOperationalNotificationPreference` | Preferências por membership |
| `TenantOperationalNotificationWorkerRun` | Observabilidade do worker |

## Estados

`pending` → `processing` → `delivered`/`sent` → `read`

Também: `failed`, `cancelled`, `suppressed`.

## Eventos notificáveis

Crítico criado, atribuído, transferido, reconhecido, resolvido, reaberto, SLA ACK/resolução, escalonado, responsável invalidado, manutenção iniciada/cancelada, monitoramento falhou.

**Não** notifica incrementos de `occurrence_count` nem polling genérico.

## Política

`knowledge_base/rag/operational_notification_policy.py`

- destinatários por membership/role/escalonamento;
- dedupe: `t{tenant}:r{membership}:e{event}:c{channel}:alert{id}:cycle{reopen_count}...`;
- eventos críticos não silenciáveis ignoram opt-out in-app;
- quiet hours adiam e-mail, não in-app.

## Preferências

Rota: `/painel/notificacoes/preferencias/`

Defaults: `in_app_enabled=True`, `email_enabled=False`.

## Portal

| Rota | Função |
|------|--------|
| `/painel/notificacoes/` | Central (filtros + paginação) |
| `/painel/notificacoes/preferencias/` | Preferências |
| POST marcar lida / todas lidas | CSRF + tenant isolation |

Badge no menu/topbar via `portal_template_context`.

## Worker

```bash
python manage.py process_operational_notifications [--limit N] [--channel] [--tenant SLUG] [--dry-run] [--json]
python manage.py prune_operational_notifications [--tenant SLUG] [--dry-run]
```

Systemd templates (não habilitados):

- `deploy/staging/livia-operational-notifications.service`
- `deploy/staging/livia-operational-notifications.timer`

## Settings

| Flag | Default |
|------|---------|
| `LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_ENABLED` | False |
| `LIVIA_OPERATIONAL_EMAIL_NOTIFICATIONS_DRY_RUN` | True |
| `LIVIA_OPERATIONAL_NOTIFICATION_BATCH_SIZE` | 50 |

Staging exige `DRY_RUN=True` para e-mail operacional.

## RBAC

Reutiliza `knowledge_base.view` — usuário vê apenas próprias notificações.

## Retenção

Lidas: 90 dias · Falhas: 180 dias (configurável).

## Integração

Hooks em:

- `operational_alert_sync`
- `operational_work_queue_services`
- `alert_governance_services`

Central de Saúde exibe seção compacta de notificações.

## Analytics (Fase 16)

Leitura agregada de métricas operacionais — ver `docs/phase16_operational_analytics.md`.
