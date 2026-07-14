# OpenAI opcional da Lívia

A livia-platform possui uma camada opcional de IA generativa para refinar o texto final da resposta da Lívia. Ela não substitui a decisão determinística: discovery, lead_state, validação de lead, handoff, dispatch CRM e recuperação de conhecimento continuam sendo executados antes da IA.

## Defaults seguros

Por padrão, a IA fica desligada e em dry-run:

```env
LIVIA_AI_ENABLED=False
LIVIA_AI_DRY_RUN=True
LIVIA_OPENAI_API_KEY=
LIVIA_OPENAI_MODEL=gpt-4.1-mini
LIVIA_OPENAI_TIMEOUT_SECONDS=8
LIVIA_OPENAI_MAX_OUTPUT_TOKENS=350
LIVIA_OPENAI_TEMPERATURE=0.3
```

Com esses defaults, nenhuma chamada real é enviada à OpenAI. Se a chave estiver ausente, a resposta determinística é preservada.

## Ativação por tenant/profile

A IA só tenta rodar quando duas condições são verdadeiras:

1. `LIVIA_AI_ENABLED=True` nas settings/ambiente.
2. `AssistantProfile.use_ai=True` para o perfil ativo do tenant.

Se não houver `AssistantProfile`, se o perfil estiver inativo ou se `use_ai=False`, a camada de IA fica bloqueada mesmo com a flag global ativa.

## Dry-run

Para validar o caminho sem custo e sem chamada externa:

```env
LIVIA_AI_ENABLED=True
LIVIA_AI_DRY_RUN=True
```

Nesse modo o serviço monta a tentativa de IA, registra logs seguros e mantém a resposta determinística.

## Fallback

O fallback determinístico é obrigatório. A resposta original é mantida quando:

- `LIVIA_AI_ENABLED=False`;
- `AssistantProfile.use_ai=False`;
- `LIVIA_AI_DRY_RUN=True`;
- `LIVIA_OPENAI_API_KEY` está vazia;
- ocorre timeout, erro HTTP, JSON inválido ou resposta vazia;
- qualquer exceção local acontece ao montar prompt ou chamar o cliente.

A IA só pode substituir o texto final quando retorna uma resposta válida. Ela não altera `lead_state`, `LeadDraft`, handoff, CRM, histórico ou contexto de conhecimento.

## Prompt

O prompt recebe contexto de baixo risco operacional:

- tenant;
- AssistantProfile;
- mensagem atual;
- histórico curto;
- DiscoveryResult;
- lead_state;
- knowledge context;
- resumo da conversa;
- resposta determinística já calculada.

As instruções proíbem inventar preço, prazo, garantia, estoque, disponibilidade, especificação técnica ou agenda. A IA também é orientada a não mencionar prompt, JSON, estado interno ou regras internas.

## Logs

Os logs registram apenas sinais operacionais: habilitado/desabilitado, dry-run, modelo, sucesso/falha e tipo de erro. A chave da OpenAI não é logada.

## Limites atuais

Esta fase não inclui streaming, embeddings, RAG avançado, fine-tuning, painel visual de configuração, billing, upload de PDF, dashboard ou n8n real.

## Alerta de custo

Ao desativar `LIVIA_AI_DRY_RUN`, cada resposta elegível pode gerar chamada à API da OpenAI. Ative primeiro em poucos tenants/perfis, monitore logs e acompanhe custos antes de ampliar o uso.
