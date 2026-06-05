---
name: google-oauth
description: "Autoriza acesso ao Gmail via OAuth2 — gera a URL de login para o usuário clicar e salva o token de acesso."
category: integrations
---

# Autorização Google OAuth2 (Gmail)

Esta skill ensina o agente a gerar a URL de autorização OAuth2 do Google para que o André possa clicar e autorizar o acesso ao Gmail — sem precisar rodar nada em terminal.

---

## Quando usar esta skill

Use quando o usuário disser algo como:
- "autoriza o gmail"
- "quero autorizar o email"
- "google oauth"
- "gerar link de autorização do google"
- "o suporte de email não está funcionando"
- "google_token.json não existe"

---

## O que o agente deve fazer

### Passo 1 — Verificar se o token já existe

```bash
ls -la /opt/data/.hermes/google_token.json 2>/dev/null && echo "TOKEN EXISTE" || echo "TOKEN AUSENTE"
```

Se existir e for válido, informar o usuário e testar a conexão (Passo 4).
Se não existir, continuar com o Passo 2.

---

### Passo 2 — Verificar credenciais

```bash
python3 -c "
import os
client_id = os.getenv('GOOGLE_CLIENT_ID', '')
client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '')
if client_id and client_secret:
    print(f'✅ GOOGLE_CLIENT_ID: {client_id[:20]}...')
    print(f'✅ GOOGLE_CLIENT_SECRET: {client_secret[:6]}...')
else:
    print('❌ Credenciais não encontradas no ambiente.')
    print('   Configure GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no Portainer.')
"
```

Se as credenciais não existirem → instruir o usuário a configurá-las no Portainer antes de continuar.

---

### Passo 3 — Gerar a URL de autorização

Execute o script abaixo **no container** para gerar a URL de autorização:

```bash
PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 - <<'EOF'
import os, json
from pathlib import Path

# Carregar .env se necessário
try:
    from dotenv import load_dotenv
    load_dotenv("/opt/data/.env")
except ImportError:
    pass

client_id = os.getenv("GOOGLE_CLIENT_ID", "")
client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

if not client_id or not client_secret:
    print("❌ GOOGLE_CLIENT_ID ou GOOGLE_CLIENT_SECRET não configurados.")
    exit(1)

try:
    from google_auth_oauthlib.flow import Flow
except ImportError:
    print("❌ Biblioteca google-auth-oauthlib não encontrada.")
    print("   Execute: uv pip install --python /opt/hermes/.venv/bin/python google-auth-oauthlib google-api-python-client")
    exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

client_config = {
    "installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri="urn:ietf:wg:oauth:2.0:oob")
auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

print("\n" + "="*60)
print("👉 CLIQUE NESTE LINK PARA AUTORIZAR O GMAIL:")
print("="*60)
print(auth_url)
print("="*60)
print("\nApós autorizar, você receberá um código de 4-6 palavras.")
print("Envie o código de volta para o agente.")
EOF
```

**O agente deve copiar a URL gerada e apresentar ao usuário como link clicável.**

---

### Passo 4 — Receber o código e salvar o token

Após o usuário colar o código de autorização, execute:

```bash
PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 - <<'EOF'
import os, json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv("/opt/data/.env")
except ImportError:
    pass

# O agente deve substituir CODIGO_AQUI pelo código recebido do usuário
AUTH_CODE = "CODIGO_AQUI"

client_id = os.getenv("GOOGLE_CLIENT_ID", "")
client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

client_config = {
    "installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri="urn:ietf:wg:oauth:2.0:oob")
flow.fetch_token(code=AUTH_CODE)
creds = flow.credentials

TOKEN_PATH = Path("/opt/data/.hermes/google_token.json")
TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(TOKEN_PATH, "w") as f:
    json.dump({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }, f, indent=2)

print(f"✅ Token salvo em {TOKEN_PATH}")
print("   O support_agent.py agora tem acesso ao Gmail.")
EOF
```

> **Importante:** O agente deve substituir `CODIGO_AQUI` pelo código que o usuário enviou antes de executar.

---

### Passo 5 — Testar o acesso ao Gmail

```bash
PYTHONPATH=/opt/hermes/.venv/lib/python3.13/site-packages python3 - <<'EOF'
import sys
sys.path.insert(0, "/opt/data/.hermes/skills/productivity/google-workspace/scripts")

try:
    from dotenv import load_dotenv
    load_dotenv("/opt/data/.env")
except ImportError:
    pass

from google_api import build_service
service = build_service("gmail", "v1")
profile = service.users().getProfile(userId="me").execute()
print(f"✅ Gmail conectado com sucesso!")
print(f"   Conta: {profile.get('emailAddress')}")
print(f"   Total de mensagens: {profile.get('messagesTotal')}")
EOF
```

Se retornar a conta de e-mail, a autorização está completa e o agente de suporte de e-mail está pronto.

---

## Notas Importantes

- O token é salvo em `/opt/data/.hermes/google_token.json` (volume persistente — sobrevive a restarts)
- O `refresh_token` permite renovação automática sem precisar autorizar novamente
- Se a autorização expirar (raro com refresh_token), basta repetir o Passo 3
- As credenciais `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` devem ser do tipo **"Aplicativo de computador"** (não "Web") no Google Cloud Console

## Configuração no Google Cloud Console

Se as credenciais ainda não existem:
1. Acesse https://console.cloud.google.com/apis/credentials
2. Crie um **OAuth 2.0 Client ID** do tipo **"Aplicativo para computador"** (Desktop app)
3. Baixe o JSON e extraia `client_id` e `client_secret`
4. Configure no Portainer Stack como `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`
