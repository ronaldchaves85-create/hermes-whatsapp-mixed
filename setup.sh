#!/bin/bash
set -e

# Pega o usuário do GitHub via parâmetro. Se não for passado, usa 'empreendedorserial' como padrão.
GITHUB_USER="${1:-empreendedorserial}"

echo "=========================================================="
echo "🤖 CONFIGURADOR DE MODO MISTO DO EMPREENDEDOR SERIAL 🤖"
echo "           GitHub Fork de: $GITHUB_USER"
echo "=========================================================="

# Define o caminho base do Hermes dentro do container
BASE_DIR="/root/.hermes"
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

# Configura o plugin whatsapp-manager nativamente
mkdir -p "/opt/data/.hermes/plugins/whatsapp-manager"
curl -sSL "$RAW_URL/plugins/whatsapp-manager/plugin.yaml" -o "/opt/data/.hermes/plugins/whatsapp-manager/plugin.yaml"
curl -sSL "$RAW_URL/plugins/whatsapp-manager/__init__.py" -o "/opt/data/.hermes/plugins/whatsapp-manager/__init__.py"
echo "  ✓ Plugin whatsapp-manager sincronizado e atualizado"

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
# Baixa e executa o patch da ponte do WhatsApp
curl -sSL "$RAW_URL/patch_whatsapp.py" -o "/tmp/patch_whatsapp.py"
python3 /tmp/patch_whatsapp.py

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
