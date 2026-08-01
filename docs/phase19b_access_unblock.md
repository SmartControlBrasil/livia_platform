# Fase 19B — Desbloqueio de acesso à VPS

**Data:** 2026-07-31  
**IP:** `129.121.55.23` (`livia.smartcontrolbrasil.com.br`)

---

## 1. Fontes consultadas (sem brute force)

| Fonte | Achado |
|---|---|
| `docs/handoff.md` | Produção em `/var/www/livia-platform` |
| `docs/deploy/sqlite_to_postgresql.md` | Serviço systemd `livia-platform.service` |
| `docs/phase19_vps_audit.md` | Porta **22 recusada** na Fase 19 |
| Probes limitados (portas comuns documentadas em CyberPanel) | **22022 aberta**, 8090/7080 abertas |

Não foi executado scan amplo de portas nem tentativa de múltiplos usuários.

---

## 2. Resultados de conectividade (executados nesta sessão)

### 2.1 SSH

```bash
# Porta 22
nc -zv 129.121.55.23 22
# → Connection refused

# Porta 22022 (alternativa comum em hosts CyberPanel)
ssh -o BatchMode=yes -o ConnectTimeout=8 -p 22022 marcelo@129.121.55.23 'hostname'
```

```text
Porta 22: recusada (sshd não escuta ou firewall)
Porta 22022: sshd ATIVO — Permission denied (publickey,...)
```

**Interpretação:** o serviço SSH existe na **22022**, mas a chave local (`~/.ssh/id_ed25519.pub`, fingerprint associado a `smartcontrolbrasiloficial@gmail.com`) **não está autorizada** para o usuário `marcelo` neste host.

### 2.2 Painéis (somente conectividade TCP)

| Porta | Serviço típico | Status |
|---|---|---|
| 8090 | CyberPanel HTTPS | aberta |
| 7080 | OpenLiteSpeed Admin | aberta |
| 443 | HTTPS público | aberta |

Acesso web ao painel **não foi tentado** nesta sessão (requer credenciais do operador).

### 2.3 DNS staging

```bash
dig +short staging-livia.smartcontrolbrasil.com.br A
dig +short livia-staging.smartcontrolbrasil.com.br A
```

```text
(ambos vazios — sem registro A)
```

---

## 3. TLS produção (revalidado)

```bash
echo | openssl s_client -connect livia.smartcontrolbrasil.com.br:443 \
  -servername livia.smartcontrolbrasil.com.br 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

| Campo | Valor |
|---|---|
| subject | `CN = livia.smartcontrolbrasil.com.br` |
| issuer | Let's Encrypt (`CN = YE1`) |
| notBefore | **2026-07-07** |
| notAfter | **2026-10-05** |

Correção em relação a anotação anterior com ano errado: expiração em **out/2026**, não 2025.

---

## 4. Procedimento de desbloqueio (CyberPanel / console)

> Executar pelo operador com credenciais do provedor. **Não alterar produção** além do necessário para autorizar acesso administrativo.

### 4.1 Opção A — CyberPanel (`https://129.121.55.23:8090`)

1. Login no CyberPanel.
2. **SSH Manager** ou **Manage SSH** → confirmar porta (**provavelmente 22022**).
3. Adicionar chave pública do operador (conteúdo de `~/.ssh/id_ed25519.pub` no notebook de deploy).
4. Confirmar usuário Linux correto (verificar em **List Websites** qual user possui `livia.smartcontrolbrasil.com.br` — frequentemente `smartc1234` ou similar, **não assumir**).

Teste após autorização:

```bash
ssh -p 22022 <usuario>@129.121.55.23 'hostname; whoami'
```

### 4.2 Opção B — Console KVM / web terminal do provedor

Comandos **somente leitura** iniciais:

```bash
ss -lntp
systemctl status sshd --no-pager -l
grep -E '^(Port|ListenAddress|PermitRootLogin)' /etc/ssh/sshd_config
systemctl list-units --type=service | grep -iE 'livia|litespeed|postgres'
find /var/www /home -maxdepth 3 -type d -iname '*livia*' 2>/dev/null
```

**Não** editar `sshd_config` antes de entender a configuração atual.

### 4.3 Auditoria produção (read-only, após login)

```bash
systemctl cat livia-platform.service
cd /var/www/livia-platform && git rev-parse HEAD && git branch --show-current
ss -lntp | grep -i gunicorn
curl -sS http://127.0.0.1:<porta-backend>/health/
curl -sS 'http://127.0.0.1:<porta-backend>/health/?readiness=1'
```

Registrar GP em produção **sem usar como staging** (observação Fase 19: CORS GP já ativo em produção via probe HTTP externo).

---

## 5. Host de staging recomendado

Após acesso confirmado, usar **um único** hostname:

```text
staging-livia.smartcontrolbrasil.com.br  →  A  129.121.55.23
```

Alternativa coerente se preferir padrão invertido: `livia-staging.smartcontrolbrasil.com.br` — **não criar os dois**.

---

## 6. Status do desbloqueio

```text
SSH ADMINISTRATIVO — PARCIALMENTE DESBLOQUEADO (porta 22022 identificada)
AUTENTICAÇÃO — BLOQUEADA (chave local não autorizada)
CONSOLE CYBERPANEL — DISPONÍVEL (porta 8090 aberta; login não tentado)
PROVISIONAMENTO STAGING — NÃO INICIADO (sem shell autenticado)
```

Próximo passo humano: autorizar chave SSH ou abrir terminal no CyberPanel e seguir `docs/phase19_staging_provisioning.md`.
