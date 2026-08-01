# Cutover SQLite para PostgreSQL

Este plano prepara a migração futura da Lívia Platform sem executar migração produtiva nesta fase. O SQLite continua válido para desenvolvimento local; produção deve usar PostgreSQL via `DATABASE_URL`.

## Pré-requisitos

1. Criar instância PostgreSQL e credenciais exclusivas para a Lívia Platform.
2. Validar conectividade com banco vazio usando `DATABASE_URL` sem expor senha em logs. Faça um ensaio completo em ambiente não produtivo antes de qualquer janela real.
3. Rodar `python manage.py database_readiness` apontando para o PostgreSQL vazio; antes das migrations ele deve indicar migrations pendentes.
4. Aplicar migrations no PostgreSQL vazio com `python manage.py migrate`.
5. Rodar `python manage.py database_readiness` novamente e confirmar `READY`.

## Janela de manutenção

1. Anunciar janela de manutenção.
2. Interromper apenas `livia-platform.service` antes de gerar backup/export/carga final, parando o Gunicorn da Lívia durante a janela. Não mexer no Smart360.
3. Fazer backup consistente do SQLite produtivo com uma das opções abaixo. Não use `cp db.sqlite3` enquanto a aplicação estiver escrevendo no arquivo.

```bash
sqlite3 /caminho/db.sqlite3 ".backup '/caminho/backup/livia-$(date +%F-%H%M%S).sqlite3'"
```

ou via Python:

```python
import sqlite3
source = sqlite3.connect('/caminho/db.sqlite3')
target = sqlite3.connect('/caminho/backup/livia-backup.sqlite3')
source.backup(target)
target.close()
source.close()
```

4. Fazer backup adicional dos arquivos de aplicação e do `.env` sem exibir segredos em terminal/logs.
5. Tornar os backups imutáveis/readonly e manter por pelo menos sete dias.
6. Não versionar backup, dump ou `.env`.

## Exportação e carga

1. Criar usuário e banco PostgreSQL com privilégio mínimo necessário para a aplicação.
2. Aplicar migrations no PostgreSQL vazio antes de carregar dados.
3. Exportar dados preservando PKs.
4. Avaliar exclusão de `contenttypes` e `auth.permission` apenas quando apropriado. Em geral, para banco migrado com migrations já aplicadas, esses registros podem ser recriados pelo Django e não devem ser importados cegamente sem conferência.
5. Carregar os dados no PostgreSQL. Não usar `pgloader` automaticamente sem avaliação explícita do schema, constraints e campos JSON.
6. Resetar sequences de todas as tabelas carregadas.
7. Verificar FKs e constraints.
8. Testar criação de novo registro após reset de sequences.

## Comparação

Gerar relatórios sem PII nos dois bancos:

```bash
python manage.py database_validation_report > sqlite-report.json
DATABASE_URL='postgresql://USER@HOST:5432/DB?sslmode=require' python manage.py database_validation_report > postgres-report.json
```

Comparar:

- contagens gerais;
- contagens por tenant;
- conversas;
- mensagens;
- leads;
- handoffs;
- knowledge documents;
- profiles;
- webhooks;
- usuários;
- memberships futuramente;
- integridade tenant de `Conversation` → `LeadDraft`/`Handoff`;
- KPIs 7/30/90 do painel.

## Cutover

1. Configurar `DATABASE_URL` PostgreSQL no ambiente produtivo.
2. Configurar `DATABASE_CONN_MAX_AGE` conforme o pool desejado, começando conservador.
3. Reiniciar apenas `livia-platform.service`.
4. Executar smoke tests:
   - `/health/`;
   - `/admin/`;
   - `/painel/`;
   - `/widget.js`;
   - `/api/widget/config/?tenant=smart-control-brasil`;
   - conversa de teste no widget;
   - criação de lead/handoff em tenant de teste.
5. Rodar `python manage.py database_readiness`.
6. Conferir logs sem segredos e KPIs 7/30/90.

## Rollback

1. Interromper `livia-platform.service`.
2. Remover/alterar `DATABASE_URL` para apontar novamente ao backup SQLite, somente se a política de produção permitir temporariamente e de forma controlada.
3. Restaurar o arquivo SQLite a partir do backup imutável.
4. Reiniciar `livia-platform.service`.
5. Executar smoke tests e preservar logs/relatórios da falha.

O rollback para SQLite é uma medida emergencial. A configuração padrão da aplicação é fail-closed em `DEBUG=False` sem `DATABASE_URL`; qualquer rollback deve ser planejado e documentado durante a janela.

## PostgreSQL e pgvector

Producao deve usar PostgreSQL. Para desenvolvimento local com vetores:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

A imagem local recomendada e `pgvector/pgvector:pg16` (extensao `vector` disponivel).
PostgreSQL esperado pelo projeto: **16.x**.

Apos `migrate`, valide:

```bash
python manage.py database_readiness
python manage.py rag_retrieval_report --tenant <slug> --days 7
```

A suíte automatizada continua em SQLite com backend `in_memory`. A busca nativa pgvector exige PostgreSQL + extensao `vector` e nao deve ser fingida em SQLite.

## Locks (`SELECT FOR UPDATE`) e OUTER JOIN

PostgreSQL rejeita:

```sql
SELECT ...
FROM lead
LEFT OUTER JOIN conversation ...  -- FK nullable
FOR UPDATE
```

com:

```text
FOR UPDATE cannot be applied to the nullable side of an outer join
```

SQLite é mais permissivo e mascara o problema.

Padrão adotado no retry CRM do portal (`operations_portal.crm_retry`):

1. Separar **LOCK** de **LOAD GRAPH**.
2. Bloquear apenas `LeadDraft` (escopo `tenant_id` + `pk`) com `select_for_update()`.
3. `select_related("tenant")` é seguro (FK obrigatória → `INNER JOIN`).
4. Não usar `select_related("conversation")` no mesmo queryset do lock (`conversation` é nullable).
5. Em seguida bloquear `OutboxEvent` do aggregate, se existir.
6. Ordem de lock: `LeadDraft` → `OutboxEvent`.
7. HTTP externo (Smart360) permanece fora da transação de enqueue (outbox assíncrona).

Validação:

```bash
# suíte full PostgreSQL local
DATABASE_URL='postgresql://...@127.0.0.1:55432/livia_platform?sslmode=disable' \
  python manage.py test

# testes específicos de concorrência CRM (PostgreSQL-only)
python manage.py test operations_portal.test_crm_retry_concurrency
```

## Collation após troca de imagem Docker

Volumes criados com `postgres:16` e depois reutilizados com `pgvector/pgvector:pg16` podem emitir:

```text
collation version mismatch (ex.: 2.41 → 2.36)
```

Mitigação segura em banco **local descartável**:

```sql
ALTER DATABASE template1 REFRESH COLLATION VERSION;
ALTER DATABASE postgres REFRESH COLLATION VERSION;
ALTER DATABASE livia_platform REFRESH COLLATION VERSION;
```

Não execute `REINDEX DATABASE` indiscriminadamente. Em staging/produção, planeje janela e backup antes de qualquer rebuild de collation.

## Evolução de dimensão de embedding

A coluna `vector(n)` é tipada no schema. Mudar `LIVIA_RAG_EMBEDDING_DIMENSION` sozinho **não** altera a coluna. Exige migration explícita + reindexação.

## Proibições operacionais

- Nunca execute a suíte de testes contra banco produtivo.
- Nunca use `cp` do SQLite enquanto a aplicação estiver escrevendo no arquivo.
- Nunca coloque senha real em documentação, commit, shell history compartilhado ou logs.
- Não use credenciais do PostgreSQL local (`livia_local` / `livia_local_password`) em produção.
