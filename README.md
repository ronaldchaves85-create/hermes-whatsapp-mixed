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

## 🕹️ Comandos e Silenciamento Inteligente

O robô conta com recursos nativos de controle diretamente pelo WhatsApp:

* 🚫 **`stop_bot`** (ou `!pausar` / `!parar`): Pausa globalmente o bot comercial para clientes. O seu assistente pessoal no Self-Chat continua respondendo você normalmente.
* 🟢 **`start_bot`** (ou `!retomar` / `!iniciar`): Reativa o bot comercial para voltar a responder seus clientes no piloto automático.
* 🔇 **Silenciamento Temporário por Conversa (10 min)**: Sempre que você ler/abrir um chat de cliente ou enviar uma mensagem manual, o bot entra automaticamente em silêncio especificamente para aquela conversa por 10 minutos, permitindo que você atenda o cliente de forma manual sem interferência da IA.

---

## 🐋 Como Fazer Deploy da Stack

Todos os arquivos de deploy de contêineres e configurações avançadas foram movidos para a pasta `/deploy`.

* Para realizar a implantação utilizando **Portainer** (com Traefik, rotas WebSocket estáveis e reinicialização inteligente de contêineres), siga as instruções em [deploy/docker-compose.yml](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/docker-compose.yml).
* Para implantação com **Easypanel**, utilize o arquivo [deploy/docker-compose.easypanel.yml](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/docker-compose.easypanel.yml).

> 📖 **Instruções Detalhadas:** Para um guia de implantação completo passo a passo, personalização de personas (`SOUL_WHATSAPP.md`), regras de negócio (`support_rules.md`) e pareamento de múltiplas contas, leia o manual completo em [deploy/README.md](file:///Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed/deploy/README.md).

---

## 🧪 Testes de Regressão

Este repositório possui uma suite de testes de regressão automatizados para validar o comportamento da ponte do WhatsApp, garantindo que alterações futuras não quebrem regras de negócio críticas (como comandos de pausa e silenciamento de chats).

### O que é testado:
1. **Comandos globais no Self-Chat:** Garante que `start_bot`/`stop_bot` pausam e resumem o bot comercial apenas no chat pessoal.
2. **Ignorar comandos no chat de clientes:** Garante que os comandos de controle digitados em chats comerciais de clientes não são executados pela ponte.
3. **Silenciamento temporário por mensagem manual:** Garante que o bot seja silenciado por exatamente 10 minutos (600.000 ms) quando o dono responde manualmente a um cliente.
4. **Isenção de comandos no silenciamento:** Garante que comandos como `!suporte status` ou semelhantes enviados pelo dono no chat de clientes não ativem o silenciamento por engano.
5. **Silenciamento temporário por leitura:** Garante que o bot seja silenciado por exatamente 10 minutos quando o dono marca/lê a conversa no celular ou web (evento `chats.update` com contagem zero).
6. **Isenção de silêncio no Self-Chat:** Garante que o chat pessoal do dono nunca seja silenciado.

### Como rodar os testes localmente:
1. Instale as dependências:
   ```bash
   npm install
   ```
2. Execute a suite de testes:
   ```bash
   npm test
   ```

---
*Desenvolvido e mantido pela Comunidade Empreendedor Serial (André Alencar).*
