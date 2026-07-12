# Checklist E2E da Lívia Platform

Use este roteiro antes de liberar staging/produção.

## Tenant e API

- Tenant inexistente retorna erro controlado.
- Tenant inativo retorna erro controlado.
- `POST /api/chat/` exige tenant, session_id/session_key e message.
- `OPTIONS /api/chat/` responde preflight para origin autorizado.

## Conversa e discovery

- Saudação: `Oi` não cria lead e responde saudação.
- Orçamento vago: `quero orçamento` pergunta área antes de pedir contato.
- Automação: `quero orçamento para CLP Mitsubishi` identifica automação e inicia coleta.
- Robótica: `vocês têm robô de limpeza?` identifica robótica e pergunta aplicação/ambiente.
- Manutenção: `preciso arrumar uma esteira` identifica manutenção e pergunta contexto técnico.
- Software/web: `quero um site com IA` identifica software/web.
- Suporte isolado: `como faço login?` não cria lead.
- Pergunta técnica isolada não qualifica lead automaticamente.
- Contato enviado cedo demais não qualifica sem necessidade mínima.

## Lead e CRM

- Lead com necessidade, nome/empresa e telefone/e-mail fica qualificado.
- `Conversation.is_qualified=True` e `lead_state=qualified` quando o mínimo é atingido.
- Dispatch dry-run gera external id `dry-run-*`.
- Payload para Smart360 inclui `notes` com resumo rico da Lívia.
- Lead já enviado não é reenviado em mensagens seguintes.
- Dispatch real só deve ser testado com token M2M validado.
- Confirmar lead recebido no Growth Engine do Smart360.

## Widget

- `/widget.js` carrega via subdomínio da Lívia.
- Embed externo usa `data-api-url` apontando para `https://livia.smartcontrolbrasil.com.br/api/chat/`.
- Origin autorizado recebe headers CORS.
- Origin não autorizado não recebe `Access-Control-Allow-Origin`.
