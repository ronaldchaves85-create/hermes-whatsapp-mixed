# 🤖 Hermes Agent - WhatsApp & Email Dual-Mode

Este repositório contém o plugin **`whatsapp-manager`** para o Hermes Agent, juntamente com todos os arquivos de configuração, templates e scripts para implantação em produção.

---

## 📂 Nova Estrutura do Repositório

Para facilitar a instalação automática pelo painel do Hermes, reorganizamos o repositório da seguinte forma:

```text
/ (Raiz do repositório)
├── plugin.yaml          # Manifesto de metadados do plugin
├── __init__.py          # Hooks de bootstrap automático e injeção de contexto
├── whatsapp_manager.py  # Gerenciador principal do comportamento do WhatsApp
├── bridge.js            # Arquivo da ponte do WhatsApp (Baileys)
├── package.json         # Dependências da ponte do WhatsApp
├── docs/                # [Compatibilidade] Pasta docs original (bridge-artifacts)
└── deploy/              # Arquivos de infraestrutura, manuais e deploys
    ├── docker-compose.yml
    ├── docker-compose.easypanel.yml
    ├── setup.sh
    ├── patch_whatsapp.py
    ├── config.yaml.example
    ├── README.md        # Documentação detalhada completa
    └── ... (arquivos de regras de negócio, personas e scripts)
```

---

## ⚡ Instalação Rápida do Plugin

Como o plugin agora está localizado diretamente na **raiz** do repositório, você pode instalá-lo de forma 100% visual no painel do seu Hermes:

1. Acesse o **Hermes Dashboard** no navegador.
2. Vá até a seção **Plugins**.
3. No campo de instalação Git, cole a URL do repositório:
   ```text
   https://github.com/empreendedorserial/hermes-whatsapp-mixed
   ```
4. Clique em **Install/Enable**. O Hermes fará o clone automático e inicializará todos os arquivos necessários.

---

## 🐋 Como Fazer Deploy da Stack

Todos os arquivos de deploy de contêineres e configurações avançadas foram movidos para a pasta `/deploy`.

* Para realizar a implantação utilizando **Portainer** (com Traefik, rotas WebSocket estáveis e reinicialização inteligente de contêineres), siga as instruções em [deploy/docker-compose.yml](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/docker-compose.yml).
* Para implantação com **Easypanel**, utilize o arquivo [deploy/docker-compose.easypanel.yml](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/docker-compose.easypanel.yml).

> 📖 **Instruções Detalhadas:** Para um guia de implantação completo passo a passo, personalização de personas (`SOUL_WHATSAPP.md`), regras de negócio (`support_rules.md`) e pareamento de múltiplas contas, leia o manual completo em [deploy/README.md](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/README.md).

---
*Desenvolvido e mantido pela Comunidade Empreendedor Serial (André Alencar).*
