# 🤖 Hermes Agent - Modo Misto Híbrido (WhatsApp + Gmail)

Este repositório contém os arquivos de configuração, templates e scripts necessários para implantar o **Hermes Agent** em modo híbrido (Dual-Mode) via **Portainer** ou **Easypanel**. 

Esse modo permite que seu agente desempenhe duas funções ao mesmo tempo:
1. **Assistente Pessoal do Dono:** Quando você fala com o robô no chat privado (ou envia mensagens para si mesmo no WhatsApp/Telegram), ele age como seu assistente técnico e de infraestrutura com permissões para rodar comandos do terminal.
2. **Chatbot Comercial de Suporte:** Quando clientes ou outras pessoas entram em contato por **WhatsApp, Telegram ou e-mail (Gmail)**, ele atua como o atendente comercial dos seus produtos, consultando suas regras de negócio e sem parecer um robô chato.
3. **Controle por Comandos:** Você pode pausar ou retomar o atendimento a clientes enviando `stop_bot` ou `start_bot` na sua conversa privada!

---

## 📂 O que está incluído neste repositório:
* 🐋 **`docker-compose.yml`**: Arquivo de produção otimizado para Portainer Stack, pré-configurado com suporte completo ao **Traefik** e rotas WebSockets seguras.
* 🟣 **`docker-compose.easypanel.yml`**: Arquivo adaptado para implantação no Easypanel (sem Swarm, sem Traefik externo — SSL e proxy gerenciados automaticamente).
* ⚡ **`setup.sh`**: Script de configuração e sincronização de 1 clique que vincula seu servidor ao seu repositório pessoal no GitHub.
* 🐍 **`patch_whatsapp.py`**: Script de automação universal que reconfigura a ponte do WhatsApp (filtro de assinaturas inteligente e novos comandos).
* ⚙️ **`config.yaml.example`**: Configuração pré-otimizada para alta performance, ativação de memória persistente e prevenção de spam em grupos de WhatsApp.
* 🔑 **`.env.example`**: Modelo de exemplo para as variáveis de ambiente necessárias (ignorado por segurança pelo `.gitignore` para evitar vazamentos).
* 👤 **`SOUL.md`**: Persona pré-configurada para o funcionamento do Modo Duplo (Dono vs Clientes).
* 📖 **`support_rules.md`**: Modelo estruturado de base de conhecimento separando as diretrizes de e-mail e WhatsApp.

---

## 🚀 Como Implantar pelo Portainer (Passo a Passo)

### Passo 1: Fazer um Fork deste Repositório e Personalizar 🎨

Em vez de editar arquivos complexos no terminal do seu servidor, você vai usar o próprio **GitHub como seu gerenciador visual (CMS)**!

1. Na parte superior desta página, clique no botão **Fork** para criar uma cópia deste repositório na sua própria conta do GitHub (ex: `github.com/SEU_USUARIO_GITHUB/hermes-whatsapp-mixed`).
2. Dentro do seu repositório pessoal recém-criado, edite os arquivos diretamente pelo seu navegador:
   * **`support_rules.md`**: Preencha o documento com as informações do seu negócio, preços, links de checkout (Kiwify, Hotmart, etc.) e formas de suporte. Clique em **Commit changes** para salvar.
   * **`SOUL.md`**: Se quiser, mude o tom do robô ou adicione instruções personalizadas de persona. Salve.
3. Pronto! Seus arquivos de persona e regras de negócio agora estão guardados com segurança e com histórico no seu próprio GitHub.

---

### Passo 2: Criar a Stack e Definir as Credenciais no Portainer

Toda a infraestrutura de rede, domínios SSL e chaves de API é gerenciada de forma visual na interface gráfica do Portainer:

1. Acesse seu painel do **Portainer** -> **Stacks** -> **Add stack**.
2. Dê um nome à stack (ex: `hermes-agent`).
3. No campo **Web editor**, cole o conteúdo do arquivo `docker-compose.yml` deste repositório.
4. Em **Environment variables** (Variáveis de ambiente) na interface do Portainer, configure os seus domínios e chaves:
   * **`HERMES_DASH_HOST`**: O domínio do seu painel visual (ex: `hermes.seu-dominio.com`).
   * **`HERMES_API_HOST`**: O subdomínio para a API (ex: `hermes-api.seu-dominio.com`).
   * **`GOOGLE_API_KEY`**: Sua chave da API do Gemini (usada para pensar).
   * **`API_SERVER_KEY`**: Uma senha secreta para proteger a API do seu robô.
5. Clique em **Deploy the stack** para criar seu container.

> 🔒 **Traefik Integrado:** O arquivo `docker-compose.yml` já possui todas as `labels` de produção do Traefik mapeadas. Ele cuida da geração e renovação de certificados SSL da Let's Encrypt de forma 100% nativa e automática!

---

### Passo 3: Sincronização e Instalação de 1 Clique ⚡

Agora, vamos fazer com que o seu servidor baixe automaticamente os arquivos que você personalizou no seu repositório pessoal do GitHub:

1. No Portainer, clique em **Containers** e clique no ícone de **Console** (`>_`) do container `hermes-agent`.
2. Clique em **Connect** para abrir o terminal integrado.
3. Substitua `SEU_USUARIO_GITHUB` pelo seu usuário real do GitHub no comando abaixo, cole-o no console e aprete Enter:

```bash
curl -sSL https://raw.githubusercontent.com/SEU_USUARIO_GITHUB/hermes-whatsapp-mixed/main/setup.sh | bash -s SEU_USUARIO_GITHUB
```

> **O que o script fez por você?** Ele baixou a persona (`SOUL.md`) e as regras de negócio (`support_rules.md`) diretamente do seu GitHub Fork pessoal, configurou as otimizações no `config.yaml` e corrigiu a ponte do WhatsApp!

💡 **Dica de Sincronização:** Toda vez que você quiser alterar as regras do seu negócio ou atualizar sua persona, basta editá-las no seu GitHub e rodar este mesmo comando de novo. Seu servidor atualizará tudo em segundos!

---

### Passo Extra: Ajustes Rápidos pela Web UI (Dashboard do Hermes) 🎨

Se você precisar fazer um ajuste rápido de última hora (mudar um preço, corrigir uma palavra na sua Persona) e não quiser usar o GitHub naquele momento, você pode editar tudo de forma visual:

1. Acesse o painel visual do Hermes direto no seu navegador:
   👉 `https://hermes.seu-dominio.com` *(Graças ao Traefik, já com HTTPS/SSL ativo!)*
2. No menu lateral, acesse o **gerenciador de arquivos visual** integrado.
3. Você pode clicar para abrir e **editar diretamente pela interface Web** os arquivos:
   * 📄 **`support_rules.md`** (Regras de Suporte e FAQ).
   * 📄 **`SOUL.md`** (Arquivo de Persona e Personalidade).
4. Basta clicar em **Salvar** e o Hermes assumirá as alterações no mesmo instante!

*⚠️ Nota: Lembre-se de que se você rodar o setup.sh novamente com o parâmetro do seu GitHub, ele baixará as versões do seu repositório. Por isso, para alterações definitivas, o recomendado é atualizar sempre o seu GitHub Fork!*

---

## 🟣 Como Implantar pelo Easypanel (Passo a Passo)

> O Easypanel gerencia automaticamente o proxy reverso (Traefik), SSL via Let's Encrypt e os volumes — sem configurações manuais de rede.

### Passo 1: Criar o Projeto no Easypanel

1. Acesse o painel do seu **Easypanel**.
2. Clique em **+ Create Project**.
3. Dê um nome ao projeto (ex: `hermes`).
4. Clique em **Create**.

---

### Passo 2: Criar o Serviço Compose

1. Dentro do projeto, clique em **+ Create Service**.
2. Selecione o tipo **Compose**.
3. Dê o nome `hermes` ao serviço.
4. No campo de conteúdo, cole o conteúdo do arquivo **`docker-compose.easypanel.yml`** deste repositório.
5. Clique em **Save** para salvar a configuração.

---

### Passo 3: Configurar as Variáveis de Ambiente

1. Na tela do serviço, clique na aba **Environment**.
2. Cole o conteúdo abaixo e preencha os valores:

```env
# OBRIGATÓRIA
API_SERVER_KEY=sua_chave_secreta_aqui

# Provedores de IA (pelo menos uma)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OPENROUTER_API_KEY=

# Telegram (opcional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=
GATEWAY_ALLOW_ALL_USERS=false

# WhatsApp (deixe false até parear)
WHATSAPP_ENABLED=false
WHATSAPP_OWNER_NUMBER=
WHATSAPP_MODE=bot
WHATSAPP_ALLOWED_USERS=*

# Google OAuth (opcional)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Configurações gerais
TZ=America/Sao_Paulo
HERMES_API_TIMEOUT=1800
HERMES_API_CALL_STALE_TIMEOUT=300
API_SERVER_CORS_ORIGINS=*
```

3. Clique em **Save**.

---

### Passo 4: Configurar os Subdomínios (Domains & Proxy)

O Hermes expõe **duas portas** que precisam de subdomínios separados:

| Porta | Finalidade | Subdomínio sugerido |
|---|---|---|
| `9119` | Dashboard Web + WebSocket (PTY, eventos) | `hermes.seu-dominio.com` |
| `8642` | API REST (integrações externas) | `hermes-api.seu-dominio.com` |

> ⚠️ **Antes de adicionar os domínios**, certifique-se de que o DNS de ambos os subdomínios já aponta para o IP do seu servidor. O Easypanel gera o SSL automaticamente via Let's Encrypt assim que o domínio resolver corretamente.

**Para configurar o domínio do Dashboard (principal):**

1. Na tela do serviço, clique na aba **Domains**.
2. Clique em **+ Add Domain**:
   - **Domain:** `hermes.seu-dominio.com`
   - **Port:** `9119`
   - Marque como **Primary Domain** (clique na estrela ⭐)
3. Clique em **Save**.

**Para configurar o domínio da API:**

1. Clique em **+ Add Domain** novamente:
   - **Domain:** `hermes-api.seu-dominio.com`
   - **Port:** `8642`
2. Clique em **Save**.

> 💡 O Easypanel suporta WebSocket nativamente — os caminhos `/api/ws`, `/api/events` e `/api/pty` funcionarão automaticamente no domínio do Dashboard sem configuração adicional.

---

### Passo 5: Fazer o Deploy

1. Clique em **Deploy** para iniciar o container.
2. Aguarde o status ficar verde (**Running**).
3. Acesse `https://hermes.seu-dominio.com` para verificar o Dashboard.

---

### Passo 6: Setup Inicial de 1 Clique ⚡

1. Na tela do serviço, clique na aba **Console**.
2. Clique em **Connect** para abrir o terminal integrado.
3. Cole o comando abaixo e pressione Enter (substitua `SEU_USUARIO_GITHUB` pelo seu usuário):

```bash
curl -sSL https://raw.githubusercontent.com/SEU_USUARIO_GITHUB/hermes-whatsapp-mixed/main/setup.sh | bash -s SEU_USUARIO_GITHUB
```

**O setup irá:**
* Configurar a persona (`SOUL.md`) em `/opt/data/SOUL.md`
* Criar as regras de suporte (`support_rules.md`) em `/opt/data/support_rules.md`
* Criar o `config.yaml` em `/root/.hermes/config.yaml`
* Criar o `.env` em `/root/.hermes/.env`
* Aplicar o patch do WhatsApp automaticamente

> Todos os arquivos são salvos em volumes persistentes — sobrevivem a restarts e atualizações de imagem.

---

### Passo 7: Inserir Chaves de API e Regras de Negócio

Acesse os arquivos pelo Console do Easypanel ou pelo gerenciador de arquivos do Dashboard:

1. **Chaves de API:** Edite `/root/.hermes/.env` com suas chaves (ex: `OPENROUTER_API_KEY`).
2. **Regras do Negócio:** Edite `/opt/data/support_rules.md` com os dados do seu produto.

---

### 💾 Mapa de Persistência no Easypanel

| Volume | Caminho no container | O que persiste |
|---|---|---|
| `hermes_data` | `/opt/data` | SOUL.md, support_rules.md, workspace, HERMES_HOME |
| `hermes_root` | `/root/.hermes` | config.yaml, .env (criados pelo setup.sh) |

> Os volumes ficam em `/etc/easypanel/projects/<projeto>/hermes/volumes/` no servidor.

---

## 📧 Como Conectar e Ativar o Suporte via Gmail (OAuth Conversacional)

Em vez de digitar códigos no terminal ou usar senhas manuais, você pode autenticar sua conta de e-mail de suporte conversando com o seu Hermes diretamente pelo **console (chat do terminal) ou pelo Telegram**!

### 1. Garantir as Credenciais do Google na Stack
Certifique-se de que as credenciais do seu cliente Google Web estejam definidas nas variáveis da sua Stack do Portainer (como explicado no Passo 2):
* `GOOGLE_CLIENT_ID`
* `GOOGLE_CLIENT_SECRET`
* *Nota: No Google Cloud Console, a URI de redirecionamento autorizada deve ser configurada como: `http://localhost:1`.*

### 2. Pedir o Link ao Bot (No Console do Hermes ou no Telegram)
1. Abra o chat com o seu Hermes (digite `hermes` no console do container, ou envie uma mensagem direta no seu Telegram/WhatsApp do bot pareado).
2. Envie a seguinte instrução para o bot:
   > *"Hermes, inicie o fluxo de login do Gmail de suporte da minha empresa."*
3. O robô vai iniciar o processo em segundo plano e responderá diretamente no chat com um **Link de Consentimento da Google**.
4. Clique no link, faça login com a conta de e-mail de suporte da sua empresa e conceda as permissões de acesso ao Gmail.

### 3. Entregar o Link de Retorno ao Bot
1. Após permitir o acesso, o navegador tentará redirecionar para uma página em branco que começará com:
   👉 `http://localhost:1/?code=4/0Ad...`
2. Copie essa **URL completa diretamente da barra de endereços** do seu navegador.
3. Volte ao seu chat com o Hermes (Console ou Telegram) e apenas envie a URL copiada para ele.

> **Pronto!** O Hermes processará a URL automaticamente, salvará suas credenciais criptografadas e responderá confirmando o sucesso da autenticação. A partir deste momento, ele começará a responder e-mails de suporte de forma 100% autônoma baseando-se nas suas `support_rules.md`!

---

## 💾 Persistência de Dados e Caminhos

Como o Hermes Agent roda containerizado, qualquer arquivo criado fora dos caminhos mapeados no seu volume persistente (`/opt/data`) **será perdido permanentemente** toda vez que o container for atualizado, reiniciado ou recriado.

### 📍 Caminhos Persistentes (SEMPRE SALVE AQUI):
* **Configurações e Chaves:** `HERMES_HOME=/opt/data/.hermes`
* **Área de Trabalho:** `WORKSPACE=/opt/data/workspace`
* **Scripts e Automações:** `/opt/data/scripts`
* **Projetos de Código:** `/opt/data/projects`
* **Arquivos e Bancos:** `/opt/data/files`

---

## 🕹️ Como Usar os Comandos no WhatsApp

Depois de parear o seu WhatsApp no Hermes Agent, você pode controlá-lo enviando mensagens na sua própria conversa de **Self-Chat (você com você mesmo)**:

* **Para desativar temporariamente o atendimento a clientes:** envie `stop_bot`. O robô entrará em modo de pausa apenas para terceiros.
* **Para reativar o atendimento comercial a clientes:** envie `start_bot`. Ele voltará a responder de forma automática.
* **Seu assistente técnico pessoal continuará funcionando o tempo todo!**

---

## 🔄 Como Reiniciar a Ponte do WhatsApp de Forma Segura

Toda vez que você rodar o script de setup para sincronizar novas regras ou atualizar sua persona, a ponte do WhatsApp precisa ser recarregada para carregar as novas instruções de forma limpa.

**A forma mais segura, robusta e recomendada de fazer isso em ambiente Docker/Portainer é:**
1. Acesse o seu painel do **Portainer**.
2. Vá em **Stacks** e clique na sua stack `hermes-agent`.
3. Clique em **Restart** (Reiniciar) ou em **Update the stack** para atualizar o container de forma limpa e segura.

*💡 Dica técnica: Evite usar comandos de kill (como pkill -f bridge.js) diretamente de dentro do console do container. Deixar o Portainer gerenciar o ciclo de vida do container previne o travamento de portas e a criação de processos zumbis de Node.js no seu servidor!*

---
*Desenvolvido e disponibilizado pela Comunidade Empreendedor Serial (André Alencar).*
