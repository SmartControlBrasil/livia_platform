# Outbox transacional

A outbox resolve o risco de efeitos externos serem executados dentro do commit local do chat. O fluxo agora é: transação local salva conversa, lead ou handoff; grava `OutboxEvent`; commit; um worker posterior processa o evento e registra sucesso, retry, skip ou dead letter.

## Fronteira transacional

`LeadDraft` qualificado e `HandoffRequest` criado enfileiram eventos na mesma `transaction.atomic()` que grava o fato local. O enqueue não faz HTTP e usa constraint de deduplicação como garantia final. Se a outbox falhar, o fato local que exige entrega posterior também falha e sofre rollback.

OpenAI permanece fora da outbox nesta fase porque participa da resposta imediata ao visitante. A tentativa opcional de refinamento ocorre após o commit da transação principal do chat e nunca dentro do bloco atômico de persistência de negócio.

## Eventos

Tipos estáveis:

- `lead.qualified`: entrega Smart360 e webhooks de lead.
- `handoff.created`: entrega notificação de handoff e webhooks de handoff.
- `conversation.summary_ready`: reservado para webhook de resumo quando houver consumidor configurado.

Cada evento possui `event_id` UUID estável, `schema_version`, `tenant_slug`, `aggregate_type`, `aggregate_id`, `deduplication_key`, payload mínimo e status operacional.

## Payload seguro

O payload contém envelope e snapshot mínimo. Não inclui transcript completo, prompts, tokens, secrets, Authorization ou resposta externa completa. O handler busca o aggregate pelo tenant do evento; se o objeto mudar depois do enqueue, o handler usa o estado atual do banco e o snapshot serve apenas como referência mínima.

## Deduplicação

A chave lógica padrão é `tenant:event_type:aggregate_type:aggregate_id:v1`, protegida por constraint única. Retry de chat ou reexecução de serviço retorna o evento existente e não cria outro.

## Claim e lock

`process_outbox --execute` faz claim em transação curta: seleciona lote elegível, marca `processing`, define `locked_at/locked_by` e faz commit. O handler roda fora dessa transação. A finalização abre nova transação e só atualiza o evento se `locked_by` ainda pertence ao worker.

PostgreSQL usa `select_for_update(skip_locked=True)` quando disponível. SQLite funciona para desenvolvimento e testes, mas múltiplos workers confiáveis exigem PostgreSQL.

## Retry e dead letter

Falhas temporárias, timeout, conexão, HTTP 408/425/429/5xx viram `retry` com backoff `base * 2^(attempts - 1)`, limitado por `LIVIA_OUTBOX_MAX_RETRY_SECONDS`. HTTP 400/401/403/404/422 e schema inválido viram `dead_letter`. Ao atingir `LIVIA_OUTBOX_MAX_ATTEMPTS`, o evento fica em `dead_letter` sem ser excluído.

Locks `processing` abandonados voltam para `retry` após `LIVIA_OUTBOX_LOCK_TIMEOUT_SECONDS`. Lock recente não é recuperado.

## Comandos

`python manage.py process_outbox` sem `--execute` é dry-run e não chama handlers. Use `--execute --once` para um lote. Opções: `--batch-size`, `--worker-id`, `--event-type`, `--tenant`.

`python manage.py outbox_report` é readonly e mostra totais, eventos vencidos, retries atrasados, locks abandonados e dead letters.

## Admin e auditoria

`OutboxEvent` aparece no Django Admin somente para superuser, somente leitura, sem criação/edição/exclusão manual. A ação de reenfileirar aceita apenas `dead_letter`/`retry` e gera `AuditEvent` `outbox.requeued` sem payload sensível.

## Observabilidade

Logs usam `event_id`, `event_type`, `tenant_slug`, tentativa, worker, duração e código. Não registram payload completo, token, secret, Authorization, transcript ou mensagem do visitante.

## Como adicionar handler

1. Adicione um tipo em `OutboxEvent.EventType`.
2. Crie builder de payload mínimo.
3. Adicione função de enqueue com deduplication key estável.
4. Implemente handler que retorna `HandlerResult`.
5. Registre em `integrations/outbox/registry.py`.
6. Cubra schema, tenant scope, dry-run, retry e dead letter em testes.

## Limitações

Não há Celery, Redis ou scheduler contínuo nesta fase. O processamento é manual/operacional via comando. Produção multi-worker deve usar PostgreSQL.
