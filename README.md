# Hermes WhatsApp Plugin

Plugin **`whatsapp-manager`** para o [Hermes Agent](https://github.com/nousresearch/hermes). Transforma o WhatsApp em assistente pessoal inteligente para o dono e atendente autônomo para clientes — tudo no mesmo número, sem parecer robô.

> **Licença:** [BUSL-1.1](LICENSE) — uso livre para desenvolvimento e testes. Converte para MIT em 2031-06-25.

---

## O que faz

### Para o dono (self-chat)
- Assistente pessoal com acesso ao histórico completo de todas as conversas
- Quando pergunta "o que a Isabel falou?", busca no banco real e injeta no contexto
- Atualização de contatos em linguagem natural: *"a Isabel é minha filha, apelido Bebel"*
- Comandos de controle do bot (pausar, retomar, sincronizar)

### Para clientes e contatos
- Atendimento guiado por `support_rules.md` (produtos, preços, FAQs)
- Tom personalizado por contato via `personal_contacts.json`
- Transcrição automática de áudios e descrição de imagens via Gemini
- Silêncio automático de 10 minutos quando o dono lê ou responde manualmente

### Inteligência de contatos
- Classificação automática: `Cliente | Amigo | AmigoProximo | Parente | Filho | Vendedor`
- Campo `notes` injetado como **instrução obrigatória** no prompt (o LLM obedece)
- Resumo cumulativo por período (`full_summary`) comprimido a cada sync
- Sync com repositório privado do GitHub — contatos e personas versionados

---

## Arquitetura

```
┌──────────────────────────────────────────┐
│  Container: hermes                       │
│  ├─ Hermes Gateway (LLM, hooks, API)     │
│  ├─ Plugin whatsapp-manager (Python)     │
│  └─ Micro-proxy TCP → whatsapp-bridge    │
└──────────────────────────────────────────┘
           ↕ rede Docker
┌──────────────────────────────────────────┐
│  Container: whatsapp-bridge              │
│  └─ bridge.js (Node.js + Baileys)        │
│     Porta 3000 — sessão WhatsApp         │
└──────────────────────────────────────────┘

Volume compartilhado: /opt/data
  ├─ .hermes/plugins/whatsapp-manager/   → plugin ativo
  ├─ .hermes/platforms/whatsapp/         → sessão e bridge.js
  ├─ .hermes/whatsapp_messages.db        → histórico raw (bridge)
  ├─ .hermes/state.db                    → sessões do Hermes
  ├─ .hermes/dedup_suppressed.log        → log de duplicatas suprimidas
  ├─ personal_contacts.json              → perfis dos contatos
  ├─ support_rules.md                    → base de conhecimento (clientes)
  └─ SOUL_WHATSAPP.md                    → persona e estilo de escrita
```

> **Easypanel:** container único — o Hermes gerencia a bridge internamente. Sem micro-proxy TCP.

---

## Estrutura do repositório

```
├── whatsapp_manager.py          # Plugin principal (hooks Python)
├── bridge.js                    # Bridge WhatsApp (Node.js + Baileys)
├── plugin.yaml                  # Manifesto do plugin
├── deploy/
│   ├── docker-compose.yml       # Swarm / Portainer (Traefik)
│   ├── docker-compose.easypanel.yml  # Easypanel (proxy nativo)
│   ├── setup.sh                 # Setup inicial de 1 clique
│   ├── SOUL.md                  # Persona base
│   ├── SOUL_WHATSAPP.md         # Persona WhatsApp
│   ├── support_rules.md         # Regras de suporte (exemplo)
│   └── personal_contacts.json.example
├── tests/
│   └── plugin_test.py           # 263 testes unitários
└── validate_dedup.py            # Validação de dedup no container
```

---

## Instalação

### Pré-requisitos

- Hermes Agent rodando (Portainer ou Easypanel)
- Domínios com DNS apontado para o servidor
- [Google AI Studio](https://aistudio.google.com) — chave da API Gemini
- Repositório privado no GitHub com seus arquivos de configuração

---

### Portainer (Swarm)

**1. Criar a stack**

No Portainer → Stacks → Add stack, cole o conteúdo de [`deploy/docker-compose.yml`](deploy/docker-compose.yml) e configure as variáveis abaixo em *Environment variables*.

**2. Variáveis indispensáveis**

| Variável | Descrição |
|---|---|
| `HERMES_DASH_HOST` | Domínio do Dashboard (ex: `hermes.seu-dominio.com`) |
| `HERMES_API_HOST` | Domínio da API REST (ex: `hermes-api.seu-dominio.com`) |
| `API_SERVER_KEY` | Chave secreta da API — `openssl rand -hex 32` |
| `GOOGLE_API_KEY` | Chave do Gemini — todos os modelos padrão usam Gemini |
| `WHATSAPP_OWNER_NUMBER` | Seu número sem `+` (ex: `5511999999999`) |
| `WHATSAPP_OWNER_NAME` | Seu nome (ex: `André`) |
| `CONFIG_GITHUB_TOKEN` | PAT do GitHub com leitura do repositório de configuração |

**3. Deploy**

Clique em **Deploy the stack**. O Traefik cuida do SSL automaticamente.

---

### Easypanel

**1. Criar serviço Compose**

No Easypanel → New Service → Compose, cole [`deploy/docker-compose.easypanel.yml`](deploy/docker-compose.easypanel.yml).

**2. Variáveis de ambiente**

Na aba *Ambiente*, cole as variáveis e marque a opção **"Criar arquivo .env"** antes de salvar.

> ⚠️ Sem marcar "Criar arquivo .env" o container ignora todas as variáveis e falha ao iniciar.

Mesmas variáveis da seção acima, **exceto** `HERMES_DASH_HOST` e `HERMES_API_HOST` (gerenciados pelo Easypanel).

**3. Domínios**

Na aba *Domains & Proxy*:

| Porta | Destino |
|---|---|
| `9119` | Dashboard + WebSocket (ex: `hermes.seu-dominio.com`) |
| `8642` | API REST (ex: `hermes-api.seu-dominio.com`) |

**4. Deploy**

Clique em **Deploy** e aguarde ficar verde.

---

### Setup inicial (ambas as plataformas)

Após o container subir, abra o console e execute:

```bash
curl -sSL https://raw.githubusercontent.com/SEU_USUARIO/hermes-whatsapp-mixed/main/deploy/setup.sh | bash -s SEU_USUARIO
```

O script configura `SOUL.md`, `support_rules.md`, `config.yaml` e prepara o ambiente. O plugin `whatsapp-manager` deve ser instalado/atualizado pelo **Dashboard do Hermes → Plugins**.

---

### Instalação do Provedor AISA (AISA CLI)

Para utilizar o provedor de IA AISA (aisa.one), você pode solicitar diretamente ao Hermes que instale a CLI. Envie o prompt abaixo no chat do Dashboard, Telegram ou WhatsApp (conversa privada com o dono):

---

```
Hermes, preciso que você instale a AISA CLI no container.
Siga os passos abaixo usando suas ferramentas de terminal:

A chave de API já está configurada e disponível nas variáveis de ambiente do container (variável `AISA_API_KEY` vinda da stack do Portainer / Easypanel).

1. Instale a AISA CLI utilizando um prefixo persistente em `/opt/data/.local` para evitar erros de permissão ao tentar instalar globalmente:
   npm install -g @aisa-one/cli --prefix /opt/data/.local

2. Configure o PATH para incluir o diretório de binários local (`/opt/data/.local/bin`) tanto na sessão atual quanto garantindo sua persistência no ambiente.

3. Valide se a instalação funcionou executando os seguintes comandos e me retorne o output:
   aisa --version
   aisa whoami
```

---

## Conectar o WhatsApp (QR Code)

Após o setup, acesse os endpoints da bridge no domínio do Dashboard:

| URL | Formato |
|---|---|
| `https://hermes.seu-dominio.com/whatsapp/qr` | HTML interativo |
| `https://hermes.seu-dominio.com/whatsapp/qr?format=png` | Imagem PNG |
| `https://hermes.seu-dominio.com/whatsapp/qr?format=svg` | Imagem SVG |
| `https://hermes.seu-dominio.com/whatsapp/status` | Status JSON da conexão |

> Aguarde alguns segundos após o restart para a bridge terminar de subir antes de abrir a URL.

No celular: **WhatsApp → Aparelhos Conectados → Conectar um aparelho** → escaneie o QR.

Após parear, reinicie o container para carregar o estado limpo.

---

## Protegendo o Dashboard

O Dashboard expõe terminal interativo, logs e histórico de conversas. **Nunca deixe público.**

### Portainer (Traefik) — Basic Auth

Gere o hash no terminal:

```bash
htpasswd -nb seu_usuario sua_senha
```

> ⚠️ Cole o output **exatamente como saiu**, sem modificar. Não use `sed` ou `echo` — isso corrompe o hash.

Adicione nas variáveis de ambiente da stack:

| Variável | Valor |
|---|---|
| `HERMES_DASH_AUTH_USERS` | `usuario:$apr1$...` (output do htpasswd) |

Atualize a stack. O Traefik solicita login em todas as rotas do Dashboard (interface, WebSocket, terminal).

### Easypanel — Password Protection

Na aba **Domains & Proxy** do serviço, ative **"Password Protection"** no domínio da porta `9119`. Defina usuário e senha diretamente no Easypanel — sem necessidade de gerar hash.

---

## Comandos no WhatsApp (self-chat)

Envie para si mesmo no WhatsApp (conversa com seu próprio número). Todos os comandos funcionam **exclusivamente para o dono**.

| Comando | Ação |
|---|---|
| `stop_bot` ou `!pausar` | Pausa o atendimento a clientes |
| `start_bot` ou `!retomar` | Reativa o atendimento a clientes |
| `sincronizar contatos` | Sync em background — classifica novos contatos e puxa dados do GitHub |
| `sincronize os contatos` | Mesmo que acima |
| `ajuda` ou `help` | Lista todos os comandos e funcionalidades disponíveis |
| *"quais comandos posso usar?"* | Mesmo que acima (linguagem natural) |
| *"como você funciona?"* | Mesmo que acima (linguagem natural) |
| *"o que você faz?"* | Mesmo que acima (linguagem natural) |

> O assistente pessoal continua funcionando normalmente durante a pausa.

---

## Variáveis de ambiente completas

### Indispensáveis

| Variável | Descrição |
|---|---|
| `API_SERVER_KEY` | Chave secreta da API (sem default — stack não sobe sem ela) |
| `GOOGLE_API_KEY` | Todos os modelos padrão usam Gemini — sem ela o bot não responde |
| `WHATSAPP_OWNER_NUMBER` | Número do dono (formato: `5511999999999`) |
| `WHATSAPP_OWNER_NAME` | Nome do dono usado nos prompts |
| `CONFIG_GITHUB_TOKEN` | Token GitHub — sem ele `personal_contacts.json` e `SOUL_WHATSAPP.md` não sincronizam |
| `HERMES_DASH_HOST` | *(Portainer)* Domínio do Dashboard |
| `HERMES_API_HOST` | *(Portainer)* Domínio da API |

### Providers de IA alternativos

| Variável | Descrição |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (Anthropic) |
| `OPENAI_API_KEY` | GPT-4 e família |
| `OPENROUTER_API_KEY` | Acesso multi-provider |
| `AISA_API_KEY` | Chave da AISA (provider de IA) |

### Modelos por função (padrão: `gemini-3.1-flash-lite` / provider: `gemini`)

| Variável | Função |
|---|---|
| `WHATSAPP_OWNER_MODEL` / `WHATSAPP_OWNER_PROVIDER` | Respostas ao dono |
| `WHATSAPP_CLIENT_MODEL` / `WHATSAPP_CLIENT_PROVIDER` | Respostas a clientes |
| `WHATSAPP_CLIENT_MEDIA_MODEL` | Transcrição e análise de mídia |
| `WHATSAPP_CONTACT_CLASSIFIER_MODEL` | Classificação de novos contatos |

### WhatsApp

| Variável | Padrão | Descrição |
|---|---|---|
| `WHATSAPP_ENABLED` | `true` | Habilitar WhatsApp (deixe `false` até parear) |
| `WHATSAPP_MODE` | `bot` | Modo de operação |
| `WHATSAPP_ALLOWED_USERS` | `*` | Números autorizados (`*` = todos) |
| `WHATSAPP_CONNECTION_NAME` | `EmpreendedorSerial` | Nome da conexão no Dashboard |
| `WHATSAPP_QR_PATH` | `/whatsapp` | Path do endpoint QR/status |

### Sincronização GitHub

| Variável | Padrão | Descrição |
|---|---|---|
| `HERMES_SETUP_GITHUB_USER` | `empreendedorserial` | Usuário dono do repositório de configuração |
| `CONFIG_REPO` | `hermes_agent_context_contatcs` | Nome do repositório privado |
| `CONFIG_GITHUB_TOKEN` | — | PAT com acesso de leitura |

### Outros

| Variável | Padrão | Descrição |
|---|---|---|
| `MAX_TURNS` | `8` | Máximo de iterações por resposta (controla custo) |
| `TZ` | `America/Sao_Paulo` | Fuso horário |
| `HERMES_API_TIMEOUT` | `1800` | Timeout da API em segundos |
| `GATEWAY_ALLOW_ALL_USERS` | `false` | Permitir qualquer usuário interagir |

---

## Perfil de contato (`personal_contacts.json`)

```json
{
  "5511999999999": {
    "name": "Nome completo",
    "relationship": "Cliente|Amigo|AmigoProximo|Parente|Filho|Vendedor",
    "manual_relationship": "nunca sobrescrito pelo sync automático",
    "nickname": "apelido",
    "tone": "tom de atendimento",
    "guidelines": "instruções gerais de comportamento",
    "notes": "instrução pontual — injetada como diretiva obrigatória no prompt",
    "summary": "resumo comprimido (1-2 frases) injetado no contexto",
    "full_summary": "histórico cumulativo por período (Jun/25: ..., Jul/25: ...)",
    "last_interaction": "2025-06-25T14:30:00"
  }
}
```

**Busca de contato — 6 níveis em cascata:**
1. Número/JID exato
2. Campo `name` exato no JSON
3. Campo `nickname` exato no JSON
4. Substring em `name`
5. `sender_name` no `whatsapp_messages.db`
6. `/contacts/search?name=X` na bridge (store do Baileys)

---

## Hooks do plugin

| Hook | Função |
|---|---|
| `pre_gateway_dispatch` | Detecta comandos, processa mídia, cross-session history, silenciamento, sync |
| `pre_llm_call` | Monta prompt personalizado por tipo de contato, injeta contexto e `support_rules.md` |
| `post_llm_call` | Dedup de respostas (session + turn), filtra tool results, notificações de status |

### Dedup de respostas duplicadas

Duas camadas independentes previnem envio duplo (especialmente para números internacionais):

- **Session-level:** mesma `session_id` só envia uma vez (protege contra race conditions)
- **Turn-level:** mesmo chat + mensagem só é respondido uma vez por sessão de chat

Eventos suprimidos são registrados em `/opt/data/.hermes/dedup_suppressed.log`.

---

## Arquivos de configuração (em `/opt/data/`)

| Arquivo | Descrição |
|---|---|
| `personal_contacts.json` | Perfis, resumos e instruções por contato |
| `support_rules.md` | Produtos, preços, FAQs e diretrizes de atendimento a clientes |
| `SOUL_WHATSAPP.md` | Persona, estilo de escrita e exemplos práticos de conversa |
| `SOUL.md` | Persona base do assistente pessoal |

Estes arquivos são sincronizados do repositório privado do GitHub a cada sync. Edite diretamente no GitHub ou pelo gerenciador de arquivos do Dashboard do Hermes.

---

## Deploy de atualizações

```bash
# 1. Mac — commit e push
git add whatsapp_manager.py
git commit -m "..."
git push

# 2. Container hermes — console do Portainer
cd /opt/data/workspace/hermes-whatsapp-mixed && git pull origin main
cp whatsapp_manager.py /opt/data/.hermes/plugins/whatsapp-manager/whatsapp_manager.py

# 3. Reiniciar container hermes no Portainer
```

---

## Testes

```bash
# Suite principal (263 testes)
python3 -m unittest tests.plugin_test -q

# Validação de dedup no container
python3 /opt/data/workspace/hermes-whatsapp-mixed/validate_dedup.py
```

---

## Bancos de dados

| Arquivo | Conteúdo |
|---|---|
| `/opt/data/.hermes/whatsapp_messages.db` | Histórico raw de mensagens (bridge) |
| `/opt/data/.hermes/state.db` | Sessões e contexto do Hermes |
| `/opt/data/personal_contacts.json` | Perfis e resumos dos contatos |
| `/opt/data/.hermes/dedup_suppressed.log` | Log de duplicatas suprimidas |

---

*Desenvolvido e mantido por [André Alencar](https://aalencar.com.br) / Empreendedor Serial.*
