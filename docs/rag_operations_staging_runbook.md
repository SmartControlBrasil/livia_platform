# Runbook — Operações RAG em staging (Fase 8)

Orquestração controlada de inventário, sync, chunks e embeddings via portal + worker.
**Nesta fase: somente simulação (`LIVIA_RAG_OPERATIONS_DRY_RUN=True`).**

## 1. Pré-requisitos

- Branch/commit alvo revisados (`chore/postgresql-readiness` ou sucessor)
- PostgreSQL acessível pelo app
- `.env` de staging **sem** credenciais reais expostas no repositório
- Usuário de serviço dedicado (placeholder: `livia-staging`)
- Python da venv disponível em `/opt/livia-platform/.venv/bin/python` (ajustar paths)

## 2. Backup do banco

```bash
pg_dump -Fc -h <HOST> -U <USER> <DATABASE> > backup-pre-rag-ops-$(date +%Y%m%d).dump
```

## 3. Conferência branch/commit

```bash
git fetch origin
git checkout chore/postgresql-readiness
git log -1 --oneline
```

## 4. Aplicar migrations (staging)

```bash
cd /opt/livia-platform
source .venv/bin/activate
python manage.py migrate knowledge_base
python manage.py migrate audit
python manage.py makemigrations --check --dry-run
```

## 5. Configuração inicial (.env)

```env
LIVIA_RAG_OPERATIONS_ENABLED=True
LIVIA_RAG_OPERATIONS_DRY_RUN=True
LIVIA_RAG_OPERATIONS_LEASE_SECONDS=3600
LIVIA_RAG_OPERATIONS_MAX_ATTEMPTS=3
# NÃO alterar nesta fase:
# LIVIA_RAG_OPERATIONS_DRY_RUN=False
# LIVIA_RAG_INDEXING_ENABLED permanece False até GO explícito
```

Reinicie o app web após alterar `.env`.

## 6. Instalar service/timer (manual)

```bash
sudo cp deploy/staging/livia-rag-operations-worker.service /etc/systemd/system/
sudo cp deploy/staging/livia-rag-operations-worker.timer /etc/systemd/system/
# Edite User, Group, WorkingDirectory, EnvironmentFile conforme o host real
sudo systemctl daemon-reload
sudo systemctl enable --now livia-rag-operations-worker.timer
```

## 7. Validar worker e readiness

```bash
python manage.py tenant_rag_operations_readiness
python manage.py tenant_rag_operations_readiness --tenant <slug>
python manage.py tenant_rag_operations_status
python manage.py tenant_rag_operations_status --tenant <slug> --json
```

Readiness com erro estrutural retorna exit code ≠ 0.

## 8. Solicitar inventário em dry-run

1. Portal → Base de conhecimento → **Atualização da base**
2. Selecionar tenant
3. Operação: **Inventário da origem**
4. Confirmar mensagem de simulação (dry-run)

## 9. Processar e acompanhar

```bash
python manage.py process_tenant_rag_operations --tenant <slug> --limit 1
python manage.py tenant_rag_operations_status --tenant <slug>
journalctl -u livia-rag-operations-worker.service -n 100 --no-pager
```

Portal: verificar status, tentativas, lease e contadores sanitizados.

## 10. Recuperação stale

```bash
python manage.py process_tenant_rag_operations --recover-stale-only --tenant <slug>
python manage.py tenant_rag_operations_status --tenant <slug>
```

Stale marca `error_code=stale_execution` e libera nova solicitação.

## 11. Rollback

1. `LIVIA_RAG_OPERATIONS_ENABLED=False` no `.env`
2. `sudo systemctl disable --now livia-rag-operations-worker.timer`
3. Restaurar backup se necessário (somente em falha grave de schema)

Não apagar corpus/index existente como parte do rollback operacional.

## 12. Interromper teste (NO-GO imediato)

- HTTP 500 no portal ao solicitar operação
- Execução stale crescente sem recuperação
- Cross-tenant em solicitações ou detalhes
- Tentativa de execução real com dry-run desligado nesta fase
- IntegrityError não tratado (500 no POST)

## 13. Critérios GO/NO-GO

| GO | NO-GO |
|----|-------|
| Solicitação dry-run conclui `succeeded` | Falha recorrente `stale_execution` |
| Constraint impede duplicata ativa | Duas operações ativas no mesmo tenant |
| Status/readiness OK | Migration pendente |
| Auditoria sanitizada registrada | Stack trace ou credencial em log/metadata |
| Worker timer executa sem erro | Chamada real Drive/OpenAI nesta fase |

## 14. Proibições desta fase

- `LIVIA_RAG_OPERATIONS_DRY_RUN=False`
- Sync/index real
- Deploy automático do systemd pelo pipeline
- Alteração da VPS além dos comandos documentados executados manualmente pelo operador

## Idempotência HTTP

Não há chave idempotente no POST: duplicatas simultâneas são bloqueadas pela
`unique_active_rag_operation_per_tenant` e pela checagem transacional.
Solicitações sequenciais após conclusão são operações legítimas novas.

## Heartbeat

Heartbeat explícito ocorre apenas entre etapas de `full_reindex` (chunks → embeddings).
Operações indivisíveis em dry-run dependem de lease conservador (`LIVIA_RAG_OPERATIONS_LEASE_SECONDS`).
