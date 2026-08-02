# Handoff humano da Lívia

A camada de handoff registra quando uma conversa precisa ser encaminhada para uma pessoa, sem depender de painel completo, n8n real ou envio real de e-mail nesta fase.

## Quando um handoff é criado

O `HandoffService` avalia a mensagem atual, a discovery e o `LeadDraft` associado. Um `HandoffRequest` é criado quando houver:

- pedido explícito de atendimento humano, como “quero falar com alguém”, “me liga” ou “chama um vendedor”;
- lead qualificado pela Lívia;
- demanda técnica complexa em automação ou manutenção;
- urgência em manutenção, automação ou suporte.

Suporte simples e isolado não gera handoff urgente por padrão.

## Status

- `pending`: registrado e aguardando tratamento operacional.
- `sent`: reservado para quando uma notificação real ou integração confirmar envio.
- `resolved`: atendimento humano resolvido.
- `cancelled`: handoff cancelado.

Enquanto existir handoff `pending` ou `sent` para a conversa, o serviço atualiza o registro existente em vez de criar duplicado.

## Prioridades

- `low`: baixa prioridade operacional.
- `normal`: contato humano comum ou lead qualificado.
- `high`: urgência técnica, manutenção/automação crítica ou suporte urgente.
- `urgent`: reservado para fluxos futuros de emergência real.

## Settings

```python
LIVIA_HANDOFF_NOTIFICATIONS_ENABLED = False
LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN = True
LIVIA_HANDOFF_NOTIFICATION_EMAIL = "contato@smartcontrolbrasil.com.br"
```

Com os defaults atuais, nenhuma notificação real é enviada. O serviço prepara um resultado dry-run, registra log seguro e mantém o handoff disponível para operação posterior.

## Modo dry-run

`HandoffNotificationService.notify(handoff)` retorna um objeto com:

- `success`
- `dry_run`
- `channel`
- `message`

No modo atual, `success=True` e `dry_run=True`, sem transporte externo.

## Resumo operacional

O handoff usa `build_conversation_summary` e `format_conversation_summary_notes` para preencher `summary`. Quando a mensagem atual ainda não foi persistida, ela é anexada ao resumo como “Última mensagem”.

## Plano futuro

- envio real por e-mail transacional;
- webhook n8n;
- painel operacional para pendências;
- SLA por prioridade;
- marcação de responsável;
- sincronização M2M com Smart360 CRM;
- histórico de notificações e tentativas.

## WhatsApp no widget por tenant

A Fase 5 permite mostrar um botão flutuante de WhatsApp no widget somente depois de um pedido explícito de atendimento humano confirmado pelo backend. A configuração fica em `AssistantProfile` e novos tenants continuam com `human_handoff_enabled=False`.

Campos operacionais:

- `human_handoff_enabled`: liga/desliga o CTA humano do tenant.
- `human_handoff_channel`: use `disabled` ou `whatsapp`.
- `handoff_whatsapp_number`: telefone internacional salvo apenas com dígitos. Não salve link `wa.me`.
- `handoff_whatsapp_label`: texto acessível do botão.
- `handoff_whatsapp_message`: texto pré-preenchido enviado ao WhatsApp. Não inclua dados pessoais nem conteúdo da conversa.

A configuração pública do widget expõe apenas `human_handoff_enabled`, `human_handoff_channel` e `handoff_whatsapp_label`. O número bruto não é enviado ao navegador na configuração inicial. A URL `https://wa.me/...` é construída internamente e enviada apenas na resposta do `/api/chat/` quando houver `HandoffRequest` elegível com reason `explicit_request`.

### Ativação posterior na VPS

Depois do deploy e da migration, ativar o Smart Control Brasil pelo shell Django, sem editar dados de outros tenants:

```bash
cd /var/www/livia-platform
source .venv/bin/activate
python manage.py shell -c "from tenants.models import Tenant; t=Tenant.objects.get(slug='smart-control-brasil'); p=t.assistant_profile; p.human_handoff_enabled=True; p.human_handoff_channel='whatsapp'; p.handoff_whatsapp_number='551151968525'; p.handoff_whatsapp_label='Falar com um especialista'; p.handoff_whatsapp_message='Olá, vim pelo atendimento da Lívia e gostaria de continuar com um especialista.'; p.full_clean(); p.save(update_fields=['human_handoff_enabled','human_handoff_channel','handoff_whatsapp_number','handoff_whatsapp_label','handoff_whatsapp_message','updated_at'])"
```

Validação manual após ativar:

1. Abrir uma página com o widget do tenant.
2. Confirmar que o botão de WhatsApp inicia oculto.
3. Enviar uma frase como “quero falar com uma pessoa”.
4. Confirmar que o `HandoffRequest` fica pendente no Admin/painel e que a resposta JSON contém `human_handoff.active=true`.
5. Conferir que o botão abre nova aba com `https://wa.me/551151968525?...` e mensagem pré-preenchida, sem envio automático.

## Entrega externa via outbox

A criação de `HandoffRequest` grava `handoff.created` na outbox na mesma transação. Notificações e webhooks de handoff são processados posteriormente por `process_outbox`, preservando o atendimento local mesmo se a integração externa estiver indisponível.
