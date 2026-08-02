# Idempotência do chat público

O chat público exige `request_id` em cada envio de mensagem. O widget gera um UUID por mensagem usando `crypto.randomUUID()` quando disponível e um fallback UUID-like quando necessário. Retries da mesma tentativa reutilizam o mesmo `request_id`; uma nova mensagem sempre recebe outro valor.

## Contrato público

Headers aceitos:

- `X-Livia-Tenant`: slug público do tenant.
- `X-Livia-Request-ID`: mesmo UUID enviado no JSON, usado para observabilidade.

Payload mínimo:

```json
{
  "tenant": "tenant-slug",
  "session_id": "session-stable-id",
  "request_id": "00000000-0000-4000-8000-000000000000",
  "message": "Olá",
  "source_page": "https://site.example/pagina"
}
```

Se header e payload trouxerem `request_id`, os dois devem coincidir. `request_id` ausente, inválido ou divergente retorna `400` e não cria `ChatRequest`.

## Fingerprint

A plataforma calcula um SHA-256 determinístico sobre dados normalizados: tenant, session_id, request_id, mensagem e source_page. O texto completo da mensagem não é salvo em `ChatRequest`; apenas o hash fica persistido em `request_fingerprint`.

Mesmo `request_id` com mesmo fingerprint é retry legítimo. Mesmo `request_id` com fingerprint diferente retorna `409` com `request_id_conflict` e não cria Conversation, Message, LeadDraft ou HandoffRequest adicional.

## Fluxo

1. Valida tenant e origin. Falhas nessa etapa não criam `ChatRequest`.
2. Valida session, message e request_id.
3. Reserva `ChatRequest` com status `processing`; a constraint `tenant + session_id + request_id` é a defesa final contra corrida.
4. Executa rate limit/spam. Essas respostas controladas são concluídas no próprio `ChatRequest`, para que retry receba o mesmo status/payload.
5. Processa persistência local em `transaction.atomic()` e conclui o `ChatRequest` com payload determinístico.
6. Opcionalmente refina o texto final com IA **fora** da transação de negócio e atualiza apenas `Message` assistente + `response_payload` do `ChatRequest` já `completed`.

O primeiro processamento retorna `X-Livia-Idempotent-Replay: false`. Replay concluído retorna o payload salvo e `X-Livia-Idempotent-Replay: true`, sem reexecutar efeitos locais (Conversation, Message, LeadDraft, HandoffRequest e outbox).

## Concorrência

O processamento persistente cria ou carrega a `Conversation` dentro da transação e usa `select_for_update()` quando o backend suporta. A unique constraint de `Conversation(tenant, session_id)` continua protegendo contra duplicação de conversa; em corrida, `IntegrityError` esperado é tratado apenas no ponto de criação concorrente.

SQLite é suficiente para testes funcionais, mas não oferece o mesmo bloqueio de linhas do PostgreSQL. Produção com múltiplos workers deve usar PostgreSQL para serialização confiável por conversa; a validação final de lock (`select_for_update`) precisa ser repetida em PostgreSQL.

## Estados e falhas

- `processing`: se recente, retorna `409 request_in_progress`; o widget espera pouco e tenta novamente com o mesmo `request_id`.
- `processing` abandonado: após `LIVIA_CHAT_PROCESSING_TIMEOUT_SECONDS`, pode ser recuperado com o mesmo fingerprint.
- `completed`: replay imediato com payload/status persistidos.
- `failed`: retry recente retorna `409 request_failed_retry`; após timeout, o request pode ser recuperado e reprocessado.
- exceção inesperada após reserva marca `failed`, registra log e retorna resposta pública `500` sem traceback.

## Logs

Eventos operacionais usam logs curtos: reserva, replay, conflito, in_progress, recuperação, conclusão e falha. Incluem tenant slug, hash curto de session_id, request_id, status, duração e código. Não incluem mensagem, e-mail, telefone, transcript, tokens ou payload completo.

## Retenção e operação

`python manage.py chat_request_report` mostra contagens e candidatos de limpeza em dry-run. A limpeza só ocorre com `--execute-cleanup`, remove apenas `ChatRequest` antigos `completed/failed` e nunca apaga Conversation, Message, LeadDraft ou AuditEvent.

Readiness sinaliza backend inesperado, SQLite em produção, timeout inválido, requests `processing` abandonados e falhas recentes.

## Troubleshooting

- `request_id_required`: widget antigo ou integração manual sem `request_id`.
- `request_id_header_mismatch`: proxy/cliente alterando header ou payload.
- `request_id_conflict`: mesmo request_id foi reutilizado para mensagem/source_page diferente.
- `request_in_progress`: retry chegou enquanto a primeira tentativa ainda processa.
- muitos `failed`: investigar exceções reais no log antes de limpar registros.

## Relação com outbox

Retries idempotentes do chat retornam o `response_payload` salvo e não executam o processamento local novamente; por isso também não criam eventos `OutboxEvent` duplicados. Eventos comerciais são gravados somente no primeiro processamento efetivo.
