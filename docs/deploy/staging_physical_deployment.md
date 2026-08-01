# Deploy físico — staging GP (`granimarmores-pitondo`)

Runbook reproduzível para concluir o staging na VPS **sem alterar produção**.

## Mapa de isolamento

| Recurso | Produção (NÃO ALTERAR) | Staging |
|---|---|---|
| Diretório | `/home/livia.smartcontrolbrasil.com.br/livia_platform` | `/home/staging-livia.smartcontrolbrasil.com.br/livia_platform` |
| Branch | `main` @ `a19e40e` | `chore/postgresql-readiness` @ `97309b1` |
| systemd | `livia-platform.service` | `livia-staging.service` |
| Gunicorn | `127.0.0.1:8011` | `127.0.0.1:8012` |
| Banco | SQLite | PostgreSQL `livia_staging` |
| Usuário Linux | (site produção) | `livia-staging` |
| Host público | `livia.smartcontrolbrasil.com.br` | `staging-livia.smartcontrolbrasil.com.br` |

**Staging concluído não autoriza piloto de produção automaticamente.**

Legenda de comandos:

```text
[VPS]      executar no servidor (shell autenticado)
[LOCAL]    executar no checkout local (validação/desenvolvimento)
[READONLY] não altera dados
[DESTRUCT] altera estado (migrations, systemd, DNS)
```

---

## 1. DNS `[VPS/painel DNS]` `[DESTRUCT]`

Criar registro **A**:

```text
staging-livia.smartcontrolbrasil.com.br → 129.121.55.23
```

Validar propagação `[READONLY]`:

```bash
dig +short staging-livia.smartcontrolbrasil.com.br
```

Esperado: `129.121.55.23`

---

## 2. Usuário Linux `[VPS]` `[DESTRUCT]`

```bash
sudo useradd -m -s /bin/bash livia-staging
sudo mkdir -p /home/staging-livia.smartcontrolbrasil.com.br
sudo chown livia-staging:livia-staging /home/staging-livia.smartcontrolbrasil.com.br
```

---

## 3. Checkout do código `[VPS]` `[DESTRUCT]`

```bash
sudo -u livia-staging git clone <url-do-repo> /home/staging-livia.smartcontrolbrasil.com.br/livia_platform
cd /home/staging-livia.smartcontrolbrasil.com.br/livia_platform
git checkout chore/postgresql-readiness
git rev-parse HEAD   # esperado: 97309b1 ou posterior compatível
```

---

## 4. Virtualenv `[VPS]` `[DESTRUCT]`

```bash
cd /home/staging-livia.smartcontrolbrasil.com.br/livia_platform
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -V
.venv/bin/pip show Django psycopg pgvector | rg '^(Name|Version):'
```

---

## 5. PostgreSQL `[VPS]` `[DESTRUCT]`

```bash
sudo -u postgres psql -c "CREATE USER livia_staging_user WITH PASSWORD '<senha-forte>';"
sudo -u postgres psql -c "CREATE DATABASE livia_staging OWNER livia_staging_user;"
sudo -u postgres psql -d livia_staging -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d livia_staging -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

**Confirmar visualmente:** database = `livia_staging`, **nunca** banco de produção.

---

## 6. `.env` `[VPS]` `[DESTRUCT]`

```bash
cp deploy/staging/livia-staging.env.example .env
chmod 600 .env
# editar placeholders: SECRET_KEY, DATABASE_URL, OpenAI keys, service account path
```

Template versionado: `deploy/staging/livia-staging.env.example`

---

## 7. Pré-deploy check `[VPS]` `[READONLY]`

Antes de migrations:

```bash
cd /home/staging-livia.smartcontrolbrasil.com.br/livia_platform
.venv/bin/python scripts/staging_predeploy_check.py --env-file .env
```

Exit codes:

```text
0 = PASS (pronto)
1 = WARN (revisar)
2 = FAIL (config inválida — abortar)
```

Flags úteis:

```bash
.venv/bin/python scripts/staging_predeploy_check.py --allow-dirty --skip-django
```

---

## 8. Migrations `[VPS]` `[DESTRUCT]`

Somente após predeploy **PASS** e confirmação do DB:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py environment_readiness --tenant granimarmores-pitondo
.venv/bin/python manage.py database_readiness
```

---

## 9. Onboarding GP `[VPS]` `[DESTRUCT]`

```bash
.venv/bin/python manage.py seed_initial_tenants
.venv/bin/python manage.py onboard_tenant \
  --slug granimarmores-pitondo \
  --name "Granimármores Pitondo" \
  --domain granimarmorespitondo.com.br \
  --use-ai \
  --widget-enabled \
  --allowed-origin https://www.granimarmorespitondo.com.br
.venv/bin/python manage.py configure_assistant_profile \
  --tenant granimarmores-pitondo \
  --enable-ai \
  --enable-grounded-synthesis
```

---

## 10. RAG corpus + embeddings `[VPS]` `[DESTRUCT]` (GP only)

Seguir `docs/rag_staging_runbook.md`:

```bash
.venv/bin/python manage.py configure_tenant_rag --tenant granimarmores-pitondo
# sync / index conforme runbook (Google Drive readonly)
.venv/bin/python manage.py rag_vector_health --tenant granimarmores-pitondo
.venv/bin/python manage.py rag_eval --tenant granimarmores-pitondo --threshold 0.40
```

---

## 11. collectstatic `[VPS]` `[DESTRUCT]`

```bash
.venv/bin/python manage.py collectstatic --noinput
```

---

## 12. systemd `[VPS]` `[DESTRUCT]`

```bash
sudo cp deploy/staging/livia-staging.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/livia-staging.service
sudo systemctl daemon-reload
sudo systemctl enable livia-staging.service
sudo systemctl start livia-staging.service
curl -sS http://127.0.0.1:8012/health/
curl -sS 'http://127.0.0.1:8012/health/?readiness=1'
```

Template: `deploy/staging/livia-staging.service`

---

## 13. OpenLiteSpeed vhost `[VPS]` `[DESTRUCT]`

Adaptar `deploy/staging/openlitespeed-vhost.conf.example` com docRoot/static/acme placeholders.

Proxy exclusivo:

```text
staging-livia.smartcontrolbrasil.com.br → http://127.0.0.1:8012
```

Validar sintaxe → **reload seguro** (não reiniciar produção desnecessariamente).

---

## 14. TLS `[VPS/painel]` `[DESTRUCT]`

Emitir Let's Encrypt **somente após DNS propagado**.

```bash
echo | openssl s_client -connect staging-livia.smartcontrolbrasil.com.br:443 \
  -servername staging-livia.smartcontrolbrasil.com.br 2>/dev/null \
  | openssl x509 -noout -subject -dates
```

---

## 15. Pós-deploy check `[LOCAL ou VPS]` `[READONLY]`

```bash
python scripts/staging_postdeploy_check.py \
  --base-url https://staging-livia.smartcontrolbrasil.com.br \
  --tenant granimarmores-pitondo \
  --origin https://www.granimarmorespitondo.com.br
```

Smoke mínimo opcional (1 interação marcada):

```bash
python scripts/staging_postdeploy_check.py \
  --base-url https://staging-livia.smartcontrolbrasil.com.br \
  --chat-smoke
```

---

## 16. Soak HTTP `[LOCAL ou VPS]` `[READONLY]` (OpenAI real no servidor)

```bash
export LIVIA_SOAK_BASE_URL='https://staging-livia.smartcontrolbrasil.com.br'
export LIVIA_SOAK_ORIGIN='https://www.granimarmorespitondo.com.br'
python scripts/phase19_http_soak.py
```

Gate: **0 falhas críticas**, ≥32 interações.

---

## 17. Telemetria `[VPS]` `[READONLY]`

```bash
.venv/bin/python manage.py ai_usage_report --tenant granimarmores-pitondo --days 1
.venv/bin/python manage.py staging_deployment_report --tenant granimarmores-pitondo --json
```

---

## 18. GO/NO-GO staging

Autorizar **continuidade do gate de piloto** somente se:

```text
predeploy PASS
database_readiness READY
environment_readiness READY
postdeploy PASS (ou WARN justificado)
soak HTTP 0 falhas críticas
telemetria observável
produção intacta (8011 / SQLite / main)
```

---

## Artefatos versionados (Fase 20)

| Arquivo | Função |
|---|---|
| `deploy/staging/livia-staging.env.example` | template `.env` |
| `deploy/staging/livia-staging.service` | unit systemd |
| `deploy/staging/openlitespeed-vhost.conf.example` | vhost staging |
| `scripts/staging_predeploy_check.py` | gate pré-deploy |
| `scripts/staging_postdeploy_check.py` | gate pós-deploy HTTP |
| `scripts/phase19_http_soak.py` | soak físico |
| `tenants/management/commands/staging_deployment_report.py` | relatório sanitizado |

Proteções compartilhadas: `tenants/services/staging_deployment.py`
