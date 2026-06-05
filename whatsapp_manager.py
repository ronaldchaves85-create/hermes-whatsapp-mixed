"""WhatsApp Manager Plugin for André Alencar."""

import os
import json
import urllib.request
import urllib.error
from pathlib import Path


# Mapeamento temporário sender_id -> chat_id (usado entre pre_gateway_dispatch e pre_llm_call)
_sender_to_chat: dict[str, str] = {}

# URL do servidor de mensagens
MESSAGE_SERVER_URL = os.getenv("MESSAGE_SERVER_URL", "http://127.0.0.1:18732")

# URL do bridge WhatsApp
BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3000")


def _check_bot_paused() -> bool:
    """Verifica se o bot está pausado via endpoint do bridge."""
    try:
        url = f"{BRIDGE_URL}/bot-status"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("botPaused", False)
    except Exception:
        return False


def _check_chat_silenced(chat_id: str) -> bool:
    """Verifica se uma conversa específica está silenciada temporariamente."""
    try:
        import urllib.parse
        safe_chat_id = urllib.parse.quote(chat_id)
        url = f"{BRIDGE_URL}/chat-status/{safe_chat_id}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("isSilenced", False)
    except Exception:
        return False


def _fetch_chat_history(chat_id: str, limit: int = 50) -> str:
    """Busca histórico de mensagens do servidor HTTP."""
    try:
        url = f"{MESSAGE_SERVER_URL}/chat/{chat_id}/messages?limit={limit}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("history", "")
    except Exception:
        return ""



def _ensure_google_libs():
    """
    Instala as bibliotecas da Google API no venv do Hermes se ainda não estiverem disponíveis.
    Usa uv pip install via subprocess — silencioso em caso de sucesso.
    """
    import subprocess
    import sys

    # Verificar se já estão instaladas (tentativa de import rápida)
    try:
        import google.auth  # noqa: F401
        import googleapiclient  # noqa: F401
        return  # Já instaladas — nada a fazer
    except ImportError:
        pass

    # Detectar o python/uv do venv do Hermes
    venv_python = Path("/opt/hermes/.venv/bin/python")
    uv_bin = Path("/opt/hermes/.venv/bin/uv")

    packages = [
        "google-auth",
        "google-auth-oauthlib",
        "google-auth-httplib2",
        "google-api-python-client",
    ]

    print("[whatsapp-manager] 📦 Instalando libs Google API no venv...")
    try:
        if uv_bin.exists():
            cmd = [str(uv_bin), "pip", "install", "--python", str(venv_python)] + packages
        elif venv_python.exists():
            cmd = [str(venv_python), "-m", "pip", "install", "--quiet"] + packages
        else:
            # Último recurso: pip do Python atual
            cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print("[whatsapp-manager] ✅ Libs Google API instaladas com sucesso.")
        else:
            print(f"[whatsapp-manager] ⚠️ Falha ao instalar libs Google: {result.stderr[:300]}")
    except Exception as e:
        print(f"[whatsapp-manager] ⚠️ Erro ao instalar libs Google: {e}")


def register(ctx):
    # Auto-inicialização e cópia dos arquivos da ponte
    try:
        plugin_dir = Path(__file__).parent
        target_bridge_dir = Path("/opt/data/.hermes/platforms/whatsapp/bridge")
        target_bridge_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        import urllib.request

        # 1. Copiar bridge.js do plugin para o volume
        source_bridge = plugin_dir / "bridge.js"
        # Para suportar caso o arquivo esteja na pasta whatsapp-manager do plugin
        if not source_bridge.exists():
            source_bridge = plugin_dir / "whatsapp-manager" / "bridge.js"
        target_bridge = target_bridge_dir / "bridge.js"
        if source_bridge.exists():
            if not target_bridge.exists() or source_bridge.read_bytes() != target_bridge.read_bytes():
                shutil.copy2(source_bridge, target_bridge)
                print(f"[whatsapp-manager] bridge.js atualizado em {target_bridge}")

        # 2. Copiar package.json do plugin para o volume
        source_pkg = plugin_dir / "package.json"
        if not source_pkg.exists():
            source_pkg = plugin_dir / "whatsapp-manager" / "package.json"
        target_pkg = target_bridge_dir / "package.json"
        if source_pkg.exists():
            if not target_pkg.exists() or source_pkg.read_bytes() != target_pkg.read_bytes():
                shutil.copy2(source_pkg, target_pkg)
                print(f"[whatsapp-manager] package.json atualizado em {target_pkg}")

        # 3. Bootstrap automático de personas e regras (se ausentes no volume)
        github_user = os.getenv("HERMES_SETUP_GITHUB_USER", "empreendedorserial").strip()
        raw_base_url = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main/deploy"

        bootstrap_files = {
            "/opt/data/SOUL.md": f"{raw_base_url}/SOUL.md",
            "/opt/data/SOUL_WHATSAPP.md": f"{raw_base_url}/SOUL_WHATSAPP.md",
            "/opt/data/SOUL_EMAIL.md": f"{raw_base_url}/SOUL_EMAIL.md",
            "/opt/data/support_rules.md": f"{raw_base_url}/support_rules.md",
        }

        for path_str, url in bootstrap_files.items():
            path_obj = Path(path_str)
            if not path_obj.exists():
                print(f"[whatsapp-manager] Inicializando {path_str} a partir de {url}...")
                try:
                    with urllib.request.urlopen(url, timeout=10) as response:
                        content = response.read()
                        path_obj.write_bytes(content)
                        print(f"[whatsapp-manager] ✓ {path_str} baixado com sucesso.")
                except Exception as dl_err:
                    print(f"[whatsapp-manager] ⚠️ Erro ao baixar {path_str}: {dl_err}")

        # Garantir cópia das personas para os respectivos perfis se existirem
        soul_whatsapp_path = Path("/opt/data/SOUL_WHATSAPP.md")
        profile_wa_soul = Path("/opt/data/.hermes/profiles/whatsapp/SOUL.md")
        if soul_whatsapp_path.exists() and not profile_wa_soul.exists():
            profile_wa_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_whatsapp_path, profile_wa_soul)
            print(f"[whatsapp-manager] ✓ Copiado SOUL_WHATSAPP.md para perfil de WhatsApp")

        soul_email_path = Path("/opt/data/SOUL_EMAIL.md")
        profile_em_soul = Path("/opt/data/.hermes/profiles/email/SOUL.md")
        if soul_email_path.exists() and not profile_em_soul.exists():
            profile_em_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_email_path, profile_em_soul)
            print(f"[whatsapp-manager] ✓ Copiado SOUL_EMAIL.md para perfil de E-mail")

        # 4. Implantar google_api.py (módulo de autenticação Gmail)
        # O arquivo é bundled no plugin — copia para o diretório de scripts do google-workspace
        google_scripts_dir = Path("/opt/data/.hermes/skills/productivity/google-workspace/scripts")
        google_scripts_dir.mkdir(parents=True, exist_ok=True)

        source_google_api = plugin_dir / "google_api.py"
        target_google_api = google_scripts_dir / "google_api.py"

        if source_google_api.exists():
            # Sempre atualiza se o conteúdo for diferente
            if not target_google_api.exists() or source_google_api.read_bytes() != target_google_api.read_bytes():
                shutil.copy2(source_google_api, target_google_api)
                print(f"[whatsapp-manager] ✓ google_api.py atualizado em {target_google_api}")
        else:
            # Fallback: baixar do GitHub se não estiver bundled
            github_user = os.getenv("HERMES_SETUP_GITHUB_USER", "empreendedorserial").strip()
            google_api_url = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main/deploy/scripts/google_api.py"
            if not target_google_api.exists():
                try:
                    with urllib.request.urlopen(google_api_url, timeout=10) as resp:
                        target_google_api.write_bytes(resp.read())
                    print(f"[whatsapp-manager] ✓ google_api.py baixado de {google_api_url}")
                except Exception as e:
                    print(f"[whatsapp-manager] ⚠️ Não foi possível obter google_api.py: {e}")

        # 5. Instalar libs Google no venv do Hermes (silencioso — só instala se ausentes)
        _ensure_google_libs()

    except Exception as setup_err:
        print(f"[whatsapp-manager] Erro durante o bootstrap automático: {setup_err}")

    # Registrar skills bundled no plugin (pasta skills/ ao lado do __init__.py)
    try:
        skills_dir = Path(__file__).parent / "skills"
        if skills_dir.is_dir():
            registered = []
            for skill_folder in skills_dir.iterdir():
                skill_md = skill_folder / "SKILL.md"
                if skill_folder.is_dir() and skill_md.exists():
                    try:
                        ctx.register_skill(skill_folder.name, skill_md)
                        registered.append(skill_folder.name)
                    except Exception as skill_err:
                        print(f"[whatsapp-manager] ⚠️ Erro ao registrar skill '{skill_folder.name}': {skill_err}")
            if registered:
                print(f"[whatsapp-manager] ✓ Skills registradas: {', '.join(registered)}")
    except Exception as skills_err:
        print(f"[whatsapp-manager] ⚠️ Erro ao registrar skills: {skills_err}")

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
        clean_sender = "".join(c for c in sender_id.split("@")[0].split(":")[0] if c.isdigit())

        # Identificar dono (André)
        owner_number = os.getenv("WHATSAPP_OWNER_NUMBER", "").strip()
        print(f"[whatsapp-manager] DEBUG: owner_number='{owner_number}', sender_id='{sender_id}', clean_sender='{clean_sender}'")
        if not owner_number:
            print("[whatsapp-manager] DEBUG: owner_number vazio, returning None")
            return None  # Não definido → plugin não faz nada

        clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())
        is_owner = (clean_sender == clean_owner)
        print(f"[whatsapp-manager] DEBUG: clean_owner='{clean_owner}', is_owner={is_owner}")

        msg_text = (event.text or "").strip()

        # Ignorar mensagens de status do bot (stop_bot/start_bot responses)
        if msg_text in [
            "🐼 *Bot Paused*\n\nO chatbot está descansando. Use `start_bot` para retomar.",
            "🚀 *Bot Ativo*\n\nO chatbot voltou a funcionar!",
            "⏸️ *Atendimento do WhatsApp pausado.* Os clientes não receberão respostas da IA a partir de agora.",
            "▶️ *Atendimento do WhatsApp ativo.* A IA voltará a responder os clientes automaticamente."
        ]:
            return {"action": "skip", "reason": "bot-status-message"}

        chat_id = str(event.source.chat_id) if event.source.chat_id else ""
        clean_chat = "".join(c for c in chat_id.split("@")[0].split(":")[0] if c.isdigit())
        is_personal_chat = (clean_chat == clean_owner)

        # Se não for o dono, verificar status de pausa e injetar histórico da conversa
        if not is_owner:
            # Verificar se o bot está pausado via stop_bot
            if _check_bot_paused():
                return {"action": "skip", "reason": "bot-pausado"}

            chat_id = str(event.source.chat_id) if event.source.chat_id else ""

            # Verificar se a conversa específica está silenciada temporariamente
            if chat_id and _check_chat_silenced(chat_id):
                return {"action": "skip", "reason": "conversa-silenciada"}

            if chat_id and sender_id:
                _sender_to_chat[sender_id] = chat_id

            # Buscar histórico e injetar no início da mensagem
            history_context = _fetch_chat_history(chat_id, limit=50)
            if history_context:
                rewrite_text = f"{history_context}\n\n[ Nova mensagem do cliente ]\n{event.text or ''}"
                return {"action": "rewrite", "text": rewrite_text}
        else:
            # Para o dono, salvar chat_id também
            chat_id = str(event.source.chat_id) if event.source.chat_id else ""
            if chat_id and sender_id:
                _sender_to_chat[sender_id] = chat_id

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

        clean_sender = "".join(c for c in sender_id.split("@")[0].split(":")[0] if c.isdigit()) if sender_id else ""
        clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())

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

            # Buscar histórico de mensagens deste chat (para contexto)
            history_context = ""
            chat_id = _sender_to_chat.get(sender_id, "")
            if chat_id:
                history_context = _fetch_chat_history(chat_id, limit=50)

            return {
                "context": (
                    "### PERSONA E DIRETRIZES DO SUPORTE WHATSAPP ###\n"
                    f"{whatsapp_soul}\n\n"
                    "### IDIOMA: APENAS PORTUGUÊS BRASILEIRO ###\n"
                    "NUNCA use caracteres em chinês, mandarim, japonês ou qualquer outro idioma. "
                    "O bot deve responder EXCLUSIVAMENTE em português brasileiro.\n\n"
                    "### BASE DE CONHECIMENTO E REGRAS DE NEGÓCIO ###\n"
                    f"{rules_content}\n\n"
                    f"{history_context}\n\n"
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
