---
name: hermes-env-vars
description: Variáveis de ambiente do Hermes Agent — WhatsApp, E-mail, Google OAuth
category: devops
---

# Hermes Agent — Variáveis de Ambiente

**Resumo:** Este é um reference rápido. Para documentação completa da arquitetura, consulte `hermes-architecture`.

## Variáveis por Plataforma

| Plataforma | Variáveis | Método | Status |
|------------|-----------|--------|--------|
| WhatsApp | `WHATSAPP_*` | Baileys | ✅ Configurado |
| E-mail | `GOOGLE_CLIENT_*` | Gmail API (OAuth2) via support_agent.py | ✅ Configurado |
| Gemini STT/Vision | `GOOGLE_CLIENT_*` | Google OAuth API | ⚠️ Billing pode estar bloqueado |

## Como verificar

```bash
# Todas plataformas
python3 -c "import os; print(sorted([k for k in os.environ.keys() if any(x in k for x in ['WHATSAPP','EMAIL','GOOGLE','IMAP','SMTP'])]))"

# WhatsApp
ps aux | grep bridge | grep -v grep

# Email
tail -5 /opt/data/support_agent.log 2>/dev/null
```

## E-mail (Gmail API — NÃO IMAP/SMTP)

O sistema de email usa **Google Gmail API** via `support_agent.py`, não IMAP/SMTP nem himalaya.

Variáveis: `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (mesmas do Gemini).

Verificar status:
```bash
cd /opt/data && PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 .hermes/scripts/support_agent.py
```

> **Nota:** Detalhes completos da arquitetura de email, fluxo do sistema, e resolução de problemas estão em `hermes-architecture`.
---

## Resumo

| Plataforma | Variáveis | Método | Status |
|------------|-----------|--------|--------|
| WhatsApp | `WHATSAPP_*` | Baileys | ✅ Configurado |
| E-mail | `GOOGLE_CLIENT_*` | Gmail API (OAuth2) via support_agent.py | ✅ Configurado |
| Gemini STT/Vision | `GOOGLE_CLIENT_*` | Google OAuth API | ⚠️ Configurado, billing bloqueado |

---

## Verificação rápida após restart

```bash
# WhatsApp
ps aux | grep bridge | grep -v grep

# Todas plataformas ativas
python3 -c "import os; print(sorted([k for k in os.environ.keys() if any(x in k for x in ['WHATSAPP','EMAIL','GOOGLE','IMAP','SMTP'])]))"
```
