# 📐 Arquitetura e Regras de Controle do Chatbot (WhatsApp)

Este documento descreve as diretrizes arquiteturais, o funcionamento dos comandos de controle e a regra de silenciamento temporário do chatbot do WhatsApp para evitar conflitos de comportamento e garantir total clareza.

---

## 1. 🎛️ Pausa Global (Controlada na Ponte)

O chatbot possui um mecanismo de pausa global que suspende o atendimento automático para **todos os clientes**. Ele **não afeta** as mensagens enviadas pelo dono na sua conversa pessoal com o bot (o assistente pessoal continua respondendo o dono normalmente).

* **Comandos:** `stop_bot` / `start_bot` (e seus sinônimos como `!pausar`, `!retomar`, `!parar`, `!iniciar`).
* **Escopo:** Nível Node.js (`bridge.js`). O status é persistido no arquivo `bot_state.json`.
* **Restrição de Execução:** Estes comandos **só funcionam se forem enviados pelo dono dentro da sua própria conversa pessoal/self-chat** com o bot. Digitar `start_bot` ou `stop_bot` na conversa de um cliente não fará nada.
* **Comportamento:** Ao pausar, a ponte descarta na origem as mensagens recebidas de qualquer pessoa que não seja o dono. O plugin Python também verifica esse estado no boot e desvia o fluxo se o bot estiver pausado.

---

## 2. 🔇 Silenciamento Temporário (Conversas Específicas com Clientes)

O silenciamento serve para que o bot **não interfira** quando o dono decide falar diretamente ou ler a conversa de um cliente específico. Ele funciona de forma 100% individualizada.

* **Duração:** 10 minutos (configurável em `WHATSAPP_SILENCE_DURATION_MIN` no `.env`).
* **Escopo:** Apenas o chat do cliente em questão. O bot continua ativo para os outros clientes e no chat pessoal do dono.
* **Gatilhos de Ativação:**
  1. **Visualização:** O dono abre/lê a conversa do cliente em qualquer dispositivo conectado (mobile ou web) — detectado via `chats.update` quando o número de não lidas cai para `0` ou `-1`.
  2. **Mensagem Manual:** O dono envia qualquer mensagem manual para o cliente — detectado via `fromMe: true` em mensagens que não foram enviadas pela própria IA (ou seja, não estão em `recentlySentIds`).
* **Restrição de Comandos:** Mensagens enviadas pelo dono que começam com `!` ou são comandos específicos (como `start_bot`/`stop_bot`) são ignoradas pelo gatilho e **não silenciam o chat**.

---

## 🔄 Fluxo de Processamento (Resumo Técnico)

```mermaid
graph TD
    msg[Nova Mensagem] --> checkOwner{Enviada pelo Dono?}
    
    checkOwner -- Sim --> checkPersonal{No chat pessoal/self-chat?}
    checkPersonal -- Sim --> checkCmd{É um comando de controle?}
    checkCmd -- Sim --> execCmd[Executa Comando e Confirma]
    checkCmd -- Não --> runAssistant[Responde com Persona: Assistente Pessoal]
    
    checkPersonal -- Não --> checkCmdClient{É um comando de controle?}
    checkCmdClient -- Sim --> ignoreCmd[Ignora Comando / Não faz nada]
    checkCmdClient -- Não --> silenceChat[Silencia esta conversa por 10 min] --> skipMsg[Ignora mensagem da IA]
    
    checkOwner -- Não --> checkGlobal{Bot pausado globalmente?}
    checkGlobal -- Sim --> dropMsg[Ignora Mensagem]
    checkGlobal -- Não --> checkSilenced{Chat está sob silêncio de 10 min?}
    checkSilenced -- Sim --> dropMsg
    checkSilenced -- Não --> runSupport[Responde com Persona: Suporte ao Cliente]
```
