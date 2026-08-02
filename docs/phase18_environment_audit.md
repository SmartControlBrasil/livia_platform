# Fase 18 — Auditoria de Ambientes

Data: 2026-07-31
Repositório: `livia-platform`

---

## 1. Ambientes identificados

| Ambiente | Evidência no repo | Uso nesta fase |
|---|---|---|
| **development** | Default `LIVIA_ENVIRONMENT=development`, SQLite permitido com `DEBUG=True` | Desenvolvimento local |
| **test** | `RUNNING_TESTS`, `manage.py test`, embeddings `fake` permitidos | Suíte automatizada |
| **staging-like** | Fases 16–17: PostgreSQL local + OpenAI real + soak `/api/chat/` | Validação operacional |
| **staging físico** | Doc `docs/deploy/livia_platform_staging.md` (`livia.smartcontrolbrasil.com.br`) — **sem infra versionada** | **Não disponível nesta sessão** |
| **production** | Mesmo doc; flags fail-closed; PostgreSQL obrigatório com `DEBUG=False` | **Não alterado** |

---

## 2. Configuração Django

| Componente | Localização | Notas |
|---|---|---|
| Settings | `config/settings.py` | `LIVIA_ENVIRONMENT`, RAG, OpenAI, CRM, handoff, webhooks |
| Database | `config/database.py` | PostgreSQL produção/staging; SQLite só dev/test |
| WSGI/ASGI | `config/wsgi.py`, `config/asgi.py` | Gunicorn documentado, não versionado |
| Health liveness | `GET /health/` | `config/views.py` |
| Health readiness | `GET /health/?readiness=1` | Config safety (Fase 18) |
| `.env.example` | Raiz | Perfil staging GP comentado |

---

## 3. Infraestrutura deploy

| Item | Existe no repo? |
|---|---|
| `docker-compose.postgres.yml` | Sim — Postgres local dev |
| systemd / nginx / OpenLiteSpeed | Apenas referência em docs |
| `.env.staging` versionado | Não — usar `.env.example` + servidor |
| Staging VM acessível nesta sessão | **Não** |

**Conclusão:** staging físico isolado **não foi validado** nesta fase. Trabalho executado em **staging-like local** com perfil `LIVIA_ENVIRONMENT=staging`.

---

## 4. Side effects externos (resumo)

Ver matriz completa: `docs/environment_side_effect_matrix.md`

---

## 5. Pendências Fase 17 endereçadas

| Pendência | Status Fase 18 |
|---|---|
| Staging físico | Documentado caminho; não executado |
| `SMART360_LEAD_DISPATCH_DRY_RUN=False` local | Gate fail-closed + `environment_readiness` |
| Tokens OpenAI | Modelo `AiUsageEvent` + `ai_usage_report` |
| Telemetria operacional | `rag_operational_report` atualizado |

---

## 6. Comandos de auditoria executados

```bash
python manage.py environment_readiness --tenant granimarmores-pitondo
python manage.py database_readiness
python manage.py rag_vector_health --tenant granimarmores-pitondo
python manage.py check
```

---

## 7. Caminho para staging físico (não executado)

1. Servidor separado (`livia.smartcontrolbrasil.com.br`)
2. `.env` com `LIVIA_ENVIRONMENT=staging`, `DJANGO_DEBUG=False`
3. `SMART360_LEAD_DISPATCH_DRY_RUN=True` (obrigatório)
4. PostgreSQL dedicado (não produção)
5. `TenantAllowedOrigin` para GP
6. `migrate`, `seed_initial_tenants`, RAG sync/index
7. `environment_readiness` → READY
8. `scripts/phase18_staging_soak.py`
