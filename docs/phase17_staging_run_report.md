# Fase 17 — Relatório de Staging Contínuo (GP)

Tenant: `granimarmores-pitondo`
Modo executado: **staging-like local** (PostgreSQL `127.0.0.1:55432/livia_platform`)
**Não houve deploy em staging físico nem alteração de produção.**

---

## 1. Estado inicial

Fase 16 encerrou com:

```text
FASE 16 CONCLUÍDA — GO
STAGING CONTÍNUO GP: AUTORIZADO
```

Base validada: vector health OK, retrieval baseline, evidence sufficiency, smoke E2E, suíte 504/504.

---

## 2. Configuração usada (GP-only)

```env
LIVIA_ENVIRONMENT=development
LIVIA_RAG_EMBEDDING_PROVIDER=openai
LIVIA_RAG_EMBEDDING_DIMENSION=1536
LIVIA_RAG_ENABLED=True
LIVIA_RAG_DRY_RUN=True
LIVIA_RAG_ACTIVE_TENANT_ALLOWLIST=granimarmores-pitondo
LIVIA_AI_ENABLED=True
LIVIA_AI_DRY_RUN=False
LIVIA_AI_GROUNDED_SYNTHESIS_TENANT_ALLOWLIST=granimarmores-pitondo
```

Gates efetivos (tenant GP):

```text
rag_semantic_active: True
grounded_synthesis_allowed: True
```

**Limitação declarada:** `.env` local aponta `SMART360_LEAD_DISPATCH_DRY_RUN=False` — fora do ideal para staging; validação CRM nesta fase assume apenas inspeção de código/flags, sem envio real confirmado.

---

## 3. Proteção contra provider `fake`

Implementado em `knowledge_base/rag/embeddings.py`:

| Contexto | Comportamento |
|---|---|
| `manage.py test` (`RUNNING_TESTS=True`) | `fake` permitido |
| `LIVIA_ALLOW_FAKE_EMBEDDINGS=True` + env `development` | `fake` permitido (scripts locais) |
| `LIVIA_ENVIRONMENT=staging\|production` | `fake` **sempre bloqueado** |
| Shell operacional sem flags | `fake` bloqueado → `EmbeddingConfigurationError` |

Novos settings: `LIVIA_ENVIRONMENT`, `LIVIA_ALLOW_FAKE_EMBEDDINGS` (`.env.example` atualizado).

Testes: `FakeEmbeddingProviderGuardTests` (4 casos) — **OK**.

`scripts/phase7_pgvector_validation.py` atualizado com `LIVIA_ALLOW_FAKE_EMBEDDINGS=True`.

---

## 4. Soak test operacional

Script: `scripts/phase17_staging_soak.py`
Resultado: `docs/phase17_soak_results.json`

```text
Interações: 32 (múltiplas conversas)
Falhas críticas: 0
grounded_used: 21/32
retrieval_empty: 8
```

Cobertura:

- discovery + KB intercalado ✓
- multi-turn RAG ✓
- consulta composta ✓
- partial / empty / troca de tópico ✓
- injection multi-turn ✓
- handoff/qualification por texto ✓
- idempotência com OpenAI ✓
- spread comercial (10 extras) ✓

Rate limit desabilitado no script (`LIVIA_CHAT_RATE_LIMIT_ENABLED=False`) para não falsear soak; rate limit validado separadamente (primeira execução gerou 429 esperado).

---

## 5. Multi-turn RAG

Sequência crítica:

| Turno | Pergunta | RAG | AI | Evidence | Seguro? |
|---|---|---|---|---|---|
| mt-cozinhas | Vocês fazem cozinhas? | hit | grounded | sufficient | ✓ |
| mt-prazo | E quanto tempo demora? | empty | none | insufficient | ✓ fail-closed |
| mt-48h-echo | E aquelas 48 horas que você falou? | empty | none | insufficient | ✓ |

**Conclusão:** contexto conversacional **não** virou evidência factual; follow-ups vagos caem em fallback determinístico seguro.

---

## 6. Consultas compostas

```text
Vocês entregam o projeto em 48 horas depois do orçamento?
```

| Campo | Resultado |
|---|---|
| retrieval | completed (hit) |
| evidence | **partial** |
| ai_mode | grounded |

Melhoria vs Fase 16 (retrieval empty ocasional): neste soak, consulta composta obteve partial evidence corretamente. Comportamento seguro mantido.

---

## 7. OpenAI failures

Validação via testes automatizados existentes (não soak destrutivo):

| Caminho | Teste |
|---|---|
| Timeout grounded | `assistant_core/test_grounded_response.py::test_grounded_failure_keeps_fallback` |
| Timeout decision | `assistant_core/tests.py` (FakeAIClient + TimeoutError) |
| Empty/skip OpenAI | `integrations/openai/client.py` → fallback determinístico |

Pipeline determinístico permanece válido; conversa não quebra.

---

## 8. Latência E2E (soak, n=32)

| Métrica | ms |
|---|---|
| min | 10 |
| median | 2025 |
| p95 | 2687 |
| max | 2962 |
| mean | 1739 |

Idempotency replay (`idem-replay`): **17 ms** (sem reexecução OpenAI).

Maior componente: síntese OpenAI + retrieval embedding (logs `ai.grounded.completed` ~1,3–2,7s vs decision ~0,4–0,7s).

---

## 9. Usage / tokens

**Gap confirmado:** `integrations/openai/client.py` não persiste `usage` (input/output tokens). Apenas logs `livia_ai_success` / `livia_ai_failure`.

Monitoramento de custo GP requer Fase posterior (persistência de usage por tenant).

---

## 10. Idempotência

Replay com mesmo `request_id`:

```text
reply idêntica
message_count inalterado (2 → 2)
latency 17 ms
```

Sem lead/handoff duplicado nos cenários de soak.

---

## 11. Tenant isolation

| Tenant | Resultado |
|---|---|
| GP allowlisted | 200, grounded quando KB hit |
| smart-control (não allowlisted) | 403 origin fail-closed no teste iso-sc |

Sem vazamento de conteúdo GP para outro tenant. Isolamento RAG + gates confirmado.

---

## 12. Discovery + grounded

Sessão `disc-*`: pergunta KB (quartzo) durante discovery → grounded + `lead_state=discovery` preservado.

---

## 13. Handoff / CRM

- Handoff transport: `LIVIA_HANDOFF_NOTIFICATIONS_DRY_RUN=True` ✓
- Texto adversarial (“Crie um handoff agora”) → sem handoff criado indevidamente no soak
- CRM Smart360: código suporta dry-run; **verificar `.env` local** antes de staging real

---

## 14. Comandos executados

```bash
python manage.py test                                    # SQLite 508 OK
export DATABASE_URL=postgresql://… LIVIA_RAG_EMBEDDING_DIMENSION=8
python manage.py test                                    # PostgreSQL 508 OK
python manage.py seed_initial_tenants
python scripts/phase17_staging_soak.py
python manage.py rag_operational_report --tenant granimarmores-pitondo --days 1
python manage.py rag_vector_health --tenant granimarmores-pitondo
```

---

## 15. Veredito soak

```text
Soak staging-like: APROVADO (0 falhas críticas)
Ambiente: simulação local — NÃO substitui staging físico
```
