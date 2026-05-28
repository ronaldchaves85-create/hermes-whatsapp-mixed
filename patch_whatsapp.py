#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de Automatização para Alunos - Hermes Agent (Modo Misto WhatsApp)
Aplica o "Modo Misto Híbrido" (Auto-repostas para clientes e assistente pessoal para o dono)
no WhatsApp do Hermes Agent, além de adicionar os comandos 'stop_bot'/'start_bot' e o filtro genérico de assinaturas.
"""

import os
import sys

def main():
    print("=" * 60)
    print("🤖 INSTALADOR DO MODO MISTO DO WHATSAPP (Hermes Agent) 🤖")
    print("            Desenvolvido para Comunidades de IA")
    print("=" * 60)

    # 1. Definir caminhos possíveis do bridge.js
    possible_paths = [
        os.path.expanduser("~/.hermes/platforms/whatsapp/bridge/bridge.js"),
        "/opt/data/.hermes/platforms/whatsapp/bridge/bridge.js",
        "/opt/hermes/scripts/whatsapp-bridge/bridge.js",
        "/root/.hermes/platforms/whatsapp/bridge/bridge.js",
    ]

    patched_any = False

    for path in possible_paths:
        if not os.path.exists(path):
            continue

        print(f"\n📂 Encontrado arquivo da ponte em: {path}")
        print("⏳ Aplicando patches de segurança e funcionalidade...")

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # ----------------------------------------------------
        # PATCH 1: formatOutgoingMessage (Failsafe Signature Stripper)
        # ----------------------------------------------------
        old_format_1 = """function formatOutgoingMessage(message) {
  // In bot mode, messages come from a different number so the prefix is
  // redundant — the sender identity is already clear.  Only prepend in
  // self-chat mode where bot and user share the same number.
  if (WHATSAPP_MODE !== 'self-chat') return message;
  return REPLY_PREFIX ? `${REPLY_PREFIX}${message}` : message;
}"""

        old_format_2 = """function formatOutgoingMessage(message, chatId) {
  // In bot mode, messages come from a different number so the prefix is
  // redundant — the sender identity is already clear.  Only prepend in
  // self-chat mode where bot and user share the same number.
  if (WHATSAPP_MODE === 'bot') return message;
  if (WHATSAPP_MODE === 'mixed') {
    if (!chatId) return message;
    const myNumber = (sock?.user?.id || '').replace(/:.*@/, '@').replace(/@.*/, '');
    const myLid = (sock?.user?.lid || '').replace(/:.*@/, '@').replace(/@.*/, '');
    const chatNumber = chatId.replace(/@.*/, '');
    const isSelfChat = (myNumber && chatNumber === myNumber) || (myLid && chatNumber === myLid);
    if (!isSelfChat) return message;
  }
  return REPLY_PREFIX ? `${REPLY_PREFIX}${message}` : message;
}"""

        new_format = """function formatOutgoingMessage(message, chatId) {
  // In bot mode, messages come from a different number so the prefix is
  // redundant — the sender identity is already clear.  Only prepend in
  // self-chat mode where bot and user share the same number.
  if (WHATSAPP_MODE === 'bot' || WHATSAPP_MODE === 'mixed') {
    if (!chatId) return message;
    const myNumber = (sock?.user?.id || '').replace(/:.*@/, '@').replace(/@.*/, '');
    const myLid = (sock?.user?.lid || '').replace(/:.*@/, '@').replace(/@.*/, '');
    const chatNumber = chatId.replace(/@.*/, '');
    const isSelfChat = (myNumber && chatNumber === myNumber) || (myLid && chatNumber === myLid);
    if (!isSelfChat) {
      // Failsafe: strip any robotic/email signatures from client WhatsApp messages
      let cleaned = message;
      // Remove e-mails
      cleaned = cleaned.replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/gi, '');
      // Remove common signatures like "Abraços, André" or "Atenciosamente, João" at the end of the message
      cleaned = cleaned.replace(/(Abraços|Abraço|Atenciosamente|Cumprimentos|Abraço forte|Abraços calorosos|Att),?\\s*[A-ZÀ-Ö][a-zà-öø-ÿ]+(\\s+[A-ZÀ-Ö][a-zà-öø-ÿ]+)*\\s*$/gi, '');
      cleaned = cleaned.replace(/\\s+$/, '');
      return cleaned;
    }
    return message;
  }
  return REPLY_PREFIX ? `${REPLY_PREFIX}${message}` : message;
}"""

        if old_format_1 in content:
            content = content.replace(old_format_1, new_format)
            print("  ✓ formatOutgoingMessage atualizado (V1)")
        elif old_format_2 in content:
            content = content.replace(old_format_2, new_format)
            print("  ✓ formatOutgoingMessage atualizado (V2)")
        elif "failsafe signature stripper" in content or "failsafe" in content:
            print("  w formatOutgoingMessage já estava atualizado")
        else:
            print("  ⚠️ formatOutgoingMessage não encontrado exatamente (aviso)")

        # ----------------------------------------------------
        # PATCH 2: calls of formatOutgoingMessage in routes
        # ----------------------------------------------------
        old_send_call = "const chunks = splitLongMessage(formatOutgoingMessage(message));"
        new_send_call = "const chunks = splitLongMessage(formatOutgoingMessage(message, chatId));"
        if old_send_call in content:
            content = content.replace(old_send_call, new_send_call)
            print("  ✓ Chamadas da função nas rotas atualizadas")

        # ----------------------------------------------------
        # PATCH 3: fromMe message handling (allow Self-Chat in bot mode)
        # ----------------------------------------------------
        old_fromme_block = """      // Handle fromMe messages based on mode
      if (msg.key.fromMe) {
        if (isGroup || chatId.includes('status')) continue;

        if (WHATSAPP_MODE === 'bot') {
          // Bot mode: separate number. ALL fromMe are echo-backs of our own replies — skip.
          continue;
        }

        // Self-chat mode: only allow messages in the user's own self-chat
        // WhatsApp now uses LID (Linked Identity Device) format: 67427329167522@lid
        // AND classic format: 34652029134@s.whatsapp.net
        // sock.user has both: { id: "number:10@s.whatsapp.net", lid: "lid_number:10@lid" }
        const myNumber = (sock.user?.id || '').replace(/:.*@/, '@').replace(/@.*/, '');
        const myLid = (sock.user?.lid || '').replace(/:.*@/, '@').replace(/@.*/, '');
        const chatNumber = chatId.replace(/@.*/, '');
        const isSelfChat = (myNumber && chatNumber === myNumber) || (myLid && chatNumber === myLid);
        if (!isSelfChat) continue;
      }"""

        new_fromme_block = """      // Handle fromMe messages based on mode
      if (msg.key.fromMe) {
        if (isGroup || chatId.includes('status')) continue;

        // Self-chat / Bot / Mixed mode: only allow messages in the user's own self-chat.
        // For bot/mixed mode, other fromMe are echo-backs of our own replies to clients — skip.
        const myNumber = (sock.user?.id || '').replace(/:.*@/, '@').replace(/@.*/, '');
        const myLid = (sock.user?.lid || '').replace(/:.*@/, '@').replace(/@.*/, '');
        const chatNumber = chatId.replace(/@.*/, '');
        const isSelfChat = (myNumber && chatNumber === myNumber) || (myLid && chatNumber === myLid);
        if (!isSelfChat) continue;
      }"""

        if old_fromme_block in content:
            content = content.replace(old_fromme_block, new_fromme_block)
            print("  ✓ Bloco 'fromMe' atualizado (Modo Misto)")
        elif new_fromme_block in content or "Self-chat / Bot / Mixed mode" in content:
            print("  w Bloco 'fromMe' já estava atualizado")
        else:
            print("  ⚠️ Bloco 'fromMe' não encontrado exatamente (aviso)")

        # ----------------------------------------------------
        # PATCH 4: stop_bot / start_bot Commands
        # ----------------------------------------------------
        old_fromme_check = """        // Process self-chat commands to pause/resume bot for other clients
        const messageContent = getMessageContent(msg);
        const text = (messageContent?.conversation || messageContent?.extendedTextMessage?.text || '').trim();
        if (text === '!pausar' || text === '!parar') {"""

        new_fromme_check = """        // Process self-chat commands to pause/resume bot for other clients
        const messageContent = getMessageContent(msg);
        const text = (messageContent?.conversation || messageContent?.extendedTextMessage?.text || '').trim();
        const textLower = text.toLowerCase();
        if (textLower === 'stop_bot' || textLower === '!pausar' || textLower === '!parar') {"""

        old_resume_check = """        if (text === '!retomar' || text === '!iniciar') {"""
        new_resume_check = """        if (textLower === 'start_bot' || textLower === '!retomar' || textLower === '!iniciar') {"""

        if old_fromme_check in content:
            content = content.replace(old_fromme_check, new_fromme_check)
            print("  ✓ Comandos stop_bot adicionados")
        elif "textLower ===" in content:
            print("  w Comandos stop_bot já estavam integrados")

        if old_resume_check in content:
            content = content.replace(old_resume_check, new_resume_check)
            print("  ✓ Comandos start_bot adicionados")

        # Salvar as alterações
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        patched_any = True
        print(f"🎉 Patch aplicado com sucesso em: {path}")

    if patched_any:
        print("\n" + "=" * 60)
        print("🔥 PROCEDIMENTO COMPLEMENTAR PARA O SEU ALUNO:")
        print("1. Peça para o aluno rodar no terminal do container:")
        print("   pkill -f bridge.js")
        print("   (Isso força o reinício do robô com as novas regras)")
        print("\n2. Peça para o aluno configurar o arquivo SOUL.md")
        print("   com as instruções de 'Agente de Dupla Personalidade'.")
        print("=" * 60)
    else:
        print("\n❌ Nenhum arquivo bridge.js foi encontrado. Certifique-se de que o WhatsApp está instalado e pareado no Hermes.")

if __name__ == "__main__":
    main()
