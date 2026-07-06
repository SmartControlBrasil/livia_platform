# Migração Lívia: Smart360 -> Lívia Platform

Data desta etapa: 2026-07-06

## Objetivo

Transferir a Lívia do `smart360` para `livia-platform` de forma controlada, modular e incremental, sem copiar o monólito inteiro e sem integrar OpenAI nesta fase.

## O que será migrado

### 1. Núcleo conversacional

Origem: `smart360/apps/livia_assistant/services.py`

Destino: `assistant_core/services/`

Responsabilidade esperada:
- orquestrar o fluxo de resposta da assistente
- ler contexto da conversa
- decidir qual caminho seguir: descoberta, qualificação, handoff, resumo
- preparar integração futura com IA externa

### 2. Prompts

Origem: `smart360/apps/livia_assistant/prompts.py`

Destino: `assistant_core/prompts/`

Responsabilidade esperada:
- guardar prompts de sistema e variações de contexto
- manter o texto separado da lógica

### 3. Discovery

Origem: `smart360/apps/livia_assistant/discovery.py`

Destino: `assistant_core/discovery/`

Responsabilidade esperada:
- identificar intenção comercial
- detectar contexto consultivo
- decidir quando aprofundar descoberta

### 4. Qualificação

Origem: `smart360/apps/livia_assistant/qualification.py`

Destino: `assistant_core/qualification/`

Responsabilidade esperada:
- validar nome, empresa, cidade, telefone e e-mail
- evitar capturas inválidas ou genéricas
- sinalizar quando um lead está pronto para notificação

### 5. Lead state

Origem: `smart360/apps/livia_assistant/lead_state.py`

Destino recomendado: `leads/services/`

Responsabilidade esperada:
- modelar o estado do fluxo de coleta
- decidir próximo campo necessário
- permitir transições previsíveis

### 6. Conversation summary

Origem: `smart360/apps/livia_assistant/conversation_summary.py`

Destino recomendado: `assistant_core/services/` ou `leads/services/`

Responsabilidade esperada:
- gerar resumo executivo da conversa
- extrair pontos-chave
- preparar texto para handoff e CRM

### 7. CRM bridge

Origem: `smart360/apps/livia_assistant/crm_bridge.py`

Destino: `integrations/smart360/client.py` e módulos correlatos em `integrations/smart360/`

Responsabilidade esperada:
- substituir chamadas diretas ao monólito por integração HTTP/API
- preparar ingestão de leads para o Smart360 ou sistema intermediário

### 8. RAG

Origem: `smart360/apps/livia_assistant/rag/`

Destino: `knowledge_base/rag/`

Responsabilidade esperada:
- indexação e recuperação de conhecimento
- processamento de documentos
- base para busca semântica futura

### 9. Widget templates e frontend

Origem: `smart360/apps/livia_assistant/templates/livia_assistant/`

Destino: `widget/`

Responsabilidade esperada:
- exibir o widget público
- carregar conversa por tenant
- manter apresentação desacoplada do backend legado

### 10. Views e URLs

Origem: `smart360/apps/livia_assistant/views.py` e `urls.py`

Destino:
- `assistant_core/views.py` para API de chat
- `widget/views.py` para asset público
- `assistant_core/urls.py` e `widget/urls.py` para rotas

Responsabilidade esperada:
- manter endpoints estáveis
- delegar a lógica para serviços e contratos

### 11. Tests

Origem: `smart360/apps/livia_assistant/tests/`

Destino:
- `assistant_core/tests.py`
- `integrations/tests.py`
- `leads/tests.py`
- `knowledge_base/tests.py`
- `widget/tests.py`
- `conversations/tests.py`
- `tenants/tests.py`

Responsabilidade esperada:
- cobrir contratos, fluxo mínimo e integração de borda

## O que fica no Smart360

- dashboards administrativos do monólito
- integrações internas acopladas ao Growth Engine
- qualquer import direto de models Django do Smart360
- rotinas específicas de operação que ainda dependem do legado

## O que será substituído por HTTP/API

- `crm_bridge.py` no futuro deve falar com um cliente HTTP
- integrações entre plataformas devem passar por contrato explícito
- qualquer dependência direta do ORM do Smart360 deve ser removida da plataforma standalone

## Riscos

1. Copiar o monólito inteiro e repetir dependências que não fazem parte da nova arquitetura.
2. Criar acoplamento direto com Smart360 antes de definir contratos estáveis.
3. Migrar RAG e IA cedo demais e misturar responsabilidades com a base de dados nova.
4. Perder compatibilidade de payloads do widget ao mudar o endpoint público.
5. Duplicar regras de qualificação sem uma camada comum bem definida.

## Ordem recomendada de migração

1. Contratos de integração e documentação.
2. Núcleo conversacional mínimo sem IA real.
3. Qualificação e lead state.
4. Conversation summary e handoff.
5. RAG em módulo isolado.
6. Widget completo.
7. Substituição do bridge por HTTP real.

## Princípio desta fase

Nesta etapa, a Lívia Platform deve ganhar a estrutura, os contratos e os pontos de extensão. O comportamento inteligente ainda pode permanecer mockado enquanto a transferência modular é validada.
