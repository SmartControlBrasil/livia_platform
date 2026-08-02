# Fase 19 — Provisionamento de staging físico GP

**Data:** 2026-07-31
**Tenant alvo:** `granimarmores-pitondo`
**Status nesta sessão:** **NÃO EXECUTADO** na VPS (SSH recusado; ver `docs/phase19_vps_audit.md`).

Este documento registra **o que foi tentado**, **o que falhou**, e o **runbook exato** para execução quando houver acesso ao servidor — sem descrever passos já concluídos como se fossem factuais.

---

## 1. Bloqueio imediato

| Gate | Resultado |
|---|---|
| DNS `staging-livia.smartcontrolbrasil.com.br` | **Ausente** |
| SSH `marcelo@129.121.55.23:22` | **Connection refused** |
| Checkout staging separado na VPS | **Não executado** |
| DB PostgreSQL staging | **Não criado** |
| systemd `livia-staging.service` | **Não criado** |
| vhost/TLS staging | **Não configurado** |
| Migrations no DB staging | **Não executado** |
| `environment_readiness` no servidor | **Não executado** |
| `database_readiness` no servidor | **Não executado** |

**Produção:** nenhum `.env`, DB, serviço ou vhost de produção foi alterado.

---

## 2. Isolamento exigido (ainda não implementado)

Staging deve ser separado de produção em:

```text
filesystem          → ex.: /home/<user>/livia_staging/  [NÃO CRIADO]
virtualenv          → ex.: livia_staging/.venv           [NÃO CRIADO]
systemd             → livia-staging.service              [NÃO CRIADO]
porta backend       → ex.: 127.0.0.1:8001 (livre)       [NÃO VERIFICADO NA VPS]
database            → ex.: livia_staging                 [NÃO CRIADO]
.env                → exclusivo staging                  [NÃO CRIADO]
logs                → diretório próprio                  [NÃO CRIADO]
host/origin         → subdomínio dedicado + TLS          [DNS AUSENTE]
```

**Não reutilizar:** `.env`, DB, porta Gunicorn ou unit de produção.

---

## 3. Host de staging

Candidato conceitual: `staging-livia.smartcontrolbrasil.com.br`.

Verificação nesta sessão:

```bash
dig +short staging-livia.smartcontrolbrasil.com.br A
# (sem resposta)
```

**Host operacional:** **não declarado** — depende de painel DNS externo + vhost na VPS.

Origin GP para widget (referência do tenant, **não** copiar para staging sem revisão):

```text
https://www.granimarmorespitondo.com.br
```

Para soak/CORS em staging sem site frontend dedicado, usar origin explicitamente cadastrada em `TenantAllowedOrigin` (cliente controlado), **nunca** `*`.

---

## 4. Runbook — execução na VPS (pendente)

> Executar **somente** após SSH funcional e confirmação visual de que `DATABASE_URL` aponta para **staging**, nunca produção.

### 4.1 Auditoria read-only in loco (primeiro login)

```bash
# Não reiniciar produção
hostname
sudo systemctl list-units --type=service --all | grep -iE 'livia|gunicorn|litespeed|postgres'
sudo ss -tlnp | grep -iE '8000|8001|5432|443'
# Identificar WorkingDirectory e EnvironmentFile do serviço de produção
sudo systemctl cat livia.service 2>/dev/null || true
```

Documentar paths reais antes de criar staging.

### 4.2 Filesystem + código

```bash
sudo -u deploy mkdir -p /home/deploy/livia_staging
cd /home/deploy/livia_staging
git clone <url-livia-platform> .
git checkout chore/postgresql-readiness   # ou branch aprovada Fases 12–18
git rev-parse HEAD
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Registrar: Python, Django, OpenAI SDK, psycopg, pgvector (versões de `pip freeze`).

### 4.3 PostgreSQL staging

```bash
sudo -u postgres psql -c "CREATE USER livia_staging WITH PASSWORD '<secret>';"
sudo -u postgres psql -c "CREATE DATABASE livia_staging OWNER livia_staging;"
sudo -u postgres psql -d livia_staging -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Validar:

```bash
sudo -u postgres psql -d livia_staging -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

### 4.4 `.env` staging (mínimo — valores secretos só no servidor)

```env
LIVIA_ENVIRONMENT=staging
DJANGO_DEBUG=False
DATABASE_URL=postgres://livia_staging:<secret>@127.0.0.1:5432/livia_staging
LIVIA_RAG_EMBEDDING_PROVIDER=openai
LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST=granimarmores-pitondo
LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST=granimarmores-pitondo
SMART360_LEAD_DISPATCH_DRY_RUN=True
SMART360_LEAD_DISPATCH_ENABLED=False
LIVIA_WEBHOOKS_DRY_RUN=True
LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN=True
DJANGO_ALLOWED_HOSTS=staging-livia.smartcontrolbrasil.com.br,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://staging-livia.smartcontrolbrasil.com.br
```

Ajustar nomes conforme `.env.example` e `config/settings.py` reais.

### 4.5 Proteção contra DB de produção

Antes de `migrate`:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py environment_readiness --tenant granimarmores-pitondo
# Confirmar DATABASE name/host = livia_staging
# Se qualquer indicação de DB produção → ABORTAR
```

### 4.6 Migrations + onboarding

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_initial_tenants
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain granimarmorespitondo.com.br \
  --use-ai \
  --widget-enabled \
  --allowed-origin https://www.granimarmorespitondo.com.br
.venv/bin/python manage.py database_readiness
# Critério: READY
```

### 4.7 RAG (somente DB staging)

```bash
.venv/bin/python manage.py configure_tenant_rag --tenant granimarmores-pitondo
# sync/index conforme runbook: docs/rag_staging_runbook.md
.venv/bin/python manage.py rag_vector_health --tenant granimarmores-pitondo
# Critério: OK, reindex_required=0, provider openai dim 1536
.venv/bin/python manage.py rag_eval --tenant granimarmores-pitondo --threshold 0.40
```

### 4.8 systemd + porta

Escolher porta livre (ex. `8001`):

```bash
ss -tlnp | grep 8001 || echo "porta livre"
```

Unit exemplo (`/etc/systemd/system/livia-staging.service`):

```ini
[Unit]
Description=Livia Platform Staging (GP pilot gate)
After=network.target postgresql.service

[Service]
User=deploy
Group=deploy
WorkingDirectory=/home/deploy/livia_staging
EnvironmentFile=/home/deploy/livia_staging/.env
ExecStart=/home/deploy/livia_staging/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable livia-staging.service
sudo systemctl start livia-staging.service
curl -sS http://127.0.0.1:8001/health/
curl -sS 'http://127.0.0.1:8001/health/?readiness=1'
```

### 4.9 vhost + TLS staging

- Criar vhost **novo** no LiteSpeed apontando para `127.0.0.1:8001`.
- Emitir certificado Let's Encrypt para hostname staging (após DNS A record).
- `reload` seguro do proxy — **não** editar vhost de produção além do mínimo.

Validação externa real:

```bash
curl -sS 'https://staging-livia.smartcontrolbrasil.com.br/health/?readiness=1'
```

---

## 5. Validações locais nesta sessão (não substituem VPS)

Executado no notebook (PostgreSQL Docker **indisponível** — auth falhou em `127.0.0.1:55432`):

```bash
# Com .env local + overrides staging parciais
python manage.py environment_readiness --tenant granimarmores-pitondo
```

Resultado:

```text
Environment readiness: READY_WITH_WARNINGS
[OK] smart360_dry_run: enabled=False dry_run=True
[WARNING] debug_disabled: DEBUG should be False in staging
[WARNING] tenant_grounded_gate: grounded_synthesis_allowed=False
```

`database_readiness` com `DEBUG=False` e DB inacessível:

```text
connection=unavailable OperationalError (password authentication failed 127.0.0.1:55432)
NOT READY
```

Isso confirma bloqueio local para simular staging físico completo nesta sessão.

---

## 6. Referências

- `docs/deploy/livia_platform_staging.md`
- `docs/rag_staging_runbook.md`
- `docs/phase19_vps_audit.md`
- `docs/environment_side_effect_matrix.md` (Fase 18)

---

## 7. Conclusão do provisionamento

```text
STAGING FÍSICO GP — NÃO PROVISIONADO NESTA SESSÃO
MOTIVO — SEM SSH + SEM DNS STAGING + SEM DB/SERVIÇO NA VPS
```
