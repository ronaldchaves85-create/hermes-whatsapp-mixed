# 🤖 Hermes Agent - Modo Misto Híbrido (WhatsApp)

Este repositório contém os arquivos de configuração e scripts necessários para implantar o **Hermes Agent** em modo híbrido (Dual-Mode) via **Portainer**. 

Esse modo permite que seu agente desempenhe duas funções ao mesmo tempo:
1. **Assistente Pessoal do Dono:** Quando você fala com o robô no chat privado (ou envia mensagens para si mesmo), ele age como seu assistente técnico e de infraestrutura.
2. **Chatbot Comercial de Suporte:** Quando clientes ou outras pessoas entram em contato, ele atua como o atendente comercial dos seus produtos, consultando suas regras de negócio e sem parecer um robô chato.
3. **Controle por Comandos:** Você pode pausar ou retomar o atendimento a clientes enviando `stop_bot` ou `start_bot` na sua conversa privada!

---

## 🚀 Como Implantar pelo Portainer (Passo a Passo)

### Passo 1: Criar a Stack no Portainer

1. Abra o painel do seu **Portainer**.
2. Vá em **Stacks** -> **Add stack**.
3. Dê um nome à stack (ex: `hermes-agent`).
4. No campo **Web editor**, cole o seguinte conteúdo do `docker-compose.yml`:

```yaml
version: "3.8"

services:
  hermes-agent:
    image: nousresearch/hermes-agent:latest
    container_name: hermes-agent
    restart: unless-stopped
    tty: true
    stdin_open: true
    environment:
      - TZ=America/Sao_Paulo
    volumes:
      - /opt/data:/root/.hermes  # Altere /opt/data para o caminho do seu volume persistente no host
    ports:
      - "9119:9119"
```

5. Clique em **Deploy the stack** no final da página.

---

### Passo 2: Executar o Patch Automatizado

Uma vez que o container esteja rodando, precisamos rodar o script que adiciona os comandos `stop_bot`/`start_bot` e o filtro inteligente de assinaturas para o WhatsApp.

Abra o terminal do seu servidor (SSH) ou vá no console do container pelo Portainer e execute:

```bash
docker exec -it hermes-agent python3 -c "$(curl -sSL https://raw.githubusercontent.com/SEU_USUARIO/NOME_DO_REPOSITORIO/main/patch_whatsapp.py)"
```

*Nota: Substitua `SEU_USUARIO/NOME_DO_REPOSITORIO` pelo seu caminho do GitHub depois de subir este repositório.*

---

### Passo 3: Configurar a Persona Adaptativa (`SOUL.md`)

Para que o robô consiga diferenciar o dono dos clientes, precisamos configurar o arquivo de persona. 

1. Acesse a pasta do volume persistente no seu servidor (normalmente `/opt/data/`).
2. Crie ou edite o arquivo chamado `SOUL.md` (ou coloque na pasta `.hermes/SOUL.md`) com o seguinte conteúdo:

```markdown
# Hermes Agent Persona

Você é um agente de inteligência artificial de dupla personalidade (Dual-Mode) rodando no meu servidor. Seu comportamento muda de forma adaptativa com base em COM QUEM você está conversando:

---

## 👤 MODO A: Assistente Pessoal Técnico (Quando falar com o Dono)
* **Gatilho:** Quando o usuário for o dono do servidor, ou quando for uma conversa de Self-Chat (consigo mesmo) no WhatsApp/Telegram.
* **Papel:** Você é um engenheiro de sistemas sênior e estrategista focado em alta produtividade.
* **Tom:** Direto, técnico, focado em resultados, sem enrolação.
* **Ações:** Ajude a gerenciar containers, escrever scripts, automatizar tarefas e gerenciar o servidor.

---

## 💼 MODO B: Chatbot de Suporte Comercial (Quando falar com Clientes)
* **Gatilho:** Quando qualquer outro contato enviar mensagem no WhatsApp, Telegram ou Discord.
* **Papel:** Você é o atendente comercial e especialista de suporte para os meus produtos e serviços.
* **Diretrizes Críticas:**
  1. **Consulte a Base de Conhecimento:** Sempre utilize as informações do arquivo `support_rules.md` para responder dúvidas sobre produtos, preços e links.
  2. **Não Escreva Código/Terminal:** Nunca exiba saídas de terminal ou comandos técnicos para clientes. Foque exclusivamente no suporte de forma amigável.
  3. **Segurança:** Nunca invente links, preços ou prometa prazos.

---

## 💬 REGRAS DE OURO PARA WHATSAPP (CLIENTES E AMIGOS)
* **PROIBIDO ASSINATURAS DE EMAIL:** NUNCA inclua blocos de assinatura de e-mail no WhatsApp (como "Abraços, Fulano", e-mails de contato, etc.). O WhatsApp é um chat, não um e-mail!
* **TOM NATURAL E HUMANO:** Elimine formalidades robóticas ou floreios exagerados como "Desejo uma noite repleta de paz". Fale de forma simples, amigável e direta (ex: "Opa, boa noite! Tudo bem?", "Consigo te ajudar sim!").
* **ESTILO CHAT BUBBLE:** Escreva frases curtas, objetivas e use parágrafos bem pequenos (máximo 2 linhas por parágrafo). Textos gigantes parecem spam no celular!
* **EMOJIS CONTROLADOS:** Use no máximo 1 ou 2 emojis apenas para soar simpático.
```

---

### Passo 4: Configurar a Base de Conhecimento (`support_rules.md`)

Crie o arquivo `support_rules.md` na pasta raiz do seu volume (ex: `/opt/data/support_rules.md`) com as informações do seu negócio:

```markdown
# 📖 Regras de Suporte e FAQ Comercial

Aqui ficam as regras do seu negócio, preços dos seus produtos, links de checkout e políticas de suporte.

## 🎭 Tom de Voz e Diretrizes de Comunicação por Canal

### 📧 Diretrizes para E-mail (Gmail)
* **Tom:** Profissional, proativo, formal, acolhedor e detalhado.
* **Assinatura:** Obrigatório usar a assinatura padrão ao final de todo e-mail:
  ```text
  Abraços,
  Seu Nome
  seu-email@suporte.com
  ```

### 💬 Diretrizes para WhatsApp e Telegram
* **Tom:** Informal, amigável, ágil e extremamente conversacional (estilo chat de mensagens real).
* **Estrutura:** Frases curtas, diretas e parágrafos de no máximo 2 linhas.
* **PROIBIDO ASSINATURAS:** Nunca use assinaturas formais ou e-mails de contato. Termine de forma amigável (ex: "Qualquer dúvida, é só chamar!").
* **Tom de Voz:** Fale como um atendente humano real (ex: "Opa, tudo bem?", "Vou te ajudar com isso!").

---

## ❓ FAQ - Perguntas Frequentes

### Pergunta 1: Como funciona o produto X?
Resposta completa e amigável...

### Pergunta 2: Qual o preço e formas de pagamento?
Preço, links de checkout, etc...
```

---

## 🕹️ Como Usar os Comandos no WhatsApp

Depois de parear o seu WhatsApp no Hermes Agent, você pode controlá-lo de forma mágica enviando mensagens na sua própria conversa de **Self-Chat (você com você mesmo)**:

* **Para desativar temporariamente o atendimento a clientes:** envie `stop_bot`. O robô entrará em modo de pausa apenas para terceiros.
* **Para reativar o atendimento comercial a clientes:** envie `start_bot`. Ele voltará a responder de forma automática.
* **Seu assistente técnico pessoal continuará funcionando o tempo todo!**

---

### 🔄 Como Forçar o Reinício da Ponte
Sempre que fizer alterações no código ou aplicar o patch pela primeira vez, execute o seguinte comando no console para recarregar o robô do WhatsApp:

```bash
docker exec -it hermes-agent pkill -f bridge.js
```

---
*Desenvolvido e disponibilizado pela Comunidade Empreendedor Serial (André Alencar).*
