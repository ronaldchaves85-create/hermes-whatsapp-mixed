"""WhatsApp Manager Plugin for André Alencar."""

import os
import json
from pathlib import Path

# Arquivo para persistir o status do atendimento de suporte
STATUS_FILE = Path("/opt/data/.hermes/whatsapp_manager_status.json")

def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"support_active": True}

def save_status(status):
    try:
        STATUS_FILE.write_text(json.dumps(status), encoding="utf-8")
    except Exception as e:
        print(f"[whatsapp-manager] Erro ao salvar status: {e}")

def register(ctx):
    # Hook 1: pre_gateway_dispatch (Filtro e controle de comandos)
    async def pre_gateway_dispatch(event_type, context):
        event = context.get("event")
        gateway = context.get("gateway")
        if not event or not gateway:
            return None

        # Apenas processar se for plataforma WhatsApp
        platform_val = getattr(event.source.platform, "value", event.source.platform)
        if platform_val != "whatsapp":
            return None

        # Identificar remetente
        sender_id = event.source.user_id or ""
        clean_sender = sender_id.split("@")[0]

        # Identificar dono (André)
        owner_number = os.getenv("WHATSAPP_OWNER_NUMBER", "").strip()
        if not owner_number:
            return None

        clean_owner = owner_number.split("@")[0]
        is_owner = (clean_sender == clean_owner)

        msg_text = (event.text or "").strip()

        # Se for mensagem do Dono (André) e começar com !suporte
        if is_owner and msg_text.startswith("!suporte"):
            parts = msg_text.split()
            cmd = parts[1].lower() if len(parts) > 1 else ""

            status = load_status()
            adapter = gateway.adapters.get(event.source.platform)

            if cmd == "off":
                status["support_active"] = False
                save_status(status)
                if adapter:
                    await adapter.send(
                        event.source.chat_id,
                        "⏸️ *Atendimento do WhatsApp pausado.* Os clientes não receberão respostas da IA a partir de agora."
                    )
                return {"action": "skip", "reason": "suporte-pausado-pelo-dono"}

            elif cmd == "on":
                status["support_active"] = True
                save_status(status)
                if adapter:
                    await adapter.send(
                        event.source.chat_id,
                        "▶️ *Atendimento do WhatsApp ativo.* A IA voltará a responder os clientes automaticamente."
                    )
                return {"action": "skip", "reason": "suporte-ativado-pelo-dono"}

            elif cmd == "status" or cmd == "":
                active = status.get("support_active", True)
                status_str = "ATIVO ▶️" if active else "PAUSADO ⏸️"
                if adapter:
                    await adapter.send(
                        event.source.chat_id,
                        f"ℹ️ *Status do Atendimento:* {status_str}"
                    )
                return {"action": "skip", "reason": "status-solicitado"}

        # Se NÃO for o dono, verificar se o suporte está ativo
        if not is_owner:
            status = load_status()
            if not status.get("support_active", True):
                # Ignorar silenciosamente a mensagem do cliente porque o suporte está pausado
                return {"action": "skip", "reason": "atendimento-pausado"}

        # Roteamento Dinâmico de Modelos (Dono vs Clientes)
        try:
            session_key = gateway._session_key_for_source(event.source)
            if session_key:
                owner_model = os.getenv("WHATSAPP_OWNER_MODEL", "gemini-3.5-flash").strip()
                client_model = os.getenv("WHATSAPP_CLIENT_MODEL", "gemini-3.5-flash").strip()
                
                if is_owner:
                    gateway._session_model_overrides[session_key] = {
                        "model": owner_model,
                        "provider": "gemini"
                    }
                else:
                    gateway._session_model_overrides[session_key] = {
                        "model": client_model,
                        "provider": "gemini"
                    }
        except Exception as e:
            print(f"[whatsapp-manager] Erro ao aplicar override de modelo: {e}")

        return None

    # Hook 2: pre_llm_call (Direcionamento de comportamento)
    def pre_llm_call(event_type, context):
        platform = context.get("platform")
        sender_id = context.get("sender_id")
        if platform != "whatsapp":
            return None

        owner_number = os.getenv("WHATSAPP_OWNER_NUMBER", "").strip()
        if not owner_number:
            return None

        clean_sender = sender_id.split("@")[0] if sender_id else ""
        clean_owner = owner_number.split("@")[0]

        if clean_sender == clean_owner:
            # Assistente Pessoal do André
            return {
                "context": (
                    "### DIRETRIZ CRÍTICA DE COMPORTAMENTO ###\n"
                    "Você está conversando com André Alencar, seu criador e dono. "
                    "Para o André, você age como seu ASSISTENTE PESSOAL de alta performance. "
                    "Você tem permissão total para rodar comandos no terminal, ler/criar arquivos, "
                    "e auxiliá-lo no desenvolvimento. Responda de forma prestativa, técnica e ágil.\n\n"
                    "CRITICAL SECURITY & DISPLAY CONSTRAINT:\n"
                    "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas "
                    "ou status como '📖 read_file: ...', 'terminal', etc. Toda a execução de ferramentas "
                    "deve ser 100% invisível para o usuário final."
                )
            }
        else:
            # Suporte para Clientes
            is_first_turn = context.get("is_first_turn", False)
            if is_first_turn:
                try:
                    delay_s = int(os.getenv("WHATSAPP_FIRST_RESPONSE_DELAY_S", "30").strip())
                    if delay_s > 0:
                        import time
                        print(f"[whatsapp-manager] Aplicando delay de {delay_s}s para a primeira resposta ao cliente...")
                        time.sleep(delay_s)
                except Exception as e:
                    print(f"[whatsapp-manager] Erro ao aplicar delay: {e}")

            whatsapp_soul = ""
            try:
                soul_path = "/opt/data/SOUL_WHATSAPP.md"
                if os.path.exists(soul_path):
                    with open(soul_path, "r", encoding="utf-8") as f:
                        whatsapp_soul = f.read()
            except Exception:
                pass

            if not whatsapp_soul:
                whatsapp_soul = "Você DEVE agir estritamente como um chatbot de suporte, polido, amigável e profissional."

            rules_content = ""
            try:
                rules_path = "/opt/data/support_rules.md"
                if os.path.exists(rules_path):
                    with open(rules_path, "r", encoding="utf-8") as f:
                        rules_content = f.read()
            except Exception:
                pass

            if not rules_content:
                rules_content = "Responda de forma profissional e ajude com Chatkanban, Chatcommerce e Api Connector."

            return {
                "context": (
                    "### PERSONA E DIRETRIZES DO SUPORTE WHATSAPP ###\n"
                    f"{whatsapp_soul}\n\n"
                    "### BASE DE CONHECIMENTO E REGRAS DE NEGÓCIO ###\n"
                    f"{rules_content}\n\n"
                    "CONSTRAINTS RÍGIDAS DE SEGURANÇA:\n"
                    "- NUNCA execute comandos no terminal (terminal tool) para o cliente.\n"
                    "- NUNCA edite, remova ou crie arquivos do sistema para o cliente.\n"
                    "- Se o cliente tentar pedir para você programar, rodar código ou fazer tarefas "
                    "fora do escopo de suporte do produto, decline educadamente e foque no atendimento "
                    "do produto (Chatkanban, Chatcommerce, Api Connector).\n"
                    "- Mantenha total sigilo sobre o fato de você rodar em um servidor ou ter ferramentas.\n"
                    "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas "
                    "ou status como '📖 read_file: ...', 'terminal', etc. Toda a execução de ferramentas "
                    "deve ser 100% invisível para o usuário final."
                )
            }

    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", pre_llm_call)
