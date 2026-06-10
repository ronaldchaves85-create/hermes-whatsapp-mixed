#!/bin/bash
set -e

# Parâmetros:
# $1: Usuário do GitHub (ex: empreendedorserial)
# $2: Token do GitHub para o código/fork (opcional, se privado)
# $3: Repositório de configurações privado (opcional, formato: usuario/repo-nome)
# $4: Token do GitHub para o repositório de configurações (opcional, se privado. Fallback para $GITHUB_TOKEN)
GITHUB_USER="${1:-${HERMES_SETUP_GITHUB_USER:-empreendedorserial}}"
GITHUB_TOKEN="${2:-$GITHUB_TOKEN}"
CONFIG_REPO="${3:-$CONFIG_REPO}"
CONFIG_GITHUB_TOKEN="${4:-${CONFIG_GITHUB_TOKEN:-$GITHUB_TOKEN}}"

echo "=========================================================="
echo "🤖 CONFIGURADOR DE MODO MISTO DO EMPREENDEDOR SERIAL 🤖"
echo "           GitHub Fork de: $GITHUB_USER"
if [ -n "$CONFIG_REPO" ]; then
echo "           Repo de Configs Privado: $CONFIG_REPO"
fi
echo "=========================================================="

# Define o caminho base do Hermes dentro do container
BASE_DIR="/opt/data/.hermes"
mkdir -p "$BASE_DIR"
mkdir -p "/opt/data"

# Configura cabeçalhos de autenticação
CURL_CODE_AUTH_HEADER=""
if [ -n "$GITHUB_TOKEN" ]; then
    CURL_CODE_AUTH_HEADER="Authorization: token $GITHUB_TOKEN"
fi

CURL_CONFIG_AUTH_HEADER=""
if [ -n "$CONFIG_GITHUB_TOKEN" ]; then
    CURL_CONFIG_AUTH_HEADER="Authorization: token $CONFIG_GITHUB_TOKEN"
fi

# URL Base para os arquivos de código (bridge, plugins, etc.) do GitHub
RAW_ROOT="https://raw.githubusercontent.com/$GITHUB_USER/hermes-whatsapp-mixed/main"
RAW_URL="$RAW_ROOT/deploy"

# URL para os arquivos de configuração (SOULs, regras, contatos)
if [ -n "$CONFIG_REPO" ]; then
    CONFIG_URL="https://raw.githubusercontent.com/$CONFIG_REPO/main"
else
    CONFIG_URL="$RAW_URL"
fi

# Função auxiliar para fazer download via curl tratando autenticação e erros
download_file() {
    local url="$1"
    local output="$2"
    local auth_header="$3"
    if [ -n "$auth_header" ]; then
        curl -f -H "$auth_header" -sSL "$url" -o "$output"
    else
        curl -f -sSL "$url" -o "$output"
    fi
}

echo "⏳ 1. Baixando arquivos de configuração e personas..."

# Baixa e atualiza o arquivo de persona (SOUL.md)
if download_file "$CONFIG_URL/SOUL.md" "/opt/data/SOUL.md" "$CURL_CONFIG_AUTH_HEADER"; then
    cp "/opt/data/SOUL.md" "$BASE_DIR/SOUL.md"
    echo "  ✓ Persona SOUL.md sincronizada"
else
    echo "  ⚠️ Falha ao baixar SOUL.md do repositório"
fi

# Baixa e atualiza o arquivo de persona do suporte do WhatsApp
if download_file "$CONFIG_URL/SOUL_WHATSAPP.md" "/opt/data/SOUL_WHATSAPP.md" "$CURL_CONFIG_AUTH_HEADER"; then
    echo "  ✓ Persona do WhatsApp SOUL_WHATSAPP.md sincronizada"
else
    echo "  ⚠️ Falha ao baixar SOUL_WHATSAPP.md"
fi

# Baixa e atualiza o arquivo de persona do suporte de E-mail
if download_file "$CONFIG_URL/SOUL_EMAIL.md" "/opt/data/SOUL_EMAIL.md" "$CURL_CONFIG_AUTH_HEADER"; then
    echo "  ✓ Persona de E-mail SOUL_EMAIL.md sincronizada"
else
    echo "  ⚠️ Falha ao baixar SOUL_EMAIL.md"
fi

# Baixa e atualiza a base de conhecimento de suporte (support_rules.md)
if download_file "$CONFIG_URL/support_rules.md" "/opt/data/support_rules.md" "$CURL_CONFIG_AUTH_HEADER"; then
    echo "  ✓ Regras de suporte support_rules.md sincronizadas"
else
    echo "  ⚠️ Falha ao baixar support_rules.md"
fi

# Baixa personal_contacts.json se estiver usando repositório privado
if [ -n "$CONFIG_REPO" ]; then
    if download_file "$CONFIG_URL/personal_contacts.json" "/opt/data/personal_contacts.json" "$CURL_CONFIG_AUTH_HEADER"; then
        echo "  ✓ Contatos pessoais personal_contacts.json sincronizados com sucesso"
    else
        echo "  - personal_contacts.json não encontrado no repositório, mantendo o arquivo local se existir"
    fi
fi

# Instala o plugin whatsapp-manager automaticamente caso não esteja presente no volume
if [ ! -d "/opt/data/.hermes/plugins/whatsapp-manager" ]; then
    echo "⏳ Instalando o plugin whatsapp-manager..."
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager"
    download_file "$RAW_ROOT/plugin.yaml"     "/opt/data/.hermes/plugins/whatsapp-manager/plugin.yaml" "$CURL_CODE_AUTH_HEADER"
    download_file "$RAW_ROOT/__init__.py"     "/opt/data/.hermes/plugins/whatsapp-manager/__init__.py" "$CURL_CODE_AUTH_HEADER"
    download_file "$RAW_ROOT/bridge.js"       "/opt/data/.hermes/plugins/whatsapp-manager/bridge.js" "$CURL_CODE_AUTH_HEADER"
    download_file "$RAW_ROOT/package.json"    "/opt/data/.hermes/plugins/whatsapp-manager/package.json" "$CURL_CODE_AUTH_HEADER"
    download_file "$RAW_ROOT/google_api.py"   "/opt/data/.hermes/plugins/whatsapp-manager/google_api.py" "$CURL_CODE_AUTH_HEADER"
    
    # Instalar skills bundled do plugin
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/google-oauth"
    download_file "$RAW_ROOT/skills/google-oauth/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/google-oauth/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/research-sources"
    download_file "$RAW_ROOT/skills/research-sources/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/research-sources/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/whatsapp-logs-diagnostics"
    download_file "$RAW_ROOT/skills/whatsapp-logs-diagnostics/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/whatsapp-logs-diagnostics/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    echo "  ✓ Plugin whatsapp-manager instalado com sucesso (incluindo skills e google_api)."
else
    echo "  - Plugin whatsapp-manager já instalado. Atualizando __init__.py, skills e módulos..."
    download_file "$RAW_ROOT/__init__.py"   "/opt/data/.hermes/plugins/whatsapp-manager/__init__.py" "$CURL_CODE_AUTH_HEADER"
    download_file "$RAW_ROOT/google_api.py" "/opt/data/.hermes/plugins/whatsapp-manager/google_api.py" "$CURL_CODE_AUTH_HEADER"
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/google-oauth"
    download_file "$RAW_ROOT/skills/google-oauth/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/google-oauth/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/research-sources"
    download_file "$RAW_ROOT/skills/research-sources/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/research-sources/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager/skills/whatsapp-logs-diagnostics"
    download_file "$RAW_ROOT/skills/whatsapp-logs-diagnostics/SKILL.md" "/opt/data/.hermes/plugins/whatsapp-manager/skills/whatsapp-logs-diagnostics/SKILL.md" "$CURL_CODE_AUTH_HEADER"
    echo "  ✓ __init__.py, google_api.py e skills atualizados."
fi

# Baixa o modelo de config.yaml se ele não existir localmente
if [ ! -f "$BASE_DIR/config.yaml" ]; then
    download_file "$RAW_URL/config.yaml.example" "$BASE_DIR/config.yaml" "$CURL_CODE_AUTH_HEADER"
    echo "  ✓ config.yaml inicial configurado."
else
    echo "  - config.yaml já existe localmente, pulando."
fi

# Baixa o modelo de chaves de API (.env) se ele não existir localmente
if [ ! -f "$BASE_DIR/.env" ]; then
    download_file "$RAW_URL/env.example" "$BASE_DIR/.env" "$CURL_CODE_AUTH_HEADER" || download_file "$RAW_URL/.env.example" "$BASE_DIR/.env" "$CURL_CODE_AUTH_HEADER"
    echo "  ✓ Arquivo de chaves .env inicial criado."
else
    echo "  - Arquivo .env já existe localmente, pulando."
fi


echo "⏳ 2. Baixando e aplicando o Patch do WhatsApp..."
# Sincroniza o arquivo bridge.js modificado diretamente do repositório
mkdir -p "/opt/data/.hermes/platforms/whatsapp/bridge"
download_file "$RAW_ROOT/docs/bridge-artifacts/bridge.js" "/opt/data/.hermes/platforms/whatsapp/bridge/bridge.js" "$CURL_CODE_AUTH_HEADER"
download_file "$RAW_ROOT/docs/bridge-artifacts/package.json" "/opt/data/.hermes/platforms/whatsapp/bridge/package.json" "$CURL_CODE_AUTH_HEADER"
echo "  ✓ Arquivos bridge.js e package.json sincronizados."

# Corrige o desalinhamento de caminhos da sessão do WhatsApp (symlink antiga -> nova)
mkdir -p "/opt/data/.hermes/platforms/whatsapp/session"
if [ -d "/opt/data/.hermes/whatsapp/session" ] && [ ! -L "/opt/data/.hermes/whatsapp/session" ]; then
    echo "  🔄 Movendo arquivos de sessão do caminho antigo para o novo..."
    mv /opt/data/.hermes/whatsapp/session/* /opt/data/.hermes/platforms/whatsapp/session/ 2>/dev/null || true
    rm -rf "/opt/data/.hermes/whatsapp/session"
fi
if [ ! -e "/opt/data/.hermes/whatsapp/session" ]; then
    ln -sfn "/opt/data/.hermes/platforms/whatsapp/session" "/opt/data/.hermes/whatsapp/session"
    echo "  ✓ Link de compatibilidade da sessão configurado automaticamente."
fi

# Baixa os scripts do agente de suporte de e-mail direto do repositório
mkdir -p "/opt/data/.hermes/scripts"
download_file "$RAW_URL/scripts/support_agent.py" "/opt/data/.hermes/scripts/support_agent.py" "$CURL_CODE_AUTH_HEADER"
chmod +x "/opt/data/.hermes/scripts/support_agent.py"
echo "  ✓ support_agent.py sincronizado."

# Baixa o módulo google_api.py (autenticação OAuth2 Gmail)
mkdir -p "/opt/data/.hermes/skills/productivity/google-workspace/scripts"
download_file "$RAW_URL/scripts/google_api.py" "/opt/data/.hermes/skills/productivity/google-workspace/scripts/google_api.py" "$CURL_CODE_AUTH_HEADER"
echo "  ✓ google_api.py sincronizado."

# Baixa o script de autorização OAuth2 (necessário na primeira vez)
download_file "$RAW_URL/scripts/authorize_google.py" "/opt/data/.hermes/scripts/authorize_google.py" "$CURL_CODE_AUTH_HEADER"
chmod +x "/opt/data/.hermes/scripts/authorize_google.py"
echo "  ✓ authorize_google.py sincronizado."

# Baixa e executa o patch_whatsapp.py para verificar a integridade
download_file "$RAW_URL/patch_whatsapp.py" "/tmp/patch_whatsapp.py" "$CURL_CODE_AUTH_HEADER"
python3 /tmp/patch_whatsapp.py


echo "⏳ 3. Instalando dependências da ponte e de geração de imagem do QR Code..."

# Instala dependências Node.js da ponte do WhatsApp (necessário para iniciar o pareamento)
if [ -d "/opt/hermes/scripts/whatsapp-bridge" ]; then
    (cd /opt/hermes/scripts/whatsapp-bridge && npm install --no-fund --no-audit --silent 2>/dev/null) \
        && echo "  ✓ Dependências Node.js da ponte do WhatsApp instaladas." \
        || echo "  ⚠️  npm install falhou (pode ser seguro ignorar se já instalado)."
else
    echo "  - Pasta whatsapp-bridge não encontrada, pulando npm install."
fi

# Instala qrcode e pillow no venv do Hermes
if [ -x "/opt/hermes/.venv/bin/python" ]; then
    uv pip install --python /opt/hermes/.venv/bin/python qrcode pillow --quiet 2>/dev/null \
        && echo "  ✓ Bibliotecas qrcode e pillow instaladas no ambiente virtual." \
        || echo "  ⚠️  uv pip install falhou (pode ser seguro ignorar se já instalado)."

    # Instala dependências do Gmail API
    uv pip install --python /opt/hermes/.venv/bin/python \
        google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client \
        --quiet 2>/dev/null \
        && echo "  ✓ Bibliotecas Google API instaladas no ambiente virtual." \
        || echo "  ⚠️  Instalação das libs Google falhou (verificar conectividade)."
else
    echo "  - Ambiente virtual do Hermes não encontrado em /opt/hermes/.venv, pulando."
fi

# Se o profile de WhatsApp do Hermes já existir, sincroniza também o SOUL_WHATSAPP
if [ -d "/opt/data/.hermes/profiles/whatsapp" ]; then
    cp "/opt/data/SOUL_WHATSAPP.md" "/opt/data/.hermes/profiles/whatsapp/SOUL.md"
    echo "  ✓ Profile de WhatsApp atualizado com a persona SOUL_WHATSAPP.md"
fi

# Se o profile de E-mail do Hermes já existir, sincroniza também o SOUL_EMAIL
if [ -d "/opt/data/.hermes/profiles/email" ]; then
    cp "/opt/data/SOUL_EMAIL.md" "/opt/data/.hermes/profiles/email/SOUL.md"
    echo "  ✓ Profile de E-mail atualizado com a persona SOUL_EMAIL.md"
fi

echo "=========================================================="
echo "🎉 SINCRONIZAÇÃO E CONFIGURAÇÃO CONCLUÍDAS COM SUCESSO!"
echo "=========================================================="
echo "Seu Hermes foi sincronizado com o seu GitHub Fork ($GITHUB_USER)!"
if [ -n "$CONFIG_REPO" ]; then
echo "Configurações e personas sincronizadas com $CONFIG_REPO!"
fi
echo "=========================================================="
