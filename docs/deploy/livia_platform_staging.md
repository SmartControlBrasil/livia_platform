# Deploy staging da Lívia Platform

Checklist operacional para preparar `livia.smartcontrolbrasil.com.br` sem executar deploy real nesta fase.

## 1. Preparar ambiente

1. Criar diretório da aplicação no servidor.
2. Clonar ou atualizar o repositório `livia-platform`.
3. Criar `.env` a partir de `.env.example`.
4. Definir `DJANGO_SECRET_KEY` com valor forte e exclusivo.
5. Manter `DJANGO_DEBUG=False` em staging/produção.
6. Configurar `DJANGO_ALLOWED_HOSTS=livia.smartcontrolbrasil.com.br` e hosts auxiliares necessários.
7. Configurar `DJANGO_CSRF_TRUSTED_ORIGINS=https://livia.smartcontrolbrasil.com.br`.
8. Cadastrar origins autorizadas por tenant em `TenantAllowedOrigin`.

## 2. Banco de dados

O projeto mantém SQLite como fallback local em `DEBUG=True`, mas staging/produção exigem `DATABASE_URL` apontando para PostgreSQL quando `DEBUG=False`.

Checklist mínimo:

1. Definir `DATABASE_URL` de staging para PostgreSQL.
2. Confirmar `DJANGO_ALLOW_EXTERNAL_TEST_DATABASE_URL=False` fora de ambiente de teste local.
3. Rodar `python manage.py database_readiness` antes e depois das migrations.
4. Rodar `python manage.py database_validation_report` após carga inicial para comparar contagens sem PII.

## 3. Instalar aplicação

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_initial_tenants
```

Criar superuser apenas quando necessário:

```bash
.venv/bin/python manage.py createsuperuser
```

## 4. Smart360 Growth Engine

1. Para validação inicial, manter:
   - `SMART360_LEAD_DISPATCH_ENABLED=False`
   - `SMART360_LEAD_DISPATCH_DRY_RUN=True`
2. Configurar `SMART360_BASE_URL=https://www.smartcontrolbrasil.com.br`.
3. Definir `SMART360_M2M_TOKEN` somente no servidor, nunca no repositório.
4. Validar dry-run antes de ativar dispatch real.
5. Ativar envio real somente depois de confirmar token, endpoint e recebimento no Growth Engine:
   - `SMART360_LEAD_DISPATCH_ENABLED=True`
   - `SMART360_LEAD_DISPATCH_DRY_RUN=False`

## 5. App server e proxy

Configurar Gunicorn ou equivalente apontando para `config.wsgi:application`.

Exemplo de comando de smoke test local no servidor:

```bash
.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000
```

Configurar OpenLiteSpeed/LiteSpeed como proxy/app server para o processo WSGI/ASGI escolhido.

## 6. HTTPS

1. Emitir certificado TLS para `livia.smartcontrolbrasil.com.br`.
2. Forçar HTTPS no proxy.
3. Confirmar que `CSRF_TRUSTED_ORIGINS` usa `https://`.

## 7. Smoke tests

1. Abrir `https://livia.smartcontrolbrasil.com.br/demo/`.
2. Abrir `https://livia.smartcontrolbrasil.com.br/widget.js`.
3. Testar `POST /api/chat/` com tenant válido.
4. Testar embed em site externo cadastrado em `TenantAllowedOrigin`.
5. Testar origin não autorizado e confirmar ausência de headers CORS permissivos.
6. Criar lead qualificado com dispatch dry-run.
7. Conferir logs do app para eventos `crm_dispatch_*`.
8. Ativar dispatch real apenas após validação de token e endpoint.
