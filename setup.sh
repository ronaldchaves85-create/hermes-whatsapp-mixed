#!/bin/bash
set -e

# Pega o usuário do GitHub via parâmetro. Se não for passado, usa 'empreendedorserial' como padrão.
GITHUB_USER="${1:-empreendedorserial}"

echo "=========================================================="
echo "🤖 CONFIGURADOR DE MODO MISTO DO EMPREENDEDOR SERIAL 🤖"
echo "           GitHub Fork de: $GITHUB_USER"
echo "=========================================================="

# Define o caminho base do Hermes dentro do container
BASE_DIR="/opt/data/.hermes"
mkdir -p "$BASE_DIR"
mkdir -p "/opt/data"

# URL Base para os arquivos do GitHub
RAW_URL="https://raw.githubusercontent.com/$GITHUB_USER/hermes-whatsapp-mixed/main"

echo "⏳ 1. Baixando arquivos de configuração e personas de $RAW_URL..."

# Baixa e atualiza o arquivo de persona (SOUL.md) direto do repositório/fork do aluno
curl -sSL "$RAW_URL/SOUL.md" -o "/opt/data/SOUL.md"
cp "/opt/data/SOUL.md" "$BASE_DIR/SOUL.md"
echo "  ✓ Persona SOUL.md sincronizada com seu GitHub"

# Baixa e atualiza o arquivo de persona do suporte do WhatsApp (SOUL_WHATSAPP.md) direto do repositório/fork do aluno
curl -sSL "$RAW_URL/SOUL_WHATSAPP.md" -o "/opt/data/SOUL_WHATSAPP.md"
echo "  ✓ Persona do WhatsApp SOUL_WHATSAPP.md sincronizada"

# Baixa e atualiza o arquivo de persona do suporte de E-mail (SOUL_EMAIL.md) direto do repositório/fork do aluno
curl -sSL "$RAW_URL/SOUL_EMAIL.md" -o "/opt/data/SOUL_EMAIL.md"
echo "  ✓ Persona de E-mail SOUL_EMAIL.md sincronizada"

# Baixa e atualiza a base de conhecimento de suporte (support_rules.md) direto do repositório/fork do aluno
curl -sSL "$RAW_URL/support_rules.md" -o "/opt/data/support_rules.md"
echo "  ✓ Regras de suporte support_rules.md sincronizadas com seu GitHub"

# Instala o plugin whatsapp-manager automaticamente caso não esteja presente no volume
if [ ! -d "/opt/data/.hermes/plugins/whatsapp-manager" ]; then
    echo "⏳ Instalando o plugin whatsapp-manager..."
    mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager"
    curl -sSL "$RAW_URL/plugins/whatsapp-manager/plugin.yaml" -o "/opt/data/.hermes/plugins/whatsapp-manager/plugin.yaml"
    curl -sSL "$RAW_URL/plugins/whatsapp-manager/__init__.py" -o "/opt/data/.hermes/plugins/whatsapp-manager/__init__.py"
    curl -sSL "$RAW_URL/plugins/whatsapp-manager/bridge.js" -o "/opt/data/.hermes/plugins/whatsapp-manager/bridge.js"
    curl -sSL "$RAW_URL/plugins/whatsapp-manager/package.json" -o "/opt/data/.hermes/plugins/whatsapp-manager/package.json"
    echo "  ✓ Plugin whatsapp-manager instalado com sucesso."
else
    echo "  - Plugin whatsapp-manager já instalado, pulando para evitar sobrescrever edições visuais."
fi

# Baixa o modelo de config.yaml se ele não existir localmente
if [ ! -f "$BASE_DIR/config.yaml" ]; then
    curl -sSL "$RAW_URL/config.yaml.example" -o "$BASE_DIR/config.yaml"
    echo "  ✓ config.yaml inicial configurado."
else
    echo "  - config.yaml já existe localmente, pulando."
fi

# Baixa o modelo de chaves de API (.env) se ele não existir localmente
if [ ! -f "$BASE_DIR/.env" ]; then
    curl -sSL "$RAW_URL/env.example" -o "$BASE_DIR/.env" || curl -sSL "$RAW_URL/.env.example" -o "$BASE_DIR/.env"
    echo "  ✓ Arquivo de chaves .env inicial criado."
else
    echo "  - Arquivo .env já existe localmente, pulando."
fi

echo "⏳ 2. Baixando e aplicando o Patch do WhatsApp..."
# Sincroniza o arquivo bridge.js modificado diretamente do repositório
mkdir -p "/opt/data/.hermes/platforms/whatsapp/bridge"
curl -sSL "$RAW_URL/docs/bridge-artifacts/bridge.js" -o "/opt/data/.hermes/platforms/whatsapp/bridge/bridge.js"
curl -sSL "$RAW_URL/docs/bridge-artifacts/package.json" -o "/opt/data/.hermes/platforms/whatsapp/bridge/package.json"
echo "  ✓ Arquivos bridge.js e package.json sincronizados."

# Baixa e executa o patch_whatsapp.py para verificar a integridade
curl -sSL "$RAW_URL/patch_whatsapp.py" -o "/tmp/patch_whatsapp.py"
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

# Instala qrcode e pillow no venv do Hermes (necessário para enviar QR Code como imagem PNG)
if [ -x "/opt/hermes/.venv/bin/python" ]; then
    uv pip install --python /opt/hermes/.venv/bin/python qrcode pillow --quiet 2>/dev/null \
        && echo "  ✓ Bibliotecas qrcode e pillow instaladas no ambiente virtual." \
        || echo "  ⚠️  uv pip install falhou (pode ser seguro ignorar se já instalado)."
else
    echo "  - Ambiente virtual do Hermes não encontrado em /opt/hermes/.venv, pulando."
fi

# Se o profile de WhatsApp do Hermes já existir, sincroniza também o SOUL_WHATSAPP para lá automaticamente
if [ -d "/opt/data/.hermes/profiles/whatsapp" ]; then
    cp "/opt/data/SOUL_WHATSAPP.md" "/opt/data/.hermes/profiles/whatsapp/SOUL.md"
    echo "  ✓ Profile de WhatsApp atualizado com a persona SOUL_WHATSAPP.md"
fi

# Se o profile de E-mail do Hermes já existir, sincroniza também o SOUL_EMAIL para lá automaticamente
if [ -d "/opt/data/.hermes/profiles/email" ]; then
    cp "/opt/data/SOUL_EMAIL.md" "/opt/data/.hermes/profiles/email/SOUL.md"
    echo "  ✓ Profile de E-mail atualizado com a persona SOUL_EMAIL.md"
fi

echo "=========================================================="
echo "🎉 SINCRONIZAÇÃO E CONFIGURAÇÃO CONCLUÍDAS COM SUCESSO!"
echo "=========================================================="
echo "Seu Hermes foi sincronizado com o seu GitHub Fork ($GITHUB_USER)!"
echo "Para deixar seu Hermes 100% operacional:"
echo "1. Preencha suas chaves no Portainer Stack Env ou em: /opt/data/.hermes/.env"
echo "2. Para atualizar suas regras de negócio ou sua persona no futuro:"
echo "   Edite-as diretamente no seu GitHub e execute este setup novamente!"
echo "3. Abra o console do Portainer e digite 'hermes' para iniciar!"
echo "=========================================================="
