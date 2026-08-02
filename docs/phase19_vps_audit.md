# Fase 19 — Auditoria da VPS (somente leitura remota)

**Data:** 2026-07-31 (sessão local `marcelo-HP-250-15-6-inch-G9-Notebook-PC`)
**Escopo:** inspeção **sem alterar produção** e **sem shell na VPS** (SSH indisponível nesta sessão).

---

## 1. Contexto da sessão

| Item | Valor observado |
|---|---|
| Máquina de execução | Notebook local Linux (`marcelo-HP-...`), **não** é a VPS |
| Usuário shell local | `root` (workspace Cursor) / dono do projeto `marcelo` |
| Repositório local | `/home/marcelo/projetos/livia-platform` |
| Branch | `chore/postgresql-readiness` |
| HEAD | `3c334b9` (à frente 9 commits de `origin/chore/postgresql-readiness`) |
| Produção alterada? | **Não** — apenas probes HTTP/TLS/DNS |

---

## 2. DNS

Comandos executados:

```bash
dig +short livia.smartcontrolbrasil.com.br A
dig +short staging-livia.smartcontrolbrasil.com.br A
getent hosts livia.smartcontrolbrasil.com.br
```

Resultados:

| Host | Registro A | Observação |
|---|---|---|
| `livia.smartcontrolbrasil.com.br` | `129.121.55.23` | Resolve |
| `staging-livia.smartcontrolbrasil.com.br` | *(vazio)* | **NXDOMAIN / não provisionado** |

**Conclusão:** não existe host de staging dedicado no DNS público verificado nesta sessão. Não declarar staging operacional.

---

## 3. TLS (produção — leitura remota)

```bash
echo | openssl s_client -connect livia.smartcontrolbrasil.com.br:443 \
  -servername livia.smartcontrolbrasil.com.br 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

| Campo | Valor |
|---|---|
| subject | `CN = livia.smartcontrolbrasil.com.br` |
| issuer | Let's Encrypt (`CN = YE1`) |
| notBefore | 2026-07-07 |
| notAfter | **2026-10-05** |
| chain | Válida para o hostname de produção |

Certificado de staging dedicado: **não aplicável** (host inexistente).

---

## 4. HTTP / reverse proxy (produção — leitura remota)

Header comum nas respostas:

```text
server: LiteSpeed
strict-transport-security: max-age=300
```

Indica **LiteSpeed / OpenLiteSpeed** na borda. Detalhes de vhost, listener e upstream **não inspecionados** (sem SSH).

### 4.1 Liveness

```bash
curl -sS -D - https://livia.smartcontrolbrasil.com.br/health/
```

```text
HTTP/2 200
content-type: application/json
```

Body:

```json
{"status": "ok", "service": "livia-platform"}
```

### 4.2 Readiness (endpoint Fase 18)

```bash
curl -sS -D - 'https://livia.smartcontrolbrasil.com.br/health/?readiness=1'
```

Resposta **idêntica** ao liveness simples (mesmo body de 45 bytes).
**Interpretação:** código em produção **não expõe** (ou não deployou) o payload de readiness da Fase 18 nesta URL. Isso é observação read-only; **não** implica alteração nesta fase.

### 4.3 Assets públicos

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://livia.smartcontrolbrasil.com.br/widget.js
curl -sS -o /dev/null -w '%{http_code}\n' https://livia.smartcontrolbrasil.com.br/demo/
```

| URL | HTTP |
|---|---|
| `/widget.js` | 200 |
| `/demo/` | 200 |

### 4.4 CORS GP (produção — read-only)

```bash
curl -sS -D - -X OPTIONS 'https://livia.smartcontrolbrasil.com.br/api/chat/' \
  -H 'Origin: https://www.granimarmorespitondo.com.br' \
  -H 'Access-Control-Request-Method: POST'
```

```text
HTTP/2 204
access-control-allow-origin: https://www.granimarmorespitondo.com.br
access-control-allow-methods: GET, POST, OPTIONS
```

**Nota:** origin GP já responde em **produção**. Staging físico separado **não** foi validado; esta evidência **não** substitui gate de staging isolado.

### 4.5 Install package GP (produção — read-only)

```bash
curl -sS 'https://livia.smartcontrolbrasil.com.br/install/granimarmores-pitondo.json'
```

Campos relevantes (trecho):

```json
{
  "tenant": "granimarmores-pitondo",
  "is_active": true,
  "allowed_origin": "https://www.granimarmorespitondo.com.br",
  "api_url": "https://livia.smartcontrolbrasil.com.br/api/chat/",
  "widget_src": "https://livia.smartcontrolbrasil.com.br/widget.js"
}
```

---

## 5. Tentativa de acesso SSH (bloqueio da auditoria in loco)

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 marcelo@129.121.55.23 'hostname'
```

Resultado:

```text
ssh: connect to host 129.121.55.23 port 22: Connection refused
```

**Não foi possível** inspecionar na VPS:

- diretório de produção / staging
- branch/commit deployado
- Python/venv
- units systemd (`livia.service`, etc.)
- processo Gunicorn / porta backend
- PostgreSQL local (versão, databases, users)
- firewall / logs
- vhosts OpenLiteSpeed

---

## 6. Itens não verificados (requerem shell na VPS)

| Item | Status |
|---|---|
| Serviço de produção Lívia (nome real) | Desconhecido |
| WorkingDirectory produção | Desconhecido |
| Commit deployado em produção | Desconhecido |
| Virtualenv produção | Desconhecido |
| Porta Gunicorn produção | Desconhecido |
| Database produção (nome/host) | Desconhecido |
| Existência de `/home/.../livia_staging/` | Desconhecido |
| Outros serviços na VPS (impacto) | Desconhecido |

---

## 7. Serviços que não podem ser impactados

Sem shell na VPS, **não foi possível** enumerar outros serviços.
Regra operacional mantida: **nenhuma alteração em produção** nesta fase.

---

## 8. Conclusão da auditoria

```text
AUDITORIA VPS INCOMPLETA — SSH INDISPONÍVEL (porta 22 recusada)
STAGING FÍSICO — NÃO EXISTE NO DNS PÚBLICO VERIFICADO
PRODUÇÃO — RESPONDENDO (LiteSpeed, TLS válido, GP configurado em produção)
PRODUÇÃO — NÃO ALTERADA NESTA SESSÃO
```

Próximo passo bloqueante para Fase 19: acesso SSH (ou console) à VPS `129.121.55.23` **sem reiniciar produção**, seguido de provisionamento isolado conforme `docs/phase19_staging_provisioning.md`.
