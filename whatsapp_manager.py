"""WhatsApp Manager Plugin for André Alencar."""

import os
import json
import urllib.request
import urllib.error
import base64
import time
from pathlib import Path


# Mapeamento temporário sender_id -> chat_id (usado entre pre_gateway_dispatch e pre_llm_call)
_sender_to_chat: dict[str, str] = {}

# URL do servidor de mensagens
MESSAGE_SERVER_URL = os.getenv("MESSAGE_SERVER_URL", "http://127.0.0.1:18732")

# URL do bridge WhatsApp
BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3000")


def _normalize_brazilian_phone(phone: str) -> str:
    """Normaliza números de telefone brasileiros para comparação segura (tratando o dígito 9 extra)."""
    clean = "".join(c for c in phone if c.isdigit())
    if clean.startswith("55") and len(clean) >= 11:
        ddd = clean[2:4]
        rest = clean[4:]
        if len(rest) == 9 and rest.startswith("9"):
            clean = f"55{ddd}{rest[1:]}"
    return clean


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


def _classify_contact_via_llm(name: str, chat_history: str, stats_info: str) -> dict:
    """Classifica contatos usando a API do LLM (Gemini, OpenAI ou OpenRouter) com base no histórico e estatísticas."""
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    prompt = (
        "You are a classification assistant for a WhatsApp bot.\n"
        "The owner of the WhatsApp account is named André Alencar.\n"
        f"Your task is to analyze the recent conversation history and statistics between André and a contact named '{name or 'Unknown'}' "
        "to classify their relationship, tone, nickname, pet names (terms of endearment), frequent greetings, "
        "conversation summary, the intent of their latest interactions, the frequency of their conversations, "
        "and specific guidelines for the bot when responding to them.\n\n"
        f"Conversation Statistics:\n{stats_info}\n\n"
        "Recent Chat history:\n"
        f"{chat_history or '(No history available)'}\n\n"
        "Classify into one of the following profiles:\n"
        "1. \"amigo/namorada\":\n"
        "   - Use this if they are friends, a girlfriend, close romantic contacts, or talk very informally/intimately.\n"
        "   - Recommended tone: \"informal e amigável\" or \"informal e carinhoso\".\n"
        "2. \"família\":\n"
        "   - Use this if they are a family member (mother, father, sibling, etc.).\n"
        "   - Recommended tone: \"informal e amigável\".\n"
        "3. \"cliente/contato\":\n"
        "   - Use this if they are a customer, client, business contact, lead, or inquiring about systems, API, support, development, or price.\n"
        "   - Recommended tone: \"polido e profissional\".\n\n"
        "Extract/determine the following details:\n"
        "- \"nickname\" (apelido): Any nickname used by André to refer to this contact (e.g. \"Bru\", \"Carlos\", etc.). null if none.\n"
        "- \"pet_name\" (nome carinhoso): Terms of endearment used by André (e.g. \"amor\", \"vida\", \"querida\", etc.). null if none.\n"
        "- \"frequent_greeting\" (saudação frequente): The typical greeting phrase used when starting a conversation (e.g. \"Eae mano\", \"Oi amor\", \"Olá\", etc.). null if none.\n"
        "- \"summary\" (resumo): A brief summary of what they usually talk about (in Portuguese).\n"
        "- \"intent\" (intenção): The main intent or topic of their latest messages (in Portuguese).\n"
        "- \"frequency\" (frequência): The frequency of their conversations (e.g. \"diária\", \"semanal\", \"mensal\", \"esporádica\") based on the statistics and history.\n\n"
        "Return a JSON object with this exact structure (do NOT wrap it in markdown code blocks like ```json, just raw JSON):\n"
        "{\n"
        "  \"relationship\": \"amigo/namorada\" | \"família\" | \"cliente/contato\",\n"
        "  \"tone\": \"informal e carinhoso\" | \"informal e amigável\" | \"polido e profissional\" | \"técnico e direto\",\n"
        "  \"nickname\": string | null,\n"
        "  \"pet_name\": string | null,\n"
        "  \"frequent_greeting\": string | null,\n"
        "  \"summary\": string,\n"
        "  \"intent\": string,\n"
        "  \"frequency\": string,\n"
        "  \"guidelines\": \"...\"\n"
        "}"
    )

    # 1. Tentar Gemini API
    if google_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={google_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text_content = result["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text_content.strip())
        except Exception as e:
            print(f"[whatsapp-manager] Falha ao classificar via Gemini: {e}")

    # 2. Tentar OpenAI API
    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {openai_key}"
            }
            payload = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text_content = result["choices"][0]["message"]["content"]
                return json.loads(text_content.strip())
        except Exception as e:
            print(f"[whatsapp-manager] Falha ao classificar via OpenAI: {e}")

    # 3. Tentar OpenRouter API
    if openrouter_key:
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {openrouter_key}"
            }
            payload = {
                "model": "google/gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text_content = result["choices"][0]["message"]["content"]
                return json.loads(text_content.strip())
        except Exception as e:
            print(f"[whatsapp-manager] Falha ao classificar via OpenRouter: {e}")

    # Fallback default se nenhuma API key estiver disponível ou todas falharem
    return {
        "relationship": "cliente/contato",
        "tone": "polido e profissional",
        "nickname": None,
        "pet_name": None,
        "frequent_greeting": None,
        "summary": "Conversa inicial de suporte/atendimento.",
        "intent": "Obter ajuda ou informações sobre os sistemas do André.",
        "frequency": "esporádica",
        "guidelines": "Responda de forma prestativa."
    }


def _sync_contacts_from_db_internal(force: bool = True) -> str:
    """Sincroniza contatos do SQLite local para personal_contacts.json e envia para o GitHub."""
    import sqlite3
    import datetime
    from pathlib import Path
    base_dir = Path("/opt/data/.hermes")
    db_path = base_dir / "whatsapp_messages.db"
    pc_path = Path("/opt/data/personal_contacts.json")

    # 1. Carregar arquivo JSON local existente
    personal_contacts = {}
    if pc_path.exists():
        try:
            with open(pc_path, "r", encoding="utf-8") as f:
                personal_contacts = json.load(f)
        except Exception as e:
            print(f"[whatsapp-manager] Erro ao ler {pc_path}: {e}")

    # 2. Ler contatos únicos do SQLite com agregação de estatísticas para performance
    if not db_path.exists():
        return "Erro: Banco de dados SQLite whatsapp_messages.db não encontrado."

    db_contacts = {}
    classification_count = 0
    max_classifications = int(os.getenv("WHATSAPP_SYNC_MAX_CLASSIFICATIONS", "40").strip())
    min_msg_threshold = int(os.getenv("WHATSAPP_SYNC_MIN_MESSAGES", "3").strip())
    skipped_few_msgs = 0
    hit_limit = False

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, MAX(sender_name) as name, COUNT(*) as msg_count, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts
            FROM messages
            WHERE chat_id NOT LIKE '%@g.us%' AND chat_id IS NOT NULL
            GROUP BY chat_id
            ORDER BY max_ts DESC
        """)
        rows = cursor.fetchall()
        for chat_id, name, msg_count, min_ts, max_ts in rows:
            if chat_id:
                phone = chat_id.split("@")[0]
                
                # Verificar se já existe por JID ou por número
                exists = False
                existing_key = None
                for key in list(personal_contacts.keys()):
                    if key == chat_id or key == phone:
                        exists = True
                        existing_key = key
                        break
                
                # Decidir se precisa de atualização (novo contato ou contato existente sem os novos campos)
                needs_update = not exists
                is_stale = False
                if exists and existing_key:
                    existing_data = personal_contacts[existing_key]
                    old_defaults = ["Conversa inicial.", "Conversa muito curta.", "Conversa inicial de suporte/atendimento.", "Conversa inicial."]
                    has_old_default_summary = existing_data.get("summary") in old_defaults
                    
                    if force or has_old_default_summary or not existing_data.get("summary") or not existing_data.get("intent") or not existing_data.get("frequency"):
                        needs_update = True
                        is_stale = True
                
                if needs_update:
                    if msg_count < min_msg_threshold:
                        # Criar fallback direto sem gastar chamada de IA para conversas com pouquíssimas mensagens
                        skipped_few_msgs += 1
                        target_key = existing_key if existing_key else chat_id
                        existing_data = personal_contacts.get(target_key, {})
                        
                        # Se for stale (antigo e incompleto), não reaproveitamos as propriedades padrão antigas
                        rel_val = "cliente/contato" if is_stale else (existing_data.get("relationship") or "cliente/contato")
                        tone_val = "polido e profissional" if is_stale else (existing_data.get("tone") or "polido e profissional")
                        guide_val = "Responda de forma prestativa." if is_stale else (existing_data.get("guidelines") or "Responda de forma prestativa.")
                        
                        personal_contacts[target_key] = {
                            "name": existing_data.get("name") or name or f"Contato {phone}",
                            "relationship": rel_val,
                            "tone": tone_val,
                            "nickname": existing_data.get("nickname"),
                            "pet_name": existing_data.get("pet_name"),
                            "frequent_greeting": existing_data.get("frequent_greeting"),
                            "summary": existing_data.get("summary") or "Conversa muito curta.",
                            "intent": existing_data.get("intent") or "Contato inicial.",
                            "frequency": existing_data.get("frequency") or "esporádica",
                            "guidelines": guide_val
                        }
                        continue

                    if classification_count >= max_classifications:
                        hit_limit = True
                        continue

                    # Estatísticas formatadas
                    stats_info = f"Total messages: {msg_count}."
                    if min_ts and max_ts:
                        try:
                            first_date = datetime.datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d')
                            last_date = datetime.datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d')
                            stats_info += f" First message date: {first_date}. Last message date: {last_date}."
                        except Exception:
                            pass
                    
                    # Buscar as últimas 15 mensagens da conversa
                    try:
                        cursor.execute("""
                            SELECT from_me, sender_name, body FROM messages
                            WHERE chat_id = ? AND body IS NOT NULL AND body != ''
                            ORDER BY timestamp DESC LIMIT 15
                        """, (chat_id,))
                        rows_msgs = cursor.fetchall()
                        rows_msgs.reverse()
                        
                        history_lines = []
                        for f_me, s_name, msg_body in rows_msgs:
                            sender_lbl = "André" if f_me else (s_name or name or "Contato")
                            history_lines.append(f"[{sender_lbl}]: {msg_body}")
                        chat_history = "\n".join(history_lines)
                    except Exception as db_err:
                        print(f"[whatsapp-manager] Erro ao ler histórico para {chat_id}: {db_err}")
                        chat_history = ""
                    
                    db_contacts[chat_id] = {
                        "name": name,
                        "history": chat_history,
                        "stats": stats_info,
                        "existing_key": existing_key,
                        "is_stale": is_stale
                    }
                    classification_count += 1
        conn.close()
    except Exception as e:
        return f"Erro ao ler banco de dados SQLite: {e}"

    # 3. Mesclar dados mantendo os já existentes com classificação inteligente via LLM
    updated = False
    added_count = 0
    for chat_id, info in db_contacts.items():
        name = info["name"]
        chat_history = info["history"]
        stats_info = info["stats"]
        existing_key = info["existing_key"]
        is_stale = info.get("is_stale", False)
        phone = chat_id.split("@")[0]
        
        # Classificação baseada no nome, estatísticas e histórico de conversas via LLM
        classification = _classify_contact_via_llm(name, chat_history, stats_info)
        
        target_key = existing_key if existing_key else chat_id
        existing_data = personal_contacts.get(target_key, {})
        
        # Se o contato era "stale" (tinha classificação rule-based ou incompleta), sobrescrevemos
        # as propriedades originais com a classificação precisa da IA, mas mantendo o nome manual
        if is_stale:
            personal_contacts[target_key] = {
                "name": existing_data.get("name") or name or f"Contato {phone}",
                "relationship": classification.get("relationship", "cliente/contato"),
                "tone": classification.get("tone", "polido e profissional"),
                "nickname": classification.get("nickname"),
                "pet_name": classification.get("pet_name"),
                "frequent_greeting": classification.get("frequent_greeting"),
                "summary": classification.get("summary", "Conversa inicial."),
                "intent": classification.get("intent", "Suporte/Atendimento."),
                "frequency": classification.get("frequency", "esporádica"),
                "guidelines": classification.get("guidelines", "Responda de forma prestativa.")
            }
        else:
            personal_contacts[target_key] = {
                "name": existing_data.get("name") or name or f"Contato {phone}",
                "relationship": existing_data.get("relationship") or classification.get("relationship", "cliente/contato"),
                "tone": existing_data.get("tone") or classification.get("tone", "polido e profissional"),
                "nickname": existing_data.get("nickname") or classification.get("nickname"),
                "pet_name": existing_data.get("pet_name") or classification.get("pet_name"),
                "frequent_greeting": existing_data.get("frequent_greeting") or classification.get("frequent_greeting"),
                "summary": existing_data.get("summary") or classification.get("summary", "Conversa inicial."),
                "intent": existing_data.get("intent") or classification.get("intent", "Suporte/Atendimento."),
                "frequency": existing_data.get("frequency") or classification.get("frequency", "esporádica"),
                "guidelines": existing_data.get("guidelines") or classification.get("guidelines", "Responda de forma prestativa.")
            }
        added_count += 1
        updated = True

    # Preparar mensagem de resultado
    result_messages = []
    if updated or skipped_few_msgs > 0:
        # Salvar JSON localmente
        try:
            with open(pc_path, "w", encoding="utf-8") as f:
                json.dump(personal_contacts, f, indent=2, ensure_ascii=False)
            
            result_messages.append(f"Sucesso! Mapeados e mesclados {added_count + skipped_few_msgs} contatos localmente.")
            if added_count > 0:
                result_messages.append(f"- {added_count} contatos classificados via IA.")
            if skipped_few_msgs > 0:
                result_messages.append(f"- {skipped_few_msgs} contatos curtos configurados com valores padrão.")
            if hit_limit:
                result_messages.append(f"⚠️ Limite de {max_classifications} chamadas de IA atingido nesta execução. Os contatos restantes serão atualizados nas próximas execuções ou interações.")
            result_str = "\n".join(result_messages)
        except Exception as e:
            return f"Erro ao salvar personal_contacts.json localmente: {e}"
    else:
        result_str = "Nenhum contato novo ou pendente encontrado para adicionar."

    # 4. Sincronizar com GitHub
    config_repo = os.getenv("CONFIG_REPO", "").strip()
    config_token = os.getenv("CONFIG_GITHUB_TOKEN", "").strip()
    setup_user = os.getenv("HERMES_SETUP_GITHUB_USER", "").strip()

    if config_repo and config_token:
        if "/" in config_repo:
            repo_parts = config_repo.split("/")
            repo_user = repo_parts[0]
            repo_name = repo_parts[1]
        else:
            repo_user = setup_user or "empreendedorserial"
            repo_name = config_repo

        try:
            with open(pc_path, "rb") as f:
                content = f.read()
            content_b64 = base64.b64encode(content).decode("utf-8")
            
            # Buscar SHA atual do arquivo no GitHub para evitar conflito
            get_url = f"https://api.github.com/repos/{repo_user}/{repo_name}/contents/personal_contacts.json"
            req_get = urllib.request.Request(get_url)
            req_get.add_header("Authorization", f"token {config_token}")
            req_get.add_header("Accept", "application/vnd.github+json")
            req_get.add_header("User-Agent", "Hermes-Agent-Plugin")
            
            sha = None
            try:
                with urllib.request.urlopen(req_get, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    sha = data.get("sha")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    print(f"[whatsapp-manager] Erro ao buscar SHA: {e}")
            
            # Atualizar conteúdo
            put_data = {
                "message": "Update personal_contacts.json from WhatsApp database history",
                "content": content_b64,
                "branch": "main"
            }
            if sha:
                put_data["sha"] = sha
                
            req_put = urllib.request.Request(get_url, data=json.dumps(put_data).encode("utf-8"), method="PUT")
            req_put.add_header("Authorization", f"token {config_token}")
            req_put.add_header("Accept", "application/vnd.github+json")
            req_put.add_header("User-Agent", "Hermes-Agent-Plugin")
            req_put.add_header("Content-Type", "application/json")
            
            with urllib.request.urlopen(req_put, timeout=10) as resp:
                if resp.status in [200, 201]:
                    result_str += "\n✓ personal_contacts.json sincronizado com o GitHub com sucesso!"
        except Exception as e:
            result_str += f"\n⚠️ Falha ao sincronizar com GitHub: {e}"
    else:
        result_str += "\nℹ️ GitHub não configurado na stack, sincronizado apenas localmente."

    return result_str



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


def _pull_and_merge_configurations():
    """Baixa as configurações do repositório privado do GitHub do cliente e faz merge com o local."""
    config_repo = os.getenv("CONFIG_REPO", "").strip()
    config_token = os.getenv("CONFIG_GITHUB_TOKEN", "").strip()
    setup_user = os.getenv("HERMES_SETUP_GITHUB_USER", "").strip()
    dev_user = os.getenv("DEV_GITHUB_USER", "").strip()

    if not config_repo:
        config_repo = "hermes_agent_context_contatcs"

    if "/" in config_repo:
        repo_parts = config_repo.split("/")
        repo_user = repo_parts[0]
        repo_name = repo_parts[1]
    else:
        repo_user = setup_user or dev_user or "empreendedorserial"
        repo_name = config_repo

    config_base_url = f"https://raw.githubusercontent.com/{repo_user}/{repo_name}/main"

    # 1. Sincronizar SOUL.md, SOUL_WHATSAPP.md, SOUL_EMAIL.md e support_rules.md
    bootstrap_files = {
        "/opt/data/SOUL.md": f"{config_base_url}/SOUL.md",
        "/opt/data/SOUL_WHATSAPP.md": f"{config_base_url}/SOUL_WHATSAPP.md",
        "/opt/data/SOUL_EMAIL.md": f"{config_base_url}/SOUL_EMAIL.md",
        "/opt/data/support_rules.md": f"{config_base_url}/support_rules.md",
    }

    for path_str, url in bootstrap_files.items():
        path_obj = Path(path_str)
        try:
            req = urllib.request.Request(url)
            if config_token:
                req.add_header("Authorization", f"token {config_token}")
            req.add_header("User-Agent", "Hermes-Agent-Plugin")
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                if content:
                    path_obj.parent.mkdir(parents=True, exist_ok=True)
                    path_obj.write_bytes(content)
                    print(f"[whatsapp-manager] ✓ {path_str} atualizado do GitHub.")
        except Exception as e:
            print(f"[whatsapp-manager] ⚠️ Falha ao baixar {path_str} de {url}: {e}")

    # Copiar personas para perfis locais correspondentes
    try:
        import shutil
        soul_whatsapp_path = Path("/opt/data/SOUL_WHATSAPP.md")
        profile_wa_soul = Path("/opt/data/.hermes/profiles/whatsapp/SOUL.md")
        if soul_whatsapp_path.exists():
            profile_wa_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_whatsapp_path, profile_wa_soul)

        soul_email_path = Path("/opt/data/SOUL_EMAIL.md")
        profile_em_soul = Path("/opt/data/.hermes/profiles/email/SOUL.md")
        if soul_email_path.exists():
            profile_em_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_email_path, profile_em_soul)
    except Exception as copy_err:
        print(f"[whatsapp-manager] ⚠️ Falha ao copiar personas para perfis locais: {copy_err}")

    # 2. Sincronizar personal_contacts.json (merge)
    personal_contacts_path = Path("/opt/data/personal_contacts.json")
    local_contacts = {}
    if personal_contacts_path.exists():
        try:
            with open(personal_contacts_path, "r", encoding="utf-8") as f:
                local_contacts = json.load(f)
        except Exception as e:
            print(f"[whatsapp-manager] Erro ao carregar local personal_contacts.json: {e}")

    remote_url = f"{config_base_url}/personal_contacts.json"
    remote_contacts = None
    try:
        req = urllib.request.Request(remote_url)
        if config_token:
            req.add_header("Authorization", f"token {config_token}")
        req.add_header("User-Agent", "Hermes-Agent-Plugin")
        with urllib.request.urlopen(req, timeout=10) as response:
            remote_contacts = json.loads(response.read().decode("utf-8"))
            print(f"[whatsapp-manager] ✓ personal_contacts.json remoto carregado com sucesso.")
    except Exception as e:
        print(f"[whatsapp-manager] ⚠️ Não foi possível baixar personal_contacts.json do GitHub: {e}")

    if remote_contacts is not None:
        # Mesclar local e remoto
        merged = {}
        # Priorizar remoto
        merged.update(remote_contacts)
        # Manter locais novos
        for k, v in local_contacts.items():
            if k not in merged:
                merged[k] = v
        
        try:
            with open(personal_contacts_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            print(f"[whatsapp-manager] ✓ Contatos mesclados localmente.")
        except Exception as e:
            print(f"[whatsapp-manager] Erro ao salvar personal_contacts.json mesclado: {e}")


def _self_update_plugin_code() -> bool:
    """Atualiza o código do plugin a partir do repositório Git. Retorna True se houve mudanças no próprio plugin."""
    github_user = (os.getenv("HERMES_SETUP_GITHUB_USER") or os.getenv("DEV_GITHUB_USER") or "empreendedorserial").strip()
    code_token = os.getenv("DEV_GITHUB_TOKEN", "").strip()

    raw_root = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main"
    plugin_dir = Path("/opt/data/.hermes/plugins/whatsapp-manager")
    if not plugin_dir.exists():
        plugin_dir = Path(__file__).parent

    files_to_update = {
        "plugin.yaml": f"{raw_root}/plugin.yaml",
        "__init__.py": f"{raw_root}/__init__.py",
        "whatsapp_manager.py": f"{raw_root}/whatsapp_manager.py",
        "bridge.js": f"{raw_root}/bridge.js",
        "package.json": f"{raw_root}/package.json",
        "google_api.py": f"{raw_root}/google_api.py",
    }

    skills_to_update = {
        "skills/google-oauth/SKILL.md": f"{raw_root}/skills/google-oauth/SKILL.md",
        "skills/research-sources/SKILL.md": f"{raw_root}/skills/research-sources/SKILL.md",
        "skills/whatsapp-logs-diagnostics/SKILL.md": f"{raw_root}/skills/whatsapp-logs-diagnostics/SKILL.md",
    }

    updated_any = False
    
    # Atualizar arquivos principais
    for filename, url in files_to_update.items():
        local_path = plugin_dir / filename
        try:
            req = urllib.request.Request(url)
            if code_token:
                req.add_header("Authorization", f"token {code_token}")
            req.add_header("User-Agent", "Hermes-Agent-Plugin")
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read()
                if content:
                    if not local_path.exists() or local_path.read_bytes() != content:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        local_path.write_bytes(content)
                        print(f"[whatsapp-manager] Code Update: {filename} atualizado com sucesso.")
                        if filename in ["whatsapp_manager.py", "bridge.js"]:
                            updated_any = True
        except Exception as e:
            print(f"[whatsapp-manager] Code Update: Falha ao atualizar {filename}: {e}")

    # Atualizar skills
    for relative_path, url in skills_to_update.items():
        local_path = plugin_dir / relative_path
        try:
            req = urllib.request.Request(url)
            if code_token:
                req.add_header("Authorization", f"token {code_token}")
            req.add_header("User-Agent", "Hermes-Agent-Plugin")
            with urllib.request.urlopen(req, timeout=15) as response:
                content = response.read()
                if content:
                    if not local_path.exists() or local_path.read_bytes() != content:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        local_path.write_bytes(content)
                        print(f"[whatsapp-manager] Code Update: {relative_path} atualizado.")
        except Exception as e:
            print(f"[whatsapp-manager] Code Update: Falha ao atualizar skill {relative_path}: {e}")

    return updated_any


def register(ctx):
    # Auto-inicialização e cópia dos arquivos da ponte
    try:
        plugin_dir = Path(__file__).parent
        target_bridge_dir = Path("/opt/data/.hermes/platforms/whatsapp/bridge")
        target_bridge_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        import urllib.request

        # Garantir link de compatibilidade para evitar path mismatch da sessão (whatsapp/session vs platforms/whatsapp/session)
        old_session = Path("/opt/data/.hermes/whatsapp/session")
        new_session = Path("/opt/data/.hermes/platforms/whatsapp/session")
        new_session.mkdir(parents=True, exist_ok=True)
        old_session.parent.mkdir(parents=True, exist_ok=True)
        if old_session.exists() and not old_session.is_symlink():
            print("[whatsapp-manager] 🔄 Migrando sessão antiga para o novo caminho...")
            for f in old_session.iterdir():
                if f.is_file():
                    try:
                        shutil.copy2(f, new_session / f.name)
                    except Exception as cp_err:
                        print(f"[whatsapp-manager] ⚠️ Erro ao copiar {f.name}: {cp_err}")
            shutil.rmtree(old_session, ignore_errors=True)
        if not old_session.exists():
            try:
                old_session.symlink_to(new_session, target_is_directory=True)
                print("[whatsapp-manager] ✅ Link de compatibilidade da sessão criado.")
            except Exception as link_err:
                print(f"[whatsapp-manager] ⚠️ Erro ao criar link simbólico da sessão: {link_err}")

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

        # Auto-criação do repositório privado se necessário (Executado no boot de forma 100% transparente)
        try:
            config_repo = os.getenv("CONFIG_REPO", "").strip()
            config_token = os.getenv("CONFIG_GITHUB_TOKEN", "").strip()
            setup_user = os.getenv("HERMES_SETUP_GITHUB_USER", "").strip()

            if config_repo and config_token:
                # Local imports removed to avoid scope issues

                if "/" in config_repo:
                    repo_parts = config_repo.split("/")
                    repo_user = repo_parts[0]
                    repo_name = repo_parts[1]
                else:
                    repo_user = setup_user or "empreendedorserial"
                    repo_name = config_repo

                repo_url = f"https://api.github.com/repos/{repo_user}/{repo_name}"
                req = urllib.request.Request(repo_url)
                req.add_header("Authorization", f"token {config_token}")
                req.add_header("Accept", "application/vnd.github+json")
                req.add_header("User-Agent", "Hermes-Agent-Plugin")

                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status == 200:
                            print(f"[whatsapp-manager] ✓ Repositório privado '{repo_user}/{repo_name}' já existe no GitHub.")
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        print(f"[whatsapp-manager] ⚠️ Repositório '{repo_user}/{repo_name}' não existe. Tentando criar automaticamente...")
                        create_url = "https://api.github.com/user/repos"
                        create_data = json.dumps({
                            "name": repo_name,
                            "private": True,
                            "description": "Hermes Configuration Repository",
                            "auto_init": True
                        }).encode("utf-8")

                        create_req = urllib.request.Request(create_url, data=create_data, method="POST")
                        create_req.add_header("Authorization", f"token {config_token}")
                        create_req.add_header("Accept", "application/vnd.github+json")
                        create_req.add_header("User-Agent", "Hermes-Agent-Plugin")
                        create_req.add_header("Content-Type", "application/json")

                        try:
                            with urllib.request.urlopen(create_req, timeout=10) as create_resp:
                                if create_resp.status in [200, 201]:
                                    print(f"[whatsapp-manager] ✓ Repositório privado '{repo_user}/{repo_name}' criado com sucesso no GitHub!")
                                    time.sleep(3) # Aguarda o GitHub provisionar o branch main

                                    # Função auxiliar para commitar via API
                                    def commit_file_to_repo(local_path, github_path, default_url):
                                        content = b""
                                        if os.path.exists(local_path):
                                            try:
                                                with open(local_path, "rb") as f:
                                                    content = f.read()
                                            except Exception:
                                                pass
                                        if not content and default_url:
                                            try:
                                                with urllib.request.urlopen(default_url, timeout=10) as r:
                                                    content = r.read()
                                            except Exception as dl_err:
                                                print(f"[whatsapp-manager] Erro ao baixar template {github_path}: {dl_err}")

                                        if content:
                                            content_b64 = base64.b64encode(content).decode("utf-8")
                                            put_url = f"https://api.github.com/repos/{repo_user}/{repo_name}/contents/{github_path}"
                                            put_data = json.dumps({
                                                "message": f"Add initial {github_path}",
                                                "content": content_b64,
                                                "branch": "main"
                                            }).encode("utf-8")

                                            put_req = urllib.request.Request(put_url, data=put_data, method="PUT")
                                            put_req.add_header("Authorization", f"token {config_token}")
                                            put_req.add_header("Accept", "application/vnd.github+json")
                                            put_req.add_header("User-Agent", "Hermes-Agent-Plugin")
                                            put_req.add_header("Content-Type", "application/json")

                                            try:
                                                with urllib.request.urlopen(put_req, timeout=10) as put_resp:
                                                    if put_resp.status in [200, 201]:
                                                        print(f"[whatsapp-manager] ✓ Arquivo '{github_path}' inicializado no repositório.")
                                            except Exception as put_err:
                                                print(f"[whatsapp-manager] Erro ao commitar {github_path}: {put_err}")

                                    raw_base = "https://raw.githubusercontent.com/empreendedorserial/hermes-whatsapp-mixed/main/deploy"
                                    commit_file_to_repo("/opt/data/SOUL.md", "SOUL.md", f"{raw_base}/SOUL.md")
                                    commit_file_to_repo("/opt/data/SOUL_WHATSAPP.md", "SOUL_WHATSAPP.md", f"{raw_base}/SOUL_WHATSAPP.md")
                                    commit_file_to_repo("/opt/data/SOUL_EMAIL.md", "SOUL_EMAIL.md", f"{raw_base}/SOUL_EMAIL.md")
                                    commit_file_to_repo("/opt/data/support_rules.md", "support_rules.md", f"{raw_base}/support_rules.md")
                                    commit_file_to_repo("/opt/data/personal_contacts.json", "personal_contacts.json", f"{raw_base}/personal_contacts.json.example")
                        except Exception as create_err:
                            print(f"[whatsapp-manager] ⚠️ Erro ao criar repositório: {create_err}")
                except Exception as check_err:
                    print(f"[whatsapp-manager] ⚠️ Erro ao verificar repositório no GitHub: {check_err}")
        except Exception as repo_err:
            print(f"[whatsapp-manager] ⚠️ Erro no processo automático de configuração de repositório: {repo_err}")

        # 3. Bootstrap automático de personas e regras (se ausentes no volume)
        github_user = (os.getenv("HERMES_SETUP_GITHUB_USER") or os.getenv("DEV_GITHUB_USER") or "empreendedorserial").strip()
        raw_base_url = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main/deploy"

        personal_contacts_path = Path("/opt/data/personal_contacts.json")
        if not personal_contacts_path.exists():
            print("[whatsapp-manager] Inicializando personal_contacts.json...")
            try:
                personal_contacts_path.write_text("{}", encoding="utf-8")
                print("[whatsapp-manager] ✓ personal_contacts.json criado.")
            except Exception as pc_err:
                print(f"[whatsapp-manager] ⚠️ Erro ao inicializar personal_contacts.json: {pc_err}")

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
            github_user = (os.getenv("HERMES_SETUP_GITHUB_USER") or os.getenv("DEV_GITHUB_USER") or "empreendedorserial").strip()
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
    def pre_gateway_dispatch(*args, **kwargs):
        context = kwargs.get("context")
        if not context:
            for arg in args:
                if isinstance(arg, dict):
                    context = arg
                    break
        
        event = None
        gateway = None
        if context:
            event = context.get("event")
            gateway = context.get("gateway")
            
        if not event:
            event = kwargs.get("event")
            
        if not gateway:
            gateway = kwargs.get("gateway")
            
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
        is_owner = (_normalize_brazilian_phone(clean_sender) == _normalize_brazilian_phone(clean_owner))
        print(f"[whatsapp-manager] DEBUG: clean_owner='{clean_owner}', is_owner={is_owner}")

        msg_text = (event.text or "").strip()

        # Comando para sincronizar e importar contatos do SQLite para personal_contacts.json e GitHub
        normalized_msg = msg_text.strip().lower().replace("_", " ").replace("-", " ")
        try:
            with open("/opt/data/whatsapp_manager_debug.log", "a", encoding="utf-8") as debug_f:
                import time
                debug_f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] sender='{sender_id}' (clean='{clean_sender}', norm='{_normalize_brazilian_phone(clean_sender)}') owner='{owner_number}' (clean='{clean_owner}', norm='{_normalize_brazilian_phone(clean_owner)}') is_owner={is_owner} msg='{msg_text}' normalized='{normalized_msg}'\n")
        except Exception as log_e:
            print(f"[whatsapp-manager] Erro ao gravar debug log: {log_e}")
        sync_commands = [
            "sync contacts", "sync_contacts",
            "importar contatos", "importar_contatos",
            "sync contatos", "sync_contatos",
            "sincronizar contatos", "sincronizar_contatos"
        ]
        
        is_sync_cmd = False
        for cmd in sync_commands:
            cmd_norm = cmd.replace("_", " ").replace("-", " ")
            if normalized_msg.startswith(cmd_norm):
                is_sync_cmd = True
                break

        if is_owner and is_sync_cmd:
            print("[whatsapp-manager] Comando de sincronização detectado (forçando atualização).")
            chat_id = str(event.source.chat_id) if event.source.chat_id else ""
            
            try:
                result_info = _sync_contacts_from_db_internal(force=True)
                response_msg = (
                    "👤 *Sincronização de Contatos*\n\n"
                    f"{result_info}"
                )
            except Exception as e:
                response_msg = f"❌ Erro na sincronização interna: {e}"
            
            # Enviar de volta
            if chat_id:
                try:
                    url = f"{BRIDGE_URL}/send"
                    payload = json.dumps({
                        "chatId": chat_id,
                        "text": response_msg
                    }).encode("utf-8")
                    req = urllib.request.Request(url, data=payload, method="POST")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        pass
                except Exception as send_err:
                    print(f"[whatsapp-manager] Erro ao enviar resposta do comando: {send_err}")
            
            return {"action": "skip", "reason": "sync-contacts-command"}

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
    def pre_llm_call(*args, **kwargs):
        context = kwargs.get("context")
        if not context:
            for arg in args:
                if isinstance(arg, dict):
                    context = arg
                    break
        
        platform = None
        sender_id = None
        if context:
            platform = context.get("platform")
            sender_id = context.get("sender_id")
            
        if not platform:
            platform = kwargs.get("platform")
            
        if not sender_id:
            sender_id = kwargs.get("sender_id")
            
        if platform != "whatsapp":
            return None

        owner_number = os.getenv("WHATSAPP_OWNER_NUMBER", "").strip()
        if not owner_number:
            return None

        clean_sender = "".join(c for c in sender_id.split("@")[0].split(":")[0] if c.isdigit()) if sender_id else ""
        clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())

        if _normalize_brazilian_phone(clean_sender) == _normalize_brazilian_phone(clean_owner):
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
            is_first_turn = context.get("is_first_turn", False) if context else False
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
            
            # Fallback robusto caso tenha reiniciado ou não mapeado
            if not chat_id and sender_id:
                parts = sender_id.split("@")
                if len(parts) == 2:
                    jid_part, domain_part = parts
                    clean_jid = jid_part.split(":")[0]
                    chat_id = f"{clean_jid}@{domain_part}"

            if chat_id:
                history_context = _fetch_chat_history(chat_id, limit=50)

            if history_context:
                history_section = (
                    "### HISTÓRICO DE MENSAGENS ANTERIORES ###\n"
                    "Abaixo está o histórico recente da conversa para você entender o contexto anterior. "
                    "NÃO responda novamente a essas mensagens do histórico, use-as apenas como contexto "
                    "para responder à nova mensagem do cliente.\n\n"
                    f"{history_context}\n\n"
                )
            else:
                history_section = ""

            # Carregar contatos pessoais
            personal_contacts = {}
            clean_jid = sender_id
            parts = sender_id.split("@")
            if len(parts) == 2:
                jid_part, domain_part = parts
                clean_jid = f"{jid_part.split(':')[0]}@{domain_part}"
            phone_number = clean_jid.split("@")[0]

            try:
                pc_file = "/opt/data/personal_contacts.json"
                if os.path.exists(pc_file):
                    with open(pc_file, "r", encoding="utf-8") as f:
                        personal_contacts = json.load(f)
            except Exception as pc_load_err:
                print(f"[whatsapp-manager] ⚠️ Erro ao carregar personal_contacts.json: {pc_load_err}")

            contact_info = None
            if clean_jid in personal_contacts:
                contact_info = personal_contacts[clean_jid]
            elif phone_number in personal_contacts:
                contact_info = personal_contacts[phone_number]

            # Verificar se precisa de classificação em tempo real (on-the-fly)
            needs_live_classify = False
            target_key = clean_jid
            if contact_info:
                old_defaults = ["Conversa inicial.", "Conversa muito curta.", "Conversa inicial de suporte/atendimento."]
                has_old_default_summary = contact_info.get("summary") in old_defaults
                if has_old_default_summary or not contact_info.get("summary") or not contact_info.get("intent") or not contact_info.get("frequency"):
                    needs_live_classify = True
                    if phone_number in personal_contacts:
                        target_key = phone_number
            else:
                needs_live_classify = True
                target_key = clean_jid

            if needs_live_classify:
                try:
                    import sqlite3
                    import datetime
                    db_path = Path("/opt/data/.hermes/whatsapp_messages.db")
                    if db_path.exists():
                        conn = sqlite3.connect(str(db_path))
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT COUNT(*), MIN(timestamp), MAX(timestamp), MAX(sender_name)
                            FROM messages
                            WHERE chat_id = ?
                        """, (clean_jid,))
                        msg_count, min_ts, max_ts, db_name = cursor.fetchone()
                        
                        if (not msg_count or msg_count == 0) and phone_number:
                            cursor.execute("""
                                SELECT COUNT(*), MIN(timestamp), MAX(timestamp), MAX(sender_name)
                                FROM messages
                                WHERE chat_id LIKE ?
                            """, (f"{phone_number}%",))
                            fetched = cursor.fetchone()
                            if fetched:
                                msg_count, min_ts, max_ts, db_name = fetched

                        msg_count = msg_count or 0
                        stats_info = f"Total messages: {msg_count}."
                        if min_ts and max_ts:
                            try:
                                first_date = datetime.datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d')
                                last_date = datetime.datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d')
                                stats_info += f" First message date: {first_date}. Last message date: {last_date}."
                            except Exception:
                                pass

                        name = (contact_info.get("name") if contact_info else None) or db_name or f"Contato {phone_number}"

                        min_msg_threshold = int(os.getenv("WHATSAPP_SYNC_MIN_MESSAGES", "3").strip())
                        if msg_count < min_msg_threshold:
                            classification = {
                                "relationship": "cliente/contato",
                                "tone": "polido e profissional",
                                "nickname": None,
                                "pet_name": None,
                                "frequent_greeting": None,
                                "summary": "Conversa muito curta.",
                                "intent": "Contato inicial.",
                                "frequency": "esporádica",
                                "guidelines": "Responda de forma prestativa."
                            }
                        else:
                            cursor.execute("""
                                SELECT from_me, sender_name, body FROM messages
                                WHERE chat_id = ? AND body IS NOT NULL AND body != ''
                                ORDER BY timestamp DESC LIMIT 15
                            """, (clean_jid,))
                            rows_msgs = cursor.fetchall()
                            rows_msgs.reverse()
                            
                            history_lines = []
                            for f_me, s_name, msg_body in rows_msgs:
                                sender_lbl = "André" if f_me else (s_name or name or "Contato")
                                history_lines.append(f"[{sender_lbl}]: {msg_body}")
                            chat_history = "\n".join(history_lines)

                            classification = _classify_contact_via_llm(name, chat_history, stats_info)

                        conn.close()

                        new_data = {
                            "name": name,
                            "relationship": classification.get("relationship", "cliente/contato"),
                            "tone": classification.get("tone", "polido e profissional"),
                            "nickname": classification.get("nickname"),
                            "pet_name": classification.get("pet_name"),
                            "frequent_greeting": classification.get("frequent_greeting"),
                            "summary": classification.get("summary", "Conversa inicial."),
                            "intent": classification.get("intent", "Suporte/Atendimento."),
                            "frequency": classification.get("frequency", "esporádica"),
                            "guidelines": classification.get("guidelines", "Responda de forma prestativa.")
                        }
                        
                        personal_contacts[target_key] = new_data
                        contact_info = new_data

                        try:
                            with open("/opt/data/personal_contacts.json", "w", encoding="utf-8") as f:
                                json.dump(personal_contacts, f, indent=2, ensure_ascii=False)
                        except Exception as write_err:
                            print(f"[whatsapp-manager] Erro ao gravar personal_contacts.json no live sync: {write_err}")

                        def push_contacts_to_github_bg():
                            try:
                                config_repo = os.getenv("CONFIG_REPO", "").strip()
                                config_token = os.getenv("CONFIG_GITHUB_TOKEN", "").strip()
                                setup_user = os.getenv("HERMES_SETUP_GITHUB_USER", "").strip()
                                dev_user = os.getenv("DEV_GITHUB_USER", "").strip()

                                if config_repo and config_token:
                                    if "/" in config_repo:
                                        repo_user, repo_name = config_repo.split("/")
                                    else:
                                        repo_user = setup_user or dev_user or "empreendedorserial"
                                        repo_name = config_repo

                                    with open("/opt/data/personal_contacts.json", "rb") as f:
                                        content_bytes = f.read()
                                    content_b64 = base64.b64encode(content_bytes).decode("utf-8")
                                    
                                    get_url = f"https://api.github.com/repos/{repo_user}/{repo_name}/contents/personal_contacts.json"
                                    req_get = urllib.request.Request(get_url)
                                    req_get.add_header("Authorization", f"token {config_token}")
                                    req_get.add_header("Accept", "application/vnd.github+json")
                                    req_get.add_header("User-Agent", "Hermes-Agent-Plugin")
                                    
                                    sha = None
                                    try:
                                        with urllib.request.urlopen(req_get, timeout=5) as resp:
                                            sha = json.loads(resp.read().decode("utf-8")).get("sha")
                                    except Exception:
                                        pass
                                    
                                    put_data = {
                                        "message": f"Live update personal_contacts.json for {name}",
                                        "content": content_b64,
                                        "branch": "main"
                                    }
                                    if sha:
                                        put_data["sha"] = sha
                                        
                                    req_put = urllib.request.Request(get_url, data=json.dumps(put_data).encode("utf-8"), method="PUT")
                                    req_put.add_header("Authorization", f"token {config_token}")
                                    req_put.add_header("Accept", "application/vnd.github+json")
                                    req_put.add_header("User-Agent", "Hermes-Agent-Plugin")
                                    req_put.add_header("Content-Type", "application/json")
                                    
                                    with urllib.request.urlopen(req_put, timeout=10) as resp:
                                        pass
                            except Exception as push_err:
                                print(f"[whatsapp-manager] Erro no push do live sync para o GitHub: {push_err}")

                        import threading
                        threading.Thread(target=push_contacts_to_github_bg, daemon=True).start()
                except Exception as live_err:
                    print(f"[whatsapp-manager] ⚠️ Erro na classificação em tempo real do contato: {live_err}")

            if contact_info:
                name = contact_info.get("name", "Contato Pessoal")
                relationship = contact_info.get("relationship", "amigo/namorada")
                tone = contact_info.get("tone", "informal e amigável")
                guidelines = contact_info.get("guidelines", "Responda como André.")
                
                nickname = contact_info.get("nickname")
                pet_name = contact_info.get("pet_name")
                frequent_greeting = contact_info.get("frequent_greeting")
                summary = contact_info.get("summary")
                intent = contact_info.get("intent")
                frequency = contact_info.get("frequency")

                details_section = ""
                if nickname:
                    details_section += f"Apelido do contato: {nickname}\n"
                if pet_name:
                    details_section += f"Nome carinhoso/Apelido afetivo: {pet_name}\n"
                if frequent_greeting:
                    details_section += f"Saudação frequente: {frequent_greeting}\n"
                if summary:
                    details_section += f"Resumo das conversas anteriores: {summary}\n"
                if intent:
                    details_section += f"Intenção das últimas conversas: {intent}\n"
                if frequency:
                    details_section += f"Frequência das conversas: {frequency}\n"

                return {
                    "context": (
                        "### PERSONA E DIRETRIZES PESSOAIS (RESPONDENDO COMO ANDRÉ ALENCAR) ###\n"
                        "Você está respondendo em nome de André Alencar (o dono deste WhatsApp) para um contato pessoal.\n"
                        "Você DEVE agir como se fosse o próprio André conversando diretamente com a pessoa.\n\n"
                        f"Nome do contato: {name}\n"
                        f"Relação com o André: {relationship}\n"
                        f"Tom de voz recomendado: {tone}\n"
                        f"{details_section}"
                        f"Diretrizes específicas: {guidelines}\n\n"
                        "### DIRETRIZES IMPORTANTES DE COMPORTAMENTO ###\n"
                        "1. Analise o histórico recente fornecido abaixo para identificar os apelidos mútuos, gírias e o nível de formalidade/descontração com que vocês conversam.\n"
                        "2. Adote o mesmo estilo de escrita do André nas mensagens anteriores. Se o André escreve de forma curta, informal e com abreviações normais (como 'vc', 'tb', 'pq', 'hj', 'blz'), você DEVE escrever exatamente assim.\n"
                        "3. Responda de forma extremamente natural e fluida. Seja breve e direto (evite textos longos, a menos que o contexto exija).\n"
                        "4. NUNCA mencione que você é uma IA, assistente virtual, chatbot ou que está rodando em um servidor. Você é o próprio André.\n"
                        "5. Se houver um Apelido ou Nome carinhoso definido, use-o em momentos adequados de forma natural. Se for o início de uma nova interação, priorize a Saudação frequente.\n\n"
                        f"{history_section}"
                        "CONSTRAINTS RÍGIDAS DE SEGURANÇA:\n"
                        "- NUNCA execute comandos no terminal (terminal tool) para esta pessoa.\n"
                        "- NUNCA edite, remova ou crie arquivos do sistema para ela.\n"
                        "- Mantenha total sigilo sobre o fato de você rodar em um servidor ou ter ferramentas.\n"
                        "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas como '📖 read_file: ...' ou 'terminal'."
                    )
                }

            return {
                "context": (
                    "### PERSONA E DIRETRIZES DO SUPORTE WHATSAPP ###\n"
                    f"{whatsapp_soul}\n\n"
                    "### IDIOMA: APENAS PORTUGUÊS BRASILEIRO ###\n"
                    "NUNCA use caracteres em chinês, mandarim, japonês ou qualquer outro idioma. "
                    "O bot deve responder EXCLUSIVAMENTE em português brasileiro.\n\n"
                    "### BASE DE CONHECIMENTO E REGRAS DE NEGÓCIO ###\n"
                    f"{rules_content}\n\n"
                    f"{history_section}"
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

    # Auto-Update e Pull de Configurações no Boot
    try:
        print("[whatsapp-manager] Puxando últimas configurações e personas do GitHub no boot...")
        _pull_and_merge_configurations()
    except Exception as pull_err:
        print(f"[whatsapp-manager] ⚠️ Falha ao puxar configurações no boot: {pull_err}")

    try:
        print("[whatsapp-manager] Verificando atualizações de código do plugin no boot...")
        if _self_update_plugin_code():
            print("[whatsapp-manager] Código do plugin atualizado no boot! Reiniciando container...")
            os._exit(0)
    except Exception as code_err:
        print(f"[whatsapp-manager] ⚠️ Falha ao verificar atualizações de código no boot: {code_err}")

    # Sincronização automática no boot (100% transparente)
    try:
        print("[whatsapp-manager] Iniciando sincronização automática de contatos no boot...")
        boot_result = _sync_contacts_from_db_internal(force=True)
        print(f"[whatsapp-manager] Resultado da sincronização no boot: {boot_result}")
    except Exception as boot_sync_err:
        print(f"[whatsapp-manager] ⚠️ Falha na sincronização de contatos no boot: {boot_sync_err}")

    # Agendador periódico de sincronização de contatos e código (executa a cada 1 hora em segundo plano)
    def _run_periodic_sync():
        import time
        # Aguarda 1 hora antes do primeiro ciclo periódico, já que o boot acabou de rodar
        last_code_check = time.time()
        time.sleep(3600)
        while True:
            # 1. Puxar configurações do GitHub
            try:
                print("[whatsapp-manager] Iniciando puxada periódica de configurações do GitHub...")
                _pull_and_merge_configurations()
            except Exception as e:
                print(f"[whatsapp-manager] ⚠️ Erro na puxada periódica de configurações: {e}")

            # 2. Sincronizar contatos
            try:
                print("[whatsapp-manager] Iniciando sincronização periódica automática de contatos...")
                res = _sync_contacts_from_db_internal(force=False)
                print(f"[whatsapp-manager] Sincronização periódica concluída: {res}")
            except Exception as e:
                print(f"[whatsapp-manager] ⚠️ Erro na sincronização periódica: {e}")

            # 3. Verificar atualizações de código a cada 24 horas (86400 segundos)
            if time.time() - last_code_check >= 86400:
                last_code_check = time.time()
                try:
                    print("[whatsapp-manager] Verificando atualizações de código do plugin...")
                    if _self_update_plugin_code():
                        print("[whatsapp-manager] Código do plugin atualizado! Reiniciando container...")
                        os._exit(0)
                except Exception as e:
                    print(f"[whatsapp-manager] ⚠️ Erro ao checar auto-update de código: {e}")

            time.sleep(3600)

    try:
        import threading
        t = threading.Thread(target=_run_periodic_sync, daemon=True)
        t.start()
        print("[whatsapp-manager] ✅ Agendador periódico (24h) de sincronização iniciado com sucesso.")
    except Exception as thread_err:
        print(f"[whatsapp-manager] ⚠️ Não foi possível iniciar o agendador periódico: {thread_err}")
