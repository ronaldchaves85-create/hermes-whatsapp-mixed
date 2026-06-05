---
name: hermes-architecture
description: Arquitetura completa do Hermes Agent — credenciais, plataformas e fluxo de dados
category: devops
---

# Arquitetura do Hermes Agent

**📌 Fonte de verdade** — Este é o documento completo de referência. Outras skills resumem seções daqui.

**Skills relacionadas:**
- `hermes-env-vars` — resumo rápido de variáveis por plataforma
- `whatsapp-bot-env-vars` — operação WhatsApp (dual-mode, startup, DB)
- `messaging-gateway-customization` — customização avançada (plugins, patching, pitfalls)
- `himalaya` — CLI IMAP/SMTP (⚠️ NÃO é o sistema ativo de email — ver nota no topo)

Guia completo de referência sobre como o sistema funciona: onde estão as credenciais, como cada plataforma se conecta, e como a informação flui entre os componentes.

---

## Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                        PORTINGER                            │
│                  (Variáveis de Ambiente)                    │
└──────────┬──────────────┬──────────────────┬────────────────┘
           │              │                  │
    ┌──────▼──────┐ ┌────▼────────┐ ┌───────▼──────────┐
    │  WhatsApp   │ │  E-mail     │ │  LLM (MiniMax)    │
    │  (Baileys)  │ │ (Gmail API) │ │  (auth.json)      │
    └─────────────┘ └─────────────┘ └───────────────────┘
```

---

## Onde Encontrar Credenciais

### 1. WhatsApp

**Localização:** Variáveis de ambiente no Portainer (não em arquivos)

**Variáveis esperadas:**
```
WHATSAPP_ENABLED=true
WHATSAPP_OWNER_NUMBER=5586981612061
WHATSAPP_MODE=mixed
WHATSAPP_BRIDGE_PORT=18732
```

**Onde são usadas:**
- Bridge Node.js: `/opt/data/.hermes/platforms/whatsapp/bridge/bridge.js`
- Autenticação: QR Code via Baileys (sessão armazenada em `/opt/data/.hermes/platforms/whatsapp/session/`)
- Arquivo de log: `/opt/data/.hermes/platforms/whatsapp/bridge.log`

**Verificação:**
```bash
python3 -c "import os; [print(k,'=',v) for k,v in os.environ.items() if 'WHATSAPP' in k.upper()]"
ps aux | grep bridge | grep -v grep
```

---

### 2. E-mail (Gmail API / Google Workspace)

**ATENÇÃO:** O sistema de email NÃO usa IMAP/SMTP. Usa a **Google Gmail API** via OAuth2.

**Localização:** Variáveis de ambiente no Portainer

**Variáveis esperadas:**
```
GOOGLE_CLIENT_ID=206771399571-xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
```

**Onde são usadas:**
- Script: `/opt/data/.hermes/scripts/support_agent.py`
- Módulo: `/opt/data/.hermes/skills/productivity/google-workspace/scripts/google_api.py`

**Arquitetura do sistema de email:**

```
Gmail API (Google Workspace OAuth2)
         │
         ▼
  support_agent.py ◄── support_rules.md (regras de negócio)
         │                  └── SOUL_EMAIL.md (persona)
         │
         ▼
   MiniMax LLM ◄── auth.json (credenciais do MiniMax)
         │
         ▼
   Resposta por email (thread-safe)
```

**Verificação:**
```bash
python3 -c "import os; [print(k,'=',v[:30]+'...') for k,v in os.environ.items() if 'GOOGLE' in k.upper() or 'CLIENT' in k.upper()]"
```

**Como funciona:**
1. O cron job executa `support_agent.py` periodicamente (watchdog pattern)
2. O script conecta na Gmail API usando OAuth2 com `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`
3. Busca e-mails não lidos na INBOX (últimas 24h)
4. Para cada e-mail, verifica se é auto-resposta, se já foi respondido, etc.
5. Se precisar responder: carrega `support_rules.md` + `SOUL_EMAIL.md` → chama MiniMax via API → envia resposta
6. Se não houver e-mails para processar: saída silenciosa (sem notificação)

**Como rodar manualmente:**
```bash
cd /opt/data && PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 .hermes/scripts/support_agent.py
```
Nota: `python-dotenv` só existe no venv do Hermes (`/opt/hermes/.venv`), não no Python global do container.

---

### 3. LLM (MiniMax)

**Localização:** `auth.json` no gateway

**Arquivo:** `/opt/data/.hermes/auth.json`

**Estrutura:**
```json
{
  "credential_pool": {
    "minimax": [
      {
        "access_token": "eyJ...",
        "base_url": "https://api.minimax.io/anthropic",
        "scope": "full"
      }
    ]
  }
}
```

**Como é usado:**

**No gateway (Hermes Agent):**
- Arquivo: `/opt/hermes/gateway/auxiliary_client.py`
- Função: `resolve_provider_client("minimax")`
- Usa o token do `credential_pool` para fazer chamadas API

**No support_agent.py (email):**
- Lê `auth.json` diretamente
- Extrai `access_token` e `base_url` do `credential_pool.minimax[0]`
- Faz chamadas HTTP diretas para o endpoint `/v1/messages`

**Verificação:**
```bash
cat /opt/data/.hermes/auth.json | python3 -c "import json,sys; d=json.load(sys.stdin); cp=d.get('credential_pool',{}); [print(k,':', list(cp[k].keys()) if isinstance(cp[k],dict) else '***') for k in cp.keys()])"
```

---

## Arquivos de Configuração e Persona

### Support Rules (Diretrizes de Negócio)

**Arquivo:** `/opt/data/support_rules.md`

**O que contém:**
- FAQs e regras de negócio
- Scripts de venda e palavras-chave proibidas
- Regras de horário comercial e feriados
- Padrões de resposta para diferentes cenários

**Carregado por:** `support_agent.py`

---

### SOUL_EMAIL.md (Persona do Email)

**Arquivo:** `/opt/data/.hermes/profiles/email/SOUL.md`

**O que contém:**
- Persona do agente de email (equipe de suporte, 1ª pessoa plural "nós")
- Tom de voz e estilo de comunicação
- Exemplos de respostas corretas e incorretas

**Importante:** A persona deve declarar "equipe de suporte" (não "assistente de IA" nem "próprio André"). Gramática sempre em 1ª pessoa plural (nós), nunca 3ª pessoa (ele/ela).

---

### SOUL_whatsapp.md (Persona do WhatsApp)

**Arquivo:** `/opt/data/.hermes/profiles/whatsapp/SOUL.md`

**O que contém:**
- Persona do agente de WhatsApp (próprio André, 1ª pessoa singular "eu")
- Tom informal, brasileiro, como se fosse o André conversando
- Regras de ouro: máximo 2-3 frases, sem emojis em excesso

---

## Fluxo Completo do Sistema

### Fluxo do Email

```
1. Cron job executa support_agent.py (a cada X minutos)
2. support_agent.py conecta na Gmail API (OAuth2 via GOOGLE_CLIENT_*)
3. Busca emails não lidos: label:INBOX newer_than:1d
4. Para cada email:
   a. Verifica se é auto-resposta (loop protection)
   b. Verifica se já foi respondido por nós
   c. Verifica se humano já participou da thread
   d. Verifica se é email promocional/transacional
   e. Se tudo ok: chama MiniMax com system_prompt (support_rules.md + SOUL_EMAIL.md)
5. MiniMax retorna resposta em 1ª pessoa plural
6. support_agent.py envia resposta via Gmail API (thread-safe, In-Reply-To)
7. Marca email original como lido (remove UNREAD)
8. Se não houver emails processados: saída silenciosa (watchdog pattern)
```

### Fluxo do WhatsApp

```
1. Usuario envia mensagem no WhatsApp
2. Bridge Node.js (/opt/data/.hermes/platforms/whatsapp/bridge/bridge.js) recebe
3. Bridge faz polling no WhatsApp via Baileys
4. Mensagem enviada ao gateway Hermes
5. Gateway carrega SOUL_whatsapp.md (persona do André, 1ª pessoa singular)
6. MiniMax processa e retorna resposta
7. Gateway envia via bridge → WhatsApp
```

### Fluxo do LLM (MiniMax)

```
1. Necessidade de chamada LLM
2. Gateway busca credenciais em auth.json (credential_pool.minimax)
3. auxiliary_client.py monta request com access_token + base_url
4. Endpoint: https://api.minimax.io/anthropic/v1/messages
5. Modelo: MiniMax-M2.7
6. Resposta retorna para o gateway
```

---

## Variáveis de Ambiente (Resumo)

| Variável | Plataforma | Onde usar | Formato |
|----------|-------------|-----------|---------|
| `WHATSAPP_ENABLED` | WhatsApp | Bridge Baileys | `true` |
| `WHATSAPP_OWNER_NUMBER` | WhatsApp | Bridge Baileys | `5586981612061` |
| `WHATSAPP_MODE` | WhatsApp | Bridge Baileys | `mixed` |
| `GOOGLE_CLIENT_ID` | Email + Gemini STT | Gmail API + Gemini | ID do OAuth2 web client |
| `GOOGLE_CLIENT_SECRET` | Email + Gemini STT | Gmail API + Gemini | Secret do OAuth2 |
| `MINIMAX_API_KEY` | LLM (via auth.json) | auth.json, não env var | Token Bearer |

---

## Verificação Rápida do Sistema

```bash
# Todas as variáveis do sistema
python3 -c "import os; print(sorted([k for k in os.environ.keys() if any(x in k for x in ['WHATSAPP','EMAIL','IMAP','SMTP','GOOGLE','CLIENT','MINIMAX'])]))"

# WhatsApp
ps aux | grep bridge | grep -v grep

# Email (ver se support_agent.py está rodando)
cat /opt/data/support_agent.log 2>/dev/null | tail -20

# LLM (ver se auth.json existe)
cat /opt/data/.hermes/auth.json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(k) for k in d.get('credential_pool',{}).keys()])"
```

---

## Onde os Dados São Armazenados

| Tipo | Localização |
|------|-------------|
| Sessão WhatsApp | `/opt/data/.hermes/platforms/whatsapp/session/` |
| Bridge WhatsApp | `/opt/data/.hermes/platforms/whatsapp/bridge/` |
| Logs WhatsApp | `/opt/data/.hermes/platforms/whatsapp/bridge.log` |
| Auth do Gateway | `/opt/data/.hermes/auth.json` |
| Config do Gateway | `/opt/data/.hermes/config.yaml` |
| Regras de Suporte | `/opt/data/support_rules.md` |
| Persona Email | `/opt/data/.hermes/profiles/email/SOUL.md` |
| Persona WhatsApp | `/opt/data/.hermes/profiles/whatsapp/SOUL.md` |
| Scripts | `/opt/data/.hermes/scripts/support_agent.py` |
| Google API | `/opt/data/.hermes/skills/productivity/google-workspace/scripts/google_api.py` |
| Logs Email | `/opt/data/support_agent.log` |
| Memória persistente | `/opt/data/.hermes/memory/MEMORY.md` |

---

## Problemas Comuns e Diagnóstico

### Email respondendo em 3ª pessoa (ele/ela)
- **Causa:** TWO bugs combinados:
  1. `support_agent.py` linha ~113: o prefixo do system prompt dizia `"Você é um assistente de suporte"` (genérico), não `"Você é a equipe de suporte do André Alencar respondendo por e-mail"`
  2. `support_agent.py` parsing: MiniMax devolve blocks na ordem `thinking` PRIMEIRO, `text` DEPOIS. O código antigo pegava `block[0]` (sempre `thinking` = raciocínio interno), não a resposta real.
- **Solução:** Corrigir AMBOS:
  - System prompt: `"Você é a equipe de suporte do André Alencar respondendo por e-mail. Ignore..."`
  - Parsing: iterar pelos blocks e priorizar `text` sobre `thinking`
- **Código correto do parsing:**
  ```python
  for block in content:
      if block.get("type") == "text":
          return block.get("text", "")
  for block in content:
      if block.get("type") == "thinking":
          return block.get("thinking", "")  # fallback
  ```
- **Arquivos:** `/opt/data/.hermes/scripts/support_agent.py`

### Email sendo enviado 2 vezes
- **Causa:** O script foi executado múltiplas vezes em paralelo (cron overlapping) ou threads duplicadas no loop
- **Verificação:** `grep "Enviando resposta" /opt/data/support_agent.log` — se aparecer 2x para mesmo msg_id, é este bug
- **Arquivo:** `/opt/data/.hermes/scripts/support_agent.py`

### WhatsApp respondendo como bot
- **Causa:** SOUL_whatsapp.md mal configurado
- **Solução:** Declarar "você é o próprio André Alencar" em 1ª pessoa singular, informal
- **Arquivo:** `/opt/data/.hermes/profiles/whatsapp/SOUL.md`

### MiniMax sem visão (imagens)
- **Causa:** MiniMax é text-only, não suporta imagem input
- **Solução:** `_PROVIDERS_WITHOUT_VISION` deve incluir "minimax" (já corrigido)
- **Fallback:** OpenRouter (gemini-3-flash-preview) ou Gemini direto

### Gemini STT bloqueado (429 RESOURCE_EXHAUSTED)
- **Causa:** Billing da Google Cloud bloqueado
- **Solução:** Verificar faturamento da conta do Google Cloud

### Credenciais não aparecem no ambiente
- **Causa:** Reinício do container wipeou variáveis temporárias
- **Solução:** Verificar no Portainer se as variáveis estão configuradas no container

### GitHub push: token removido do remote URL
- **Causa:** Após `git remote set-url origin https://github.com/...` (sem token), o push falha com `Authentication failed`
- **Sintoma:** `remote: Invalid username or token` ao fazer git push
- **Solução:** Re-adicionar o token temporariamente:
  ```bash
  git remote set-url origin https://TOKEN@github.com/user/repo.git
  git push origin main
  # Imediatamente depois:
  git remote set-url origin https://github.com/user/repo.git
  ```
- **Segurança:** Após push, SEMPRE remover token do remote URL (já está no `.git-credentials` local)

### Python dotenv não encontrado ao rodar scripts manualmente
- **Sintoma:** `ModuleNotFoundError: No module named 'dotenv'`
- **Causa:** `python-dotenv` só existe no venv do Hermes (`/opt/hermes/.venv`), não no Python global do container
- **Solução:** Usar PYTHONPATH:
  ```bash
  PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 /opt/data/.hermes/scripts/support_agent.py
  ```
- **Arquivo:** `/opt/data/.hermes/scripts/support_agent.py`