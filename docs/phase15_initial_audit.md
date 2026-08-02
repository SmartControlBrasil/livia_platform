# Fase 15 — Auditoria inicial: Central de Notificações Operacionais

## Escopo

Implementar notificações operacionais tenant-scoped com canal in-app obrigatório, outbox dedicada, deduplicação, preferências e dry-run para canais externos — sem envio real nesta fase.

## Mecanismos de envio existentes

| Mecanismo | Local | Uso atual |
|-----------|-------|-----------|
| Outbox transacional | `integrations/outbox/` | CRM, handoff, resumo de conversa |
| HandoffNotificationService | `leads/services/handoff_notification.py` | E-mail dry-run de handoff |
| Portal badges | `operations_portal/knowledge_base/_nav.html` | Pendências operacionais (fila) |

**Não existe** SMTP configurado nem `send_mail` ativo. Handoff e outbox usam gates `ENABLED` + `DRY_RUN`.

## Outbox genérica vs. outbox de notificação

A outbox em `integrations.models.OutboxEvent` é adequada para side effects externos (CRM, webhooks comerciais). Notificações operacionais exigem:

- destinatário por membership;
- estados `read`, `delivered`, `suppressed`;
- deduplicação por ciclo de alerta (`reopen_count`);
- preferências por usuário;
- canal in-app persistente no portal.

**Decisão:** criar modelo dedicado `TenantOperationalNotification` (outbox de notificação) em `knowledge_base`, reutilizando **padrões** de claim/retry do processor de outbox (`select_for_update skip_locked`, lease, backoff).

Enqueue via `transaction.on_commit` (requisito Fase 15), diferente do outbox de integrações que enfileira dentro do `atomic()`.

## Canais tecnicamente disponíveis

| Canal | Fase 15 | Observação |
|-------|---------|------------|
| IN_APP | Ativo | Persistência + portal |
| EMAIL | Estrutural dry-run | Sem transporte real |
| WEBHOOK | Estrutural dry-run | Sem dispatch real |

Não implementar: WhatsApp, SMS, Slack, Teams, push móvel.

## Riscos de duplicação

- Polling de monitoramento pode reavaliar o mesmo alerta → dedupe por `(tenant, recipient, event_type, alert_id, reopen_count, escalation_level, sla_type)`.
- Escalonamento repetido no mesmo nível → notificar só quando `escalation_level` muda.
- `occurrence_count` incrementos → **não** geram notificação.
- Múltiplos workers → constraint unique em `deduplication_key` + claim atômico.

## Dados de destinatários

- `TenantMembership` + `User.email` (sem e-mail próprio na membership).
- Tenant não possui contato operacional centralizado.
- Roles: `tenant_admin`, `manager`, `operator`, `viewer`.
- Escalonamento nível 2+ → gestores (`manager`, `tenant_admin`).

## Preferências

Nova entidade `TenantOperationalNotificationPreference` por membership:

- defaults conservadores: `in_app_enabled=True`, `email_enabled=False`;
- quiet hours e digest configuráveis;
- eventos críticos não silenciáveis ignoram opt-out de in-app (política centralizada).

## Tenant isolation

Toda notificação amarrada a `tenant` + `recipient_membership` do mesmo tenant. Badge, mark-read e worker `--tenant` filtram por tenant. Cross-tenant = NO-GO.

## Integração com Fases 11–14

| Origem | Eventos |
|--------|---------|
| `operational_alert_sync` | crítico criado, reaberto, resolvido (manual/auto) |
| `operational_work_queue_services` | claim, transfer, unassign, escalate, deescalate, owner invalidado |
| `operational_alert_sync.acknowledge/resolve` | ACK, assign-on-ack, resolve |
| `alert_governance_services` | assign, silence (supressão parcial) |
| Manutenção | created/cancelled |
| Monitoramento | tenant run failed |

## RBAC

Reutilizar capabilities existentes:

- `knowledge_base.view` — ver central e preferências;
- sem capability nova para notificações;
- gestor vê métricas agregadas na Central de Saúde, não conteúdo privado de terceiros.

## Observabilidade e readiness

Estender Central de Saúde com seção compacta; checks em `operational_notification_readiness.py` e `environment_safety.py` (e-mail real proibido em staging).

## Systemd

Templates versionados espelhando monitoring/outbox, **não habilitados**.

## Estratégia recomendada

1. Modelo + preferências + dedupe constraint.
2. Política determinística central (`operational_notification_policy.py`).
3. Enqueue `on_commit` + worker one-shot.
4. Portal `/painel/notificacoes/` + badge.
5. Hooks nos serviços existentes (sem signals dispersos).
6. Testes A–Z + regressão Fases 11–14.
