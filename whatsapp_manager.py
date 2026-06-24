"""WhatsApp Manager Plugin para André Alencar."""

import sys
import os
import re
import json
import shutil
import sqlite3
import base64
import time
import threading
import datetime
import urllib.request
import urllib.error
import urllib.parse
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import logging

logger = logging.getLogger("whatsapp_manager")
logger.setLevel(logging.INFO)

# Handler personalizado: INFO→stdout, WARNING+→stderr, com prefixo [whatsapp-manager]
if not logger.handlers:
    class _WMLogHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                stream = sys.stderr if record.levelno >= logging.WARNING else sys.stdout
                print(msg, file=stream)
            except Exception:
                self.handleError(record)

    _handler = _WMLogHandler()
    _handler.setFormatter(logging.Formatter('[whatsapp-manager] %(message)s'))
    logger.addHandler(_handler)
    logger.propagate = False



class PluginConfig:
    @property
    def google_api_key(self) -> str:
        key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not key:
            # Fallback: ler do credential_pool.gemini no auth.json do Hermes
            try:
                hermes_home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
                auth_path = Path(hermes_home) / "auth.json"
                if auth_path.exists():
                    auth = json.loads(auth_path.read_text(encoding="utf-8"))
                    key = (auth.get("credential_pool", {}).get("gemini", "") or "").strip()
            except Exception:
                pass
        return key

    @property
    def whatsapp_client_media_model(self) -> str:
        return os.getenv("WHATSAPP_CLIENT_MEDIA_MODEL", "gemini-3.1-flash-lite").strip()

    @property
    def message_server_url(self) -> str:
        return os.getenv("MESSAGE_SERVER_URL", "http://127.0.0.1:18732").strip()
    
    @property
    def whatsapp_bridge_url(self) -> str:
        return os.getenv("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3000").strip()

    @property
    def openai_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY", "").strip()

    @property
    def openrouter_api_key(self) -> str:
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    @property
    def whatsapp_contact_classifier_model(self) -> str:
        return os.getenv("WHATSAPP_CONTACT_CLASSIFIER_MODEL", "").strip()

    @property
    def whatsapp_sync_max_classifications(self) -> int:
        val = os.getenv("WHATSAPP_SYNC_MAX_CLASSIFICATIONS", "100").strip()
        try:
            return int(val)
        except ValueError:
            return 100

    @property
    def whatsapp_sync_min_messages(self) -> int:
        val = os.getenv("WHATSAPP_SYNC_MIN_MESSAGES", "3").strip()
        try:
            return int(val)
        except ValueError:
            return 3

    @property
    def config_repo(self) -> str:
        return os.getenv("CONFIG_REPO", "").strip()

    @property
    def config_github_token(self) -> str:
        return os.getenv("CONFIG_GITHUB_TOKEN", "").strip()

    @property
    def hermes_setup_github_user(self) -> str:
        return os.getenv("HERMES_SETUP_GITHUB_USER", "").strip()

    @property
    def dev_github_user(self) -> str:
        return os.getenv("DEV_GITHUB_USER", "").strip()

    @property
    def dev_github_token(self) -> str:
        return os.getenv("DEV_GITHUB_TOKEN", "").strip()

    @property
    def github_user(self) -> str:
        return (self.hermes_setup_github_user or self.dev_github_user or "empreendedorserial").strip()

    @property
    def whatsapp_owner_number(self) -> str:
        return os.getenv("WHATSAPP_OWNER_NUMBER", "").strip()

    @property
    def whatsapp_owner_model(self) -> str:
        return os.getenv("WHATSAPP_OWNER_MODEL", "gemini-3.1-flash-lite").strip()

    @property
    def whatsapp_owner_provider(self) -> str:
        return os.getenv("WHATSAPP_OWNER_PROVIDER", "gemini").strip()

    @property
    def whatsapp_client_model(self) -> str:
        return os.getenv("WHATSAPP_CLIENT_MODEL", "gemini-3.1-flash-lite").strip()

    @property
    def whatsapp_client_provider(self) -> str:
        return os.getenv("WHATSAPP_CLIENT_PROVIDER", "gemini").strip()

    @property
    def whatsapp_first_response_delay_s(self) -> int:
        val = os.getenv("WHATSAPP_FIRST_RESPONSE_DELAY_S", "30").strip()
        try:
            return int(val)
        except ValueError:
            return 30

    @property
    def whatsapp_live_classify_cooldown(self) -> int:
        val = os.getenv("WHATSAPP_LIVE_CLASSIFY_COOLDOWN", "3600").strip()
        try:
            return int(val)
        except ValueError:
            return 3600

config = PluginConfig()


# Mapeamento temporário sender_id -> chat_id (usado entre pre_gateway_dispatch e pre_llm_call)
_sender_to_chat: dict[str, str] = {}

# Cache do último texto do owner (usado em pre_llm_call para detecção cross-session)
_last_owner_text: dict[str, str] = {}

# Mapeamento LID -> telefone obtido da ponte no bot-status
_lid_to_phone: dict[str, str] = {}

# Atualização de contato pendente aguardando número do owner: { sender_id -> {name, fields} }
_pending_contact_update: dict[str, dict] = {}

# Último cartão de contato compartilhado pelo owner: { sender_id -> {name, phone} }
_pending_contact_card: dict[str, dict] = {}

# Cache TTL para _check_bot_paused() — evita HTTP a cada mensagem
_BOT_STATUS_TTL_S: int = int(os.getenv("WHATSAPP_BOT_STATUS_TTL_S", "5"))
_bot_status_cache: dict = {"paused": False, "ts": 0.0}

# Cache TTL para _check_chat_silenced() — evita HTTP a cada mensagem
_CHAT_STATUS_TTL_S: int = int(os.getenv("WHATSAPP_CHAT_STATUS_TTL_S", "5"))
_chat_status_cache: dict[str, dict] = {}  # chat_id -> {"silenced": bool, "ts": float}


def _get_media_info(event) -> dict:
    """Extrai informações de mídia de um objeto de evento de forma extremamente robusta."""
    info = {
        "has_media": False,
        "media_type": None,
        "media_urls": [],
        "message_id": None
    }
    if not event:
        return info

    # 1. Tentar ler atributos diretos do objeto event
    for attr in ["has_media", "hasMedia"]:
        if hasattr(event, attr):
            info["has_media"] = getattr(event, attr)
            break
            
    for attr in ["media_type", "mediaType"]:
        if hasattr(event, attr):
            info["media_type"] = getattr(event, attr)
            break
            
    for attr in ["media_urls", "mediaUrls"]:
        if hasattr(event, attr):
            val = getattr(event, attr)
            if isinstance(val, list):
                info["media_urls"] = val
            elif isinstance(val, str):
                info["media_urls"] = [val]
            break

    for attr in ["message_id", "messageId", "id"]:
        if hasattr(event, attr):
            info["message_id"] = getattr(event, attr)
            break

    # 2. Tentar obter a partir de payload bruto (dict) no evento se disponível
    raw = None
    for attr in ["raw", "raw_event", "payload", "data"]:
        if hasattr(event, attr):
            val = getattr(event, attr)
            if isinstance(val, dict):
                raw = val
                break
    
    if isinstance(raw, dict):
        if not info["has_media"]:
            info["has_media"] = raw.get("hasMedia") or raw.get("has_media") or False
        if not info["media_type"]:
            info["media_type"] = raw.get("mediaType") or raw.get("media_type")
        if not info["media_urls"]:
            urls = raw.get("mediaUrls") or raw.get("media_urls") or []
            if isinstance(urls, list):
                info["media_urls"] = urls
            elif isinstance(urls, str):
                info["media_urls"] = [urls]
        if not info["message_id"]:
            info["message_id"] = raw.get("messageId") or raw.get("message_id") or raw.get("id")

    return info


def _get_mime_type(file_path: str) -> str:
    """Retorna o tipo MIME adequado com base na extensão do arquivo."""
    ext = os.path.splitext(file_path.lower())[1]
    mime_map = {
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif"
    }
    return mime_map.get(ext, "application/octet-stream")


def _process_media_message(event) -> str | None:
    """Processa mensagem de mídia (áudio ou imagem) usando Gemini, OpenAI ou OpenRouter.

    Retorna a transcrição ou descrição, ou None se falhar/não for mídia.
    """
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    media_model = config.whatsapp_client_media_model
    if not google_key and not openai_key and not openrouter_key:
        logger.info("Nenhuma API Key configurada para processamento de mídia.")
        return None
        
    media_info = _get_media_info(event)
    if not media_info["has_media"] or not media_info["media_urls"]:
        return None
        
    media_type = media_info["media_type"]
    
    # Limita a no máximo 5 imagens por mensagem, ou 1 áudio
    if media_type == "image":
        urls_to_process = media_info["media_urls"][:5]
        prompt = "Descreva as imagens fornecidas detalhadamente em português (identifique textos, objetos e o contexto geral). Retorne APENAS a descrição direta de todas elas de forma unificada, sem nenhuma introdução, explicações adicionais ou metalinguagem."
    elif media_type in ["ptt", "audio"]:
        urls_to_process = media_info["media_urls"][:1]
        prompt = "Transcreva o áudio de forma literal e precisa, em português. Retorne APENAS o texto da transcrição, sem nenhuma introdução, explicação, aspas ou comentários."
    else:
        # Outros tipos de mídia não são suportados para transcrição/descrição direta
        return None

    parts = []
    for file_path in urls_to_process:
        if not os.path.exists(file_path):
            logger.info(f"Arquivo de mídia não encontrado: {file_path}")
            continue
            
        mime_type = _get_mime_type(file_path)
        
        try:
            with open(file_path, "rb") as f:
                base64_data = base64.b64encode(f.read()).decode("utf-8")
                parts.append({
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64_data
                    }
                })
        except OSError as read_err:
            logger.error(f"Erro ao ler arquivo de mídia para envio: {read_err}")
        finally:
            # Remover o arquivo físico após carregar os dados em memória para honrar a diretriz de não armazenar mídias
            try:
                os.remove(file_path)
                logger.info(f"Arquivo temporário de mídia removido para economizar espaço: {file_path}")
            except OSError as delete_err:
                logger.warning(f"Erro ao deletar arquivo de mídia temporário: {delete_err}")

    if not parts:
        return None

    # --- Gemini ---
    if google_key:
        parts_with_prompt = parts + [{"text": prompt}]
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{media_model}:generateContent?key={google_key}"
            req = urllib.request.Request(url, data=json.dumps({"contents": [{"parts": parts_with_prompt}]}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
                return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            logger.warning(f"[media] Gemini falhou: {e}")

    # --- OpenAI gpt-4o (suporta áudio base64 via chat completions) ---
    if openai_key and media_type in ["ptt", "audio"] and parts:
        try:
            audio_part = parts[0]
            b64 = audio_part["inlineData"]["data"]
            mime = audio_part["inlineData"]["mimeType"]
            oai_model = "gpt-4o-audio-preview" if "audio" in (media_model or "") else "gpt-4o-audio-preview"
            payload = {
                "model": oai_model,
                "modalities": ["text"],
                "messages": [{"role": "user", "content": [
                    {"type": "input_audio", "input_audio": {"data": b64, "format": "wav" if "wav" in mime else "mp3" if "mp3" in mime else "mp4" if "mp4" in mime else "wav"}},
                    {"type": "text", "text": prompt},
                ]}],
            }
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
                return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[media] OpenAI falhou: {e}")

    # --- OpenRouter (Gemini via OR) ---
    if openrouter_key and media_type in ["ptt", "audio"] and parts:
        try:
            audio_part = parts[0]
            b64 = audio_part["inlineData"]["data"]
            mime = audio_part["inlineData"]["mimeType"]
            or_model = media_model or "google/gemini-flash-1.5-8b"
            payload = {
                "model": or_model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ]}],
            }
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
                return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[media] OpenRouter falhou: {e}")

    logger.error("[media] Todos os provedores falharam para transcrição de mídia.")
    return None


def _update_db_message(db_path: str, msg_id: str, new_body: str) -> int:
    """Atualiza o corpo da mensagem no SQLite detectando dinamicamente a coluna de ID."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Verificar nomes de colunas
        cursor.execute("PRAGMA table_info(messages)")
        columns = [row[1] for row in cursor.fetchall()]
        
        id_column = None
        if "message_id" in columns:
            id_column = "message_id"
        elif "msg_id" in columns:
            id_column = "msg_id"
        elif "id" in columns:
            id_column = "id"
            
        if id_column:
            cursor.execute(f"UPDATE messages SET body = ? WHERE {id_column} = ?", (new_body, msg_id))
            conn.commit()
            updated_rows = cursor.rowcount
            conn.close()
            return updated_rows
        else:
            conn.close()
            return -1
    except Exception as e:  # noqa: BLE001 — sqlite3.Error + OSError both needed
        logger.error(f"DB update error para msg_id {msg_id}: {e}", exc_info=True)
        return -2


def _persist_owner_message_to_db(chat_id: str, message_id: str, body: str, timestamp: int, sender_name: str = "André Alencar") -> None:
    """Insere mensagem manual do dono no whatsapp_messages.db (Hermes não grava from_me=1)."""
    if not body:
        return
    db_path = Path("/opt/data/.hermes/whatsapp_messages.db")
    if not db_path.exists():
        return
    try:
        _sender_id = config.whatsapp_owner_number or sender_name
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO messages
                    (chat_id, sender_id, sender_name, message_id, message_type, body, timestamp, from_me)
                VALUES (?, ?, ?, ?, 'text', ?, ?, 1)
                """,
                (chat_id, _sender_id, sender_name, message_id or f"owner_{int(time.time())}", body, timestamp),
            )
            conn.commit()
            if cur.rowcount:
                logger.info(f"[owner-msg] Gravado no SQLite: chat={chat_id} body='{body[:60]}'")
    except Exception as e:
        logger.warning(f"[owner-msg] Erro ao gravar mensagem do dono: {e}")


def _persist_transcription_to_db(db_path: str, msg_id: str, new_body: str):
    """Executa a persistência da transcrição/descrição tratando eventuais race conditions via thread."""
    # 1. Tentar atualizar imediatamente
    rows = _update_db_message(db_path, msg_id, new_body)
    if rows == 0:
        # Se 0 linhas afetadas, a mensagem pode não ter sido inserida ainda.
        # Spawna uma thread em background para tentar atualizar com retries.
        def _bg_update():
            for delay in [1, 3, 5]:
                time.sleep(delay)
                r = _update_db_message(db_path, msg_id, new_body)
                if r > 0:
                    logger.info(f"SQLite atualizado em background para msg_id={msg_id}")
                    break
        threading.Thread(target=_bg_update, daemon=True).start()




def _resolve_phone_from_jid(jid: str) -> str:
    """Traduz JID do WhatsApp (seja LID ou formato padrão) para JID com telefone clássico usando cache de LIDs."""
    if not jid:
        return jid
    # Remover device suffix se houver
    clean_jid = jid.split(":")[0]
    if "@" in clean_jid:
        jid_part, domain_part = clean_jid.split("@", 1)
    else:
        jid_part, domain_part = clean_jid, "s.whatsapp.net"

    # Se for LID e não estiver no cache, forçar atualização chamando _check_bot_paused
    if domain_part == "lid" and jid_part not in _lid_to_phone:
        try:
            _check_bot_paused()
        except Exception:
            pass

    # Se for LID, tentar mapear
    if domain_part == "lid" or jid_part in _lid_to_phone:
        phone = _lid_to_phone.get(jid_part)
        if phone:
            return f"{phone}@s.whatsapp.net"
    
    return f"{jid_part}@{domain_part}"

# URL do servidor de mensagens
MESSAGE_SERVER_URL = config.message_server_url

# URL do bridge WhatsApp
BRIDGE_URL = config.whatsapp_bridge_url


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
    """Verifica se o bot está pausado via endpoint do bridge e atualiza o mapa de LIDs.

    Resultado é cacheado por _BOT_STATUS_TTL_S segundos (padrão: 5s via env
    WHATSAPP_BOT_STATUS_TTL_S) para evitar uma chamada HTTP a cada mensagem.
    """
    global _lid_to_phone, _bot_status_cache
    now = time.time()
    if now - _bot_status_cache["ts"] < _BOT_STATUS_TTL_S:
        return _bot_status_cache["paused"]
    try:
        url = f"{BRIDGE_URL}/bot-status"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            new_map = data.get("lidToPhone")
            if isinstance(new_map, dict):
                _lid_to_phone.update(new_map)
            paused = data.get("botPaused", False)
            _bot_status_cache = {"paused": paused, "ts": now}
            return paused
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        # Bridge offline ou resposta inválida — retorna padrão seguro
        return False


def _check_chat_silenced(chat_id: str) -> bool:
    """Verifica se uma conversa específica está silenciada temporariamente.

    Resultado é cacheado por _CHAT_STATUS_TTL_S segundos (padrão: 5s via env
    WHATSAPP_CHAT_STATUS_TTL_S) para evitar uma chamada HTTP a cada mensagem.
    """
    now = time.time()
    cached = _chat_status_cache.get(chat_id)
    if cached and now - cached["ts"] < _CHAT_STATUS_TTL_S:
        return cached["silenced"]
    try:
        safe_chat_id = urllib.parse.quote(chat_id)
        url = f"{BRIDGE_URL}/chat-status/{safe_chat_id}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            silenced = data.get("isSilenced", False)
            _chat_status_cache[chat_id] = {"silenced": silenced, "ts": now}
            return silenced
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        # Bridge offline ou resposta inválida — retorna padrão seguro
        return False


def _fetch_chat_history(chat_id: str, limit: int = 50) -> str:
    """Busca histórico de mensagens do servidor HTTP."""
    try:
        url = f"{MESSAGE_SERVER_URL}/chat/{chat_id}/messages?limit={limit}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("history", "")
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        # Servidor de mensagens offline ou resposta inválida
        return ""


def _fetch_all_bridge_contact_names() -> dict[str, str]:
    """Busca todos os nomes de contatos do bridge via /contacts/all. Retorna dict jid→name."""
    try:
        url = f"{BRIDGE_URL}/contacts/all"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {c["jid"]: c["name"] for c in data.get("contacts", []) if c.get("jid") and c.get("name")}
    except Exception as e:
        logger.info(f"[sync] bridge /contacts/all falhou: {e}")
        return {}


def _resolve_contact_name_from_bridge(jid: str) -> str | None:
    """Consulta o Baileys via bridge para obter o pushName/contact name de um JID.

    Retorna None se nao conseguir resolver (contato nao existe, bridge offline, etc).
    """
    if not jid:
        return None
    try:
        import urllib.parse
        safe = urllib.parse.quote(jid, safe="")
        url = f"{BRIDGE_URL}/contact/{safe}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
            return None
    except Exception as e:
        logger.info(f"bridge contact lookup falhou para {jid}: {e}")
        return None


def _best_contact_name(jid: str, bridge_name: str | None, db_name: str | None, phone: str) -> tuple[str, str]:
    """Resolve o melhor nome disponivel para um contato.

    Ordem de prioridade:
    1. Nome vindo do Baileys (pushName) se for real (nao for o proprio JID/numero)
    2. Nome vindo do bridge log (whatsapp_messages.db.sender_name) se for real
    3. Fallback: "Contato {phone}"

    Retorna (nome, fonte) onde fonte e um de: "bridge", "log", "fallback".
    """
    def is_generic(name):
        if not name or not isinstance(name, str):
            return True
        n = name.strip()
        if not n:
            return True
        # Numeros puros sao genericos
        if n.replace("+", "").replace(" ", "").isdigit():
            return True
        # JIDs ou numeros puros nao contam
        if "@" in n or n.startswith("+"):
            return True
        return False

    if not is_generic(bridge_name):
        return bridge_name.strip(), "bridge"
    if not is_generic(db_name):
        return db_name.strip(), "log"
    return f"Contato {phone}", "fallback"

def _extract_json_from_text(text: str) -> dict:
    """Extrai o primeiro objeto JSON válido de um texto usando balanceamento de chaves."""
    # Remove blocos markdown ```json ... ``` ou ``` ... ```
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # Tenta parse direto primeiro
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Encontra o início do primeiro objeto JSON
    start = text.find("{")
    if start == -1:
        raise ValueError(f"Nenhum JSON encontrado no texto: {text[:300]}")

    # Balanceia chaves para encontrar o fim exato do objeto JSON
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    logger.info(f"JSON inválido extraído: {e} | conteúdo: {candidate[:300]}")
                    raise

    raise ValueError(f"JSON incompleto ou malformado no texto: {text[:300]}")



def _sanitize_classification_result(res: dict) -> dict:
    """Evita que nomes possessivos/parentesco do André (como 'pai', 'mãe', etc.) sejam classificados como pet_name/nickname do contato."""
    if not isinstance(res, dict):
        return res
    forbidden = {"pai", "mãe", "mae", "tio", "tia", "vô", "vó", "dono", "chefe", "patrão"}
    for field in ["pet_name", "nickname"]:
        val = res.get(field)
        if isinstance(val, str) and val.lower().strip() in forbidden:
            res[field] = None
    return res


def _call_llm_api(url: str, headers: dict, payload: dict, extract_fn, timeout: int = 30) -> str | None:
    """Envia uma requisição HTTP POST para uma API de LLM e extrai o texto da resposta.

    Args:
        url: URL da API.
        headers: Headers HTTP (Content-Type, Authorization, etc.).
        payload: Corpo da requisição como dict (será serializado para JSON).
        extract_fn: Função que recebe o dict de resposta e retorna o texto extraido.
        timeout: Timeout em segundos (padrão: 30).

    Returns:
        Texto extraido ou None em caso de erro.
    """
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return extract_fn(result)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        logger.debug(f"_call_llm_api HTTP error ({url}): {e}")
        return None
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.debug(f"_call_llm_api parse error ({url}): {e}")
        return None


def _classify_owner_intent(message: str) -> dict:
    """Classifica a intenção do owner e extrai o nome do contato alvo.

    Retorna:
        {"is_update": True, "contact_name": "Nome"} — comando de atualização de contato
        {"is_update": False, "intent": "descrição curta"} — outra intenção
    """
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    # Strip audio wrapper if present
    clean_msg = message
    m = re.match(r'\[Áudio:\s*"(.+?)"\]', message, re.IGNORECASE | re.DOTALL)
    if m:
        clean_msg = m.group(1)

    from datetime import datetime as _dt
    now_str = _dt.now().strftime("%Y-%m-%d %H:%M")

    prompt = (
        "Você é um classificador de intenções para um assistente de WhatsApp.\n"
        "Analise a mensagem e classifique em UMA das três categorias:\n\n"
        "1. ATUALIZAÇÃO DE CONTATO — usuário quer mudar dados de um contato:\n"
        "   Ex: 'coloque a Mayra como namorada', 'cadastre apelido Pedro como Pedrinho'\n\n"
        "2. STATUS DO DONO — usuário informa onde está ou o que vai fazer, com ou sem horário:\n"
        "   Ex: 'vou estar no futebol até as 21h', 'entrei em call agora', 'estou dirigindo',\n"
        "       'já voltei', 'cancelar status', 'to livre agora'\n\n"
        "3. OUTRO — qualquer outra coisa\n\n"
        f"Data/hora atual: {now_str}\n"
        f"Mensagem: \"{clean_msg}\"\n\n"
        "Retorne APENAS JSON:\n"
        "Se for atualização de contato:\n"
        "  {\"intent_type\": \"update_contact\", \"contact_name\": \"nome\", \"intent\": \"resumo 5 palavras\"}\n"
        "Se for status do dono:\n"
        "  {\"intent_type\": \"set_status\", \"description\": \"o que está fazendo\", "
        "\"until_iso\": \"YYYY-MM-DDTHH:MM:SS ou null se não informado\", "
        "\"is_clear\": false, \"intent\": \"resumo 5 palavras\"}\n"
        "  (se for 'já voltei'/'cancelar status'/'to livre': {\"intent_type\": \"set_status\", \"is_clear\": true, \"intent\": \"limpando status\"})\n"
        "Se for outro:\n"
        "  {\"intent_type\": \"other\", \"intent\": \"resumo 5 palavras\"}\n"
    )

    model_name = classify_model or "gemini-3.1-flash-lite"
    text_content = None
    for key, url, headers, make_payload, extract_fn in [
        (google_key,
         f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={google_key}",
         {"Content-Type": "application/json"},
         lambda p: {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"maxOutputTokens": 128}},
         lambda r: r["candidates"][0]["content"]["parts"][0]["text"]),
        (openai_key,
         "https://api.openai.com/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
         lambda p: {"model": model_name, "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
        (openrouter_key,
         "https://openrouter.ai/api/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
         lambda p: {"model": model_name, "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
    ]:
        if not key:
            continue
        text_content = _call_llm_api(url, headers, make_payload(prompt), extract_fn, timeout=10)
        if text_content:
            break

    if not text_content:
        return {"intent_type": "other", "is_update": False, "intent": "falha na classificação"}
    try:
        result = _extract_json_from_text(text_content)
        if not isinstance(result, dict):
            return {"intent_type": "other", "is_update": False, "intent": "resposta inválida"}
        # Compatibilidade: mapear intent_type para is_update
        intent_type = result.get("intent_type", "other")
        result["is_update"] = (intent_type == "update_contact")
        result["is_status"] = (intent_type == "set_status")
        return result
    except Exception:
        return {"intent_type": "other", "is_update": False, "intent": "erro ao parsear"}


_OWNER_STATUS_PATH = Path("/opt/data/.hermes/owner_status.json")


def _save_owner_status(description: str, until_iso: str | None, raw: str) -> None:
    from datetime import datetime as _dt
    status = {
        "active": True,
        "description": description,
        "until_iso": until_iso,
        "raw": raw,
        "set_at": _dt.now().isoformat(),
    }
    _OWNER_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[owner-status] Status salvo: '{description}' até {until_iso or 'indefinido'}")


def _clear_owner_status() -> None:
    if _OWNER_STATUS_PATH.exists():
        data = json.loads(_OWNER_STATUS_PATH.read_text(encoding="utf-8"))
        data["active"] = False
        _OWNER_STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[owner-status] Status limpo pelo dono")


def _get_active_owner_status() -> dict | None:
    """Retorna o status ativo do dono, ou None se inativo/expirado."""
    if not _OWNER_STATUS_PATH.exists():
        return None
    try:
        from datetime import datetime as _dt
        data = json.loads(_OWNER_STATUS_PATH.read_text(encoding="utf-8"))
        if not data.get("active"):
            return None
        until = data.get("until_iso")
        if until:
            try:
                if _dt.now() > _dt.fromisoformat(until):
                    # Expirado — desativar automaticamente
                    data["active"] = False
                    _OWNER_STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.info(f"[owner-status] Status expirado automaticamente: '{data.get('description')}'")
                    return None
            except Exception:
                pass
        return data
    except Exception:
        return None


def _generate_status_response(contact_name: str, relationship: str, manual_rel: str | None, status: dict) -> str:
    """Gera resposta casual como Assistente do André baseada no status e relacionamento."""
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    owner_name = config.whatsapp_owner_name or "André"
    classify_model = config.whatsapp_contact_classifier_model or "gemini-3.1-flash-lite"

    from datetime import datetime as _dt
    import locale as _locale

    description = status.get("description", "ocupado")
    until_iso = status.get("until_iso")

    until_str = ""
    if until_iso:
        try:
            until_dt = _dt.fromisoformat(until_iso)
            until_str = f" até as {until_dt.strftime('%H:%M')}"
        except Exception:
            pass

    # Contexto de data/hora e tipo de dia
    now = _dt.now()
    weekday_names = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    weekday = weekday_names[now.weekday()]
    is_weekend = now.weekday() >= 5

    # Feriados nacionais brasileiros fixos (MM-DD)
    _feriados_fixos = {
        "01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25"
    }
    today_mmdd = now.strftime("%m-%d")
    is_holiday = today_mmdd in _feriados_fixos
    day_type = "feriado" if is_holiday else ("fim de semana" if is_weekend else "dia útil")
    date_context = f"{weekday}, {now.strftime('%d/%m/%Y %H:%M')} ({day_type})"

    rel_label = manual_rel or relationship or ""
    rel_type = relationship or ""
    is_close = (
        rel_type in ("AmigoProximo", "Parente", "Filho")
        or rel_label.lower() in ("namorada", "namorado", "esposa", "marido", "mãe", "pai", "filho", "filha", "irmão", "irmã", "avó", "avô")
    )
    is_friend = rel_type == "Amigo"
    is_business = rel_type in ("Cliente", "Vendedor")

    if is_close:
        tone_instruction = "Tom casual e caloroso, como um assistente próximo da família."
        status_info = f"{owner_name} está {description}{until_str}"
    elif is_friend:
        tone_instruction = "Tom descontraído e amigável."
        status_info = f"{owner_name} está {description}{until_str}"
    elif is_business:
        tone_instruction = "Tom profissional mas leve. NÃO revele o que André está fazendo — diga apenas que ele está indisponível no momento."
        status_info = f"{owner_name} está indisponível no momento{until_str}"
    else:
        tone_instruction = (
            "Tom neutro e prestativo. NÃO revele o que André está fazendo — diga apenas que ele está indisponível. "
            "Pergunte brevemente como pode ajudar ou o que a pessoa precisa, para que André possa retornar já informado."
        )
        status_info = f"{owner_name} está indisponível no momento"

    prompt = (
        f"Você é o Assistente pessoal de {owner_name}.\n"
        f"Data e hora atual: {date_context}.\n"
        f"{status_info} e não pode responder agora.\n\n"
        f"A pessoa que mandou mensagem é: {contact_name or 'alguém'}"
        + (f" (relacionamento: {rel_label})" if rel_label else "") + ".\n"
        f"{tone_instruction}\n\n"
        f"Escreva UMA mensagem curta (2-3 linhas). Seja natural, não robótico. Não use emojis em excesso.\n"
        f"NÃO diga que é uma IA nem use termos como 'inteligência artificial'.\n"
        f"Se perguntarem se é um bot, diga que é o assistente de {owner_name} e confirme que ele está indisponível.\n"
    )

    model_name = classify_model
    text_content = None
    for key, url, headers, make_payload, extract_fn in [
        (google_key,
         f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={google_key}",
         {"Content-Type": "application/json"},
         lambda p: {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"maxOutputTokens": 200}},
         lambda r: r["candidates"][0]["content"]["parts"][0]["text"]),
        (openai_key,
         "https://api.openai.com/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
         lambda p: {"model": model_name, "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
        (openrouter_key,
         "https://openrouter.ai/api/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
         lambda p: {"model": model_name, "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
    ]:
        if not key:
            continue
        text_content = _call_llm_api(url, headers, make_payload(prompt), extract_fn, timeout=15)
        if text_content:
            break

    if not text_content:
        return f"Oi! {owner_name} está {description}{until_str} e vai retornar em breve. 👋"
    return text_content.strip()


def _extract_update_fields_via_llm(contact_name: str, message: str) -> dict:
    """Extrai campos de atualização de contato de uma mensagem em linguagem natural.

    Retorna dict com os campos explicitamente mencionados (relationship, manual_relationship,
    nickname, pet_name, notes, product, name). Nunca inventa campos.
    """
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    prompt = (
        f"O usuário pediu para atualizar o contato '{contact_name}' com a seguinte instrução:\n"
        f"\"{message}\"\n\n"
        "Extraia SOMENTE os campos explicitamente mencionados e retorne um JSON.\n"
        "Campos permitidos: relationship, manual_relationship, nickname, pet_name, notes, product, name.\n"
        "- notes = observação/anotação sobre o contato (texto livre). Use quando o usuário disser 'coloque uma observação', 'anote', 'registre que', etc.\n"
        "- nickname = apelido (ex: Bebel, Zé). Use quando o usuário disser 'o apelido é X'.\n"
        "- relationship enum: Amigo, AmigoProximo, Parente, Filho, Cliente, Vendedor\n"
        "- manual_relationship: valor livre (ex: Namorada, Filho, Esposa, Cliente VIP)\n"
        "  'como namorada' → relationship=AmigoProximo, manual_relationship=Namorada\n"
        "  'como filho' → relationship=Filho, manual_relationship=Filho\n"
        "  'como cliente' → relationship=Cliente, manual_relationship=Cliente\n"
        "NÃO invente campos. NÃO inclua tone, guidelines, summary, intent, frequency.\n"
        "Retorne APENAS JSON. Exemplos:\n"
        "  'coloque como namorada' → {\"relationship\": \"AmigoProximo\", \"manual_relationship\": \"Namorada\"}\n"
        "  'coloque uma observação: ele prefere WhatsApp' → {\"notes\": \"ele prefere WhatsApp\"}\n"
        "  'apelido é Zé, coloque como cliente' → {\"nickname\": \"Zé\", \"relationship\": \"Cliente\", \"manual_relationship\": \"Cliente\"}\n"
    )

    model_name = classify_model or "gemini-3.1-flash-lite"
    text_content = None
    for key, url, headers, make_payload, extract_fn in [
        (google_key,
         f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={google_key}",
         {"Content-Type": "application/json"},
         lambda p: {"contents": [{"parts": [{"text": p}]}], "generationConfig": {"maxOutputTokens": 256}},
         lambda r: r["candidates"][0]["content"]["parts"][0]["text"]),
        (openai_key,
         "https://api.openai.com/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
         lambda p: {"model": classify_model or "gpt-4o-mini", "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
        (openrouter_key,
         "https://openrouter.ai/api/v1/chat/completions",
         {"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
         lambda p: {"model": classify_model or "google/gemini-flash-1.5-8b", "messages": [{"role": "user", "content": p}]},
         lambda r: r["choices"][0]["message"]["content"]),
    ]:
        if not key:
            continue
        logger.info(f"[extract-fields] Chamando LLM modelo={model_name} contato='{contact_name}'")
        text_content = _call_llm_api(url, headers, make_payload(prompt), extract_fn, timeout=15)
        logger.info(f"[extract-fields] Resposta bruta: {repr(text_content)[:200] if text_content else 'None'}")
        if text_content:
            break

    if not text_content:
        logger.info(f"[extract-fields] LLM não retornou conteúdo para '{contact_name}'")
        return {}
    try:
        result = _extract_json_from_text(text_content)
        logger.info(f"[extract-fields] Campos extraídos: {result}")
        return result if isinstance(result, dict) else {}
    except Exception as e:
        logger.info(f"[extract-fields] Erro ao parsear JSON: {e} — raw: {repr(text_content)[:200]}")
        return {}


def _extract_contact_name_via_llm(message: str) -> str | None:
    """Usa a LLM para extrair o nome do contato de uma mensagem em linguagem natural.
    Retorna apenas o nome/apelido, ou None se não encontrado."""
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    prompt = (
        "Da mensagem abaixo, extraia APENAS o nome ou apelido do contato que deve ser atualizado. "
        "Responda somente com o nome, sem explicações, aspas ou pontuação. "
        "Se não houver nome claro, responda: NONE\n\n"
        f"Mensagem: {message}"
    )

    text_content = None

    if google_key:
        model_to_use = classify_model if (classify_model and "gemini" in classify_model.lower()) else "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent?key={google_key}"
        text_content = _call_llm_api(
            url,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 64},
            },
            extract_fn=lambda r: r["candidates"][0]["content"]["parts"][0]["text"],
            timeout=15,
        )

    if not text_content and openai_key:
        model_to_use = classify_model if (classify_model and any(p in classify_model.lower() for p in ["gpt", "o1-", "o3-"])) else "gpt-4o-mini"
        text_content = _call_llm_api(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}]},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=15,
        )

    if not text_content and openrouter_key:
        model_to_use = classify_model or "google/gemini-flash-1.5-8b"
        text_content = _call_llm_api(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}]},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=15,
        )

    if not text_content:
        return None

    name = text_content.strip().strip('"\'').strip()
    if not name or name.upper() == "NONE" or len(name) > 60:
        return None
    return name


def _update_full_summary(name: str, existing_full_summary: str, new_session_text: str, session_date: str) -> str | None:
    """Atualiza o full_summary de um contato com uma nova sessão de conversa.

    Chama o LLM com o resumo anterior e o texto da sessão nova, retornando
    o resumo atualizado no formato 'Mês/Ano: ...'.
    """
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    previous = f"Resumo anterior:\n{existing_full_summary}\n\n" if existing_full_summary else ""
    prompt = (
        f"Contato: {name}\n\n"
        f"{previous}"
        f"Mensagens do contato em {session_date}:\n{new_session_text}\n\n"
        f"Com base APENAS nas mensagens acima, adicione ao resumo o que {name} disse, pediu ou demonstrou nesta conversa. "
        "Use o formato: '<Mês/Ano>: <fatos reais da conversa>'. "
        "Mantenha o histórico anterior intacto. Não invente informações. "
        "Retorne APENAS o texto do resumo completo atualizado, sem títulos ou explicações."
    )

    text_content = None
    if google_key:
        model = classify_model if (classify_model and "gemini" in classify_model.lower()) else "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={google_key}"
        text_content = _call_llm_api(
            url,
            headers={"Content-Type": "application/json"},
            payload={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 512}},
            extract_fn=lambda r: r["candidates"][0]["content"]["parts"][0]["text"],
            timeout=30,
        )
    if not text_content and openai_key:
        model = classify_model if (classify_model and "gpt" in classify_model.lower()) else "gpt-4o-mini"
        text_content = _call_llm_api(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
            payload={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=30,
        )
    if not text_content and openrouter_key:
        model = classify_model or "google/gemini-flash-1.5-8b"
        text_content = _call_llm_api(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
            payload={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=30,
        )
    return text_content.strip() if text_content else None


def _compress_full_summary(name: str, full_summary: str) -> str | None:
    """Comprime um full_summary longo em 1-2 linhas para uso no contexto de atendimento."""
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    prompt = (
        f"Histórico de conversas com {name}:\n{full_summary}\n\n"
        f"Resuma em no máximo 2 frases o que {name} costuma buscar, seu perfil e tom preferido. "
        "Use apenas fatos do histórico acima. Retorne APENAS o resumo, sem títulos."
    )

    text_content = None
    if google_key:
        model = classify_model if (classify_model and "gemini" in classify_model.lower()) else "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={google_key}"
        text_content = _call_llm_api(
            url,
            headers={"Content-Type": "application/json"},
            payload={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 128}},
            extract_fn=lambda r: r["candidates"][0]["content"]["parts"][0]["text"],
            timeout=20,
        )
    if not text_content and openai_key:
        model = classify_model if (classify_model and "gpt" in classify_model.lower()) else "gpt-4o-mini"
        text_content = _call_llm_api(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
            payload={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 128},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=20,
        )
    if not text_content and openrouter_key:
        model = classify_model or "google/gemini-flash-1.5-8b"
        text_content = _call_llm_api(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
            payload={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 128},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=20,
        )
    return text_content.strip() if text_content else None


def _sync_full_summaries(personal_contacts: dict, state_db_path, max_contacts: int = 10) -> int:
    """Atualiza full_summary para contatos com sessões novas no state.db.

    Processa sessões ainda não resumidas (posteriores a last_summarized_at),
    atualizando full_summary incrementalmente e comprimindo em summary quando longo.
    Retorna o número de contatos atualizados.
    """
    if not state_db_path or not Path(str(state_db_path)).exists():
        return 0

    updated = 0
    owner_phone = "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())

    try:
        with sqlite3.connect(str(state_db_path)) as conn:
            cur = conn.cursor()
            for contact_key, contact_data in list(personal_contacts.items()):
                if updated >= max_contacts:
                    break
                if not isinstance(contact_data, dict):
                    continue
                phone = contact_key.split("@")[0]
                if owner_phone and _normalize_brazilian_phone(phone) == _normalize_brazilian_phone(owner_phone):
                    continue

                last_summarized_at = contact_data.get("last_summarized_at") or 0

                # Buscar sessões novas para este contato
                cur.execute("""
                    SELECT s.id, s.started_at, s.title
                    FROM sessions s
                    WHERE s.source = 'whatsapp'
                    AND (s.user_id = ? OR s.user_id LIKE ?)
                    AND s.started_at > ?
                    ORDER BY s.started_at ASC
                """, (contact_key, f"{phone}%", last_summarized_at))
                new_sessions = cur.fetchall()

                if not new_sessions:
                    continue

                logger.info(f"[full-summary] {contact_data.get('name', phone)}: {len(new_sessions)} sessão(ões) nova(s)")
                contact_name = contact_data.get("name") or phone

                for session_id, started_at, title in new_sessions:
                    # Buscar mensagens da sessão
                    cur.execute("""
                        SELECT role, content FROM messages
                        WHERE session_id = ? AND content IS NOT NULL AND content != ''
                        ORDER BY timestamp ASC
                        LIMIT 60
                    """, (session_id,))
                    msgs = cur.fetchall()
                    if not msgs:
                        continue

                    # role="user" = contato falando; role="assistant" = bot respondendo
                    # Para o resumo, incluir apenas mensagens do contato (user)
                    # para não poluir com respostas do bot
                    lines = []
                    for role, content in msgs:
                        if role == "user":
                            lines.append(content[:400])
                    if not lines:
                        continue
                    session_text = "\n".join(lines)

                    try:
                        session_date = datetime.datetime.fromtimestamp(started_at).strftime("%b/%y")
                    except Exception:
                        session_date = "?"

                    new_full = _update_full_summary(
                        name=contact_name,
                        existing_full_summary=contact_data.get("full_summary") or "",
                        new_session_text=session_text,
                        session_date=session_date,
                    )
                    if new_full:
                        contact_data["full_summary"] = new_full
                        contact_data["last_summarized_at"] = started_at
                        logger.info(f"[full-summary] {contact_name}: full_summary atualizado")

                        # Comprimir em summary quando full_summary > 600 chars
                        if len(new_full) > 600:
                            compressed = _compress_full_summary(contact_name, new_full)
                            if compressed:
                                contact_data["summary"] = compressed
                                logger.info(f"[full-summary] {contact_name}: summary comprimido")
                        else:
                            contact_data["summary"] = new_full

                updated += 1

    except sqlite3.Error as e:
        logger.warning(f"[full-summary] Erro ao ler state.db: {e}")

    return updated


def _classify_contact_via_llm(name: str, chat_history: str, stats_info: str) -> dict:
    """Classifica contatos usando a API do LLM (Gemini, OpenAI ou OpenRouter) com base no histórico e estatísticas."""
    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key

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
        "1. \"Amigo\":\n"
        "   - Use this if they are a regular friend (casual communication, casual topics).\n"
        "   - Recommended tone: \"informal e amigável\".\n"
        "2. \"AmigoProximo\":\n"
        "   - Use this if they are a close friend, girlfriend, romantic partner, or close personal/intimate contacts.\n"
        "   - Recommended tone: \"informal e carinhoso\" or \"informal e amigável\".\n"
        "3. \"Parente\":\n"
        "   - Use this if they are a family member (mother, father, sibling, cousin, uncle, etc.).\n"
        "   - Recommended tone: \"informal e amigável\".\n"
        "4. \"Filho\":\n"
        "   - Use this if they are André's child/son.\n"
        "   - Recommended tone: \"informal e amigável\" or \"informal e carinhoso\".\n"
        "5. \"Cliente\":\n"
        "   - Use this if they are a customer, client, business contact, lead, or inquiring about purchasing André's systems, API, support, development, or price.\n"
        "   - Recommended tone: \"polido e profissional\".\n"
        "6. \"Vendedor\":\n"
        "   - Use this if they are a salesperson, vendor, or offering/selling products, services, platforms, tools, or partnerships to André.\n"
        "   - Recommended tone: \"técnico e direto\" or \"polido e profissional\".\n\n"
        "Extract/determine the following details:\n"
        "- \"nickname\" (apelido): Any nickname used by André to refer to this contact (e.g. \"Bru\", \"Carlos\", etc.). NEVER extract terms the contact uses to refer to André (like \"pai\", \"mãe\", \"tio\", etc.). null if none.\n"
        "- \"pet_name\" (nome carinhoso): Terms of endearment used by André to refer to this contact (e.g. \"amor\", \"vida\", \"querida\", etc.). NEVER extract terms the contact uses to refer to André (like \"pai\", \"mãe\", \"tio\", etc.). null if none.\n"
        "- \"frequent_greeting\" (saudação frequente): The typical greeting phrase used when starting a conversation (e.g. \"Eae mano\", \"Oi amor\", \"Olá\", etc.). null if none.\n"
        "- \"summary\" (resumo): Um resumo CURTO (máx 150 caracteres) sobre o que costumam conversar (em português).\n"
        "- \"intent\" (intenção): O principal objetivo/tópico das últimas mensagens em português (máx 100 caracteres).\n"
        "- \"frequency\" (frequência): The frequency of their conversations (e.g. \"diária\", \"semanal\", \"mensal\", \"esporádica\") based on the statistics and history.\n"
        "- \"product\" (produto): If the relationship is classified as \"Vendedor\", extract the name/type of product or service they are trying to sell. null otherwise.\n\n"
        "Return a JSON object with this exact structure (do NOT wrap it in markdown code blocks like ```json, just raw JSON):\n"
        "{\n"
        "  \"relationship\": \"Amigo\" | \"AmigoProximo\" | \"Parente\" | \"Filho\" | \"Cliente\" | \"Vendedor\",\n"
        "  \"tone\": \"informal e carinhoso\" | \"informal e amigável\" | \"polido e profissional\" | \"técnico e direto\",\n"
        "  \"nickname\": string | null,\n"
        "  \"pet_name\": string | null,\n"
        "  \"frequent_greeting\": string | null,\n"
        "  \"summary\": string,\n"
        "  \"intent\": string,\n"
        "  \"frequency\": string,\n"
        "  \"product\": string | null,\n"
        "  \"guidelines\": \"...máx 200 caracteres...\"\n"
        "}"
    )

    classify_model = config.whatsapp_contact_classifier_model

    # 1. Tentar Gemini API
    if google_key:
        model_to_use = classify_model if (classify_model and "gemini" in classify_model.lower()) else "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent?key={google_key}"
        text_content = _call_llm_api(
            url,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 4096},
            },
            extract_fn=lambda r: r["candidates"][0]["content"]["parts"][0]["text"],
            timeout=45,
        )
        if text_content:
            try:
                return _sanitize_classification_result(_extract_json_from_text(text_content))
            except Exception as e:
                logger.error(f"Falha ao classificar via Gemini: {e}")

    # 2. Tentar OpenAI API
    if openai_key:
        model_to_use = classify_model if (classify_model and any(p in classify_model.lower() for p in ["gpt", "o1-", "o3-"])) else "gpt-4o-mini"
        text_content = _call_llm_api(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=30,
        )
        if text_content:
            try:
                return _sanitize_classification_result(_extract_json_from_text(text_content))
            except Exception as e:
                logger.error(f"Falha ao classificar via OpenAI: {e}")

    # 3. Tentar OpenRouter API
    if openrouter_key:
        if classify_model:
            if "/" in classify_model:
                model_to_use = classify_model
            elif "gemini" in classify_model.lower():
                model_to_use = f"google/{classify_model}"
            elif "gpt" in classify_model.lower():
                model_to_use = f"openai/{classify_model}"
            else:
                model_to_use = classify_model
        else:
            model_to_use = "google/gemini-3.1-flash-lite"
        text_content = _call_llm_api(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}},
            extract_fn=lambda r: r["choices"][0]["message"]["content"],
            timeout=30,
        )
        if text_content:
            try:
                return _sanitize_classification_result(_extract_json_from_text(text_content))
            except Exception as e:
                logger.error(f"Falha ao classificar via OpenRouter: {e}")

    # Fallback default se nenhuma API key estiver disponível ou todas falharem
    return {
        "relationship": "Cliente",
        "tone": "polido e profissional",
        "nickname": None,
        "pet_name": None,
        "frequent_greeting": None,
        "summary": "Conversa inicial de suporte/atendimento.",
        "intent": "Obter ajuda ou informações sobre os sistemas do André.",
        "frequency": "esporádica",
        "product": None,
        "guidelines": "Responda de forma prestativa.",
    }


def _build_lid_phone_map(db_path: "Path | None" = None,
                         personal_contacts: "dict | None" = None) -> dict[str, str]:
    """Constrói mapa {lid → phone_digits} a partir de três fontes, em ordem de prioridade:
    1. Arquivos de sessão lid-mapping-{phone}.json (escritos pelo bridge)
    2. Banco SQLite (mensagens recebidas em chats @lid)
    3. Campo 'lid' nas entradas @s.whatsapp.net do personal_contacts (persistido por dedup anterior)
    """
    import re as _re
    lid_phone_map: dict[str, str] = {}
    session_dir = Path("/opt/data/.hermes/platforms/whatsapp/session")
    if session_dir.exists():
        try:
            _files = list(session_dir.iterdir())
        except Exception:
            _files = []
        for f in _files:
            # Formato 1: lid-mapping-{phone}.json → conteúdo é o LID
            m = _re.match(r'^lid-mapping-(\d+)\.json$', f.name)
            if m:
                try:
                    lid = json.loads(f.read_text()).strip().strip('"')
                    if lid:
                        lid_phone_map[lid] = m.group(1)
                except Exception:
                    pass
                continue
            # Formato 2: lid-mapping-{lid}_reverse.json → conteúdo é o phone
            m2 = _re.match(r'^lid-mapping-(\d+)_reverse\.json$', f.name)
            if m2:
                try:
                    phone = json.loads(f.read_text()).strip().strip('"')
                    if phone and phone.isdigit():
                        lid_phone_map[m2.group(1)] = phone
                except Exception:
                    pass
    if db_path and Path(db_path).exists():
        import sqlite3
        try:
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT chat_id, sender_id FROM messages
                    WHERE from_me=0 AND chat_id LIKE '%@lid%'
                    AND sender_id IS NOT NULL
                    AND sender_id NOT LIKE '%@lid%'
                    AND sender_id NOT LIKE '%@g.us%'
                """)
                for cid, sid in cur.fetchall():
                    lid = cid.split("@")[0]
                    phone = sid.split("@")[0].split(":")[0]
                    if phone and phone.isdigit() and lid not in lid_phone_map:
                        lid_phone_map[lid] = phone
        except Exception:
            pass
    # Fonte adicional: campo 'lid' gravado em execuções anteriores do dedup
    if personal_contacts:
        for key, entry in personal_contacts.items():
            if not isinstance(entry, dict) or "@s.whatsapp.net" not in key:
                continue
            lid_ref = entry.get("lid", "")
            if not lid_ref:
                continue
            lid_raw = lid_ref.split("@")[0]
            phone_raw = key.split("@")[0].split(":")[0]
            phone_digits = "".join(c for c in phone_raw if c.isdigit())
            if lid_raw and phone_digits and lid_raw not in lid_phone_map:
                lid_phone_map[lid_raw] = phone_digits
    logger.info(f"[lid-map] {len(lid_phone_map)} mapeamentos lid→phone carregados")
    return lid_phone_map


def _resolve_man_rel(existing_data: dict, personal_contacts: dict) -> "str | None":
    """Retorna o manual_relationship mais confiável para um contato.

    Lê do existing_data e também do @lid correspondente (campo 'lid'),
    evitando que o sync sobrescreva valores definidos via bot no @lid.
    """
    _non_manual = {"Cliente", "Geral"}
    man_rel = existing_data.get("manual_relationship")
    # Fallback: relationship explícito que era tratado como manual
    if not man_rel and existing_data.get("relationship") in [
        "Vendedor", "Amigo", "AmigoProximo", "Parente", "Filho"
    ]:
        man_rel = existing_data.get("relationship")
    # Checar entrada @lid correspondente (campo 'lid' gravado pelo dedup)
    lid_key = existing_data.get("lid")
    if lid_key and lid_key in personal_contacts:
        lid_entry = personal_contacts[lid_key]
        lid_man_rel = lid_entry.get("manual_relationship")
        if not lid_man_rel and lid_entry.get("relationship") in [
            "Vendedor", "Amigo", "AmigoProximo", "Parente", "Filho"
        ]:
            lid_man_rel = lid_entry.get("relationship")
        # @lid vence se tem valor e o atual está vazio ou é genérico
        if lid_man_rel and (not man_rel or man_rel in _non_manual):
            name_hint = existing_data.get("nickname") or existing_data.get("name") or "?"
            logger.info(f"[resolve-man-rel] {name_hint}: @lid vence → '{lid_man_rel}' (era '{man_rel}')")
            man_rel = lid_man_rel
    return man_rel


def _merge_contact_entries(primary: dict, secondary: dict) -> None:
    """
    Mescla `secondary` em `primary` in-place.
    Regras de precedência (ordem decrescente de confiabilidade):
      1. manual_relationship  — definido pelo usuário, nunca sobrescrever
      2. Campos do secondary se primary tiver placeholder/vazio
      3. relationship: secondary vence se primary não tem manual_relationship
    """
    _owner_norms = {"andre alencar", "andré alencar", "andre", "andré"}

    # manual_relationship: never overwrite, only fill if missing
    if secondary.get("manual_relationship") and not primary.get("manual_relationship"):
        primary["manual_relationship"] = secondary["manual_relationship"]

    # relationship: secondary vence se primary não tem manual_relationship
    if not primary.get("manual_relationship"):
        sec_rel = secondary.get("manual_relationship") or secondary.get("relationship")
        if sec_rel:
            primary["relationship"] = sec_rel

    # name/nickname: secondary vence se primary está vazio ou é placeholder
    for field in ("nickname", "name"):
        sec_val = secondary.get(field) or ""
        pri_val = primary.get(field) or ""
        pri_norm = _normalize_text(pri_val)
        if sec_val and (not pri_val or pri_norm in _owner_norms
                        or pri_norm.startswith("contato ")
                        or pri_norm.startswith("usuario ")):
            primary[field] = sec_val

    # campos manuais: preencher se ausente no primary
    for field in ("notes", "pet_name", "full_summary", "last_summarized_at"):
        if secondary.get(field) and not primary.get(field):
            primary[field] = secondary[field]


def _dedup_personal_contacts(personal_contacts: dict, lid_phone_map: dict) -> int:
    """
    Deduplica personal_contacts garantindo uma entrada por contato real.

    Casos tratados:
    1. @lid + @s.whatsapp.net coexistem — ambos são mantidos, mas o campo 'lid' é
       adicionado ao @s.whatsapp.net como cross-reference (sem merge, sem remoção).
    2. @s.whatsapp.net duplicado por normalização de 9º dígito brasileiro
       (ex: 5586994140236 e 558694140236) → mantém o mais recente/completo, remove o outro.

    Retorna o total de entradas removidas.
    """
    phone_to_lid = {v: k for k, v in lid_phone_map.items()}
    to_remove: list[str] = []

    # --- Passo 1: registrar campo 'lid' no @s.whatsapp.net (cross-reference apenas) ---
    for key in list(personal_contacts.keys()):
        if "@lid" in key or not isinstance(personal_contacts.get(key), dict):
            continue
        raw = key.split("@")[0].split(":")[0]
        digits = "".join(c for c in raw if c.isdigit())
        phone_norm = _normalize_brazilian_phone(digits)
        lid = phone_to_lid.get(digits) or phone_to_lid.get(phone_norm)
        if lid:
            personal_contacts[key]["lid"] = f"{lid}@lid"
            name_hint = personal_contacts[key].get("nickname") or personal_contacts[key].get("name") or key
            logger.info(f"[dedup] campo 'lid' adicionado: {name_hint} → {lid}@lid")

    # --- Passo 2: @s.whatsapp.net duplicados por normalização de telefone ---
    # Agrupa por telefone normalizado; mantém a entrada com mais campos preenchidos
    phone_norm_to_keys: dict[str, list[str]] = {}
    for key in list(personal_contacts.keys()):
        if "@lid" in key or key in to_remove or not isinstance(personal_contacts.get(key), dict):
            continue
        raw = key.split("@")[0].split(":")[0]
        digits = "".join(c for c in raw if c.isdigit())
        if not digits:
            continue
        pnorm = _normalize_brazilian_phone(digits)
        phone_norm_to_keys.setdefault(pnorm, []).append(key)

    for pnorm, keys in phone_norm_to_keys.items():
        if len(keys) < 2:
            continue
        # Escolher a entrada canonical: prefere a que tem manual_relationship, depois mais campos
        def _score(k: str) -> int:
            e = personal_contacts.get(k, {})
            return (
                bool(e.get("manual_relationship")) * 100
                + bool(e.get("nickname")) * 10
                + bool(e.get("name")) * 5
                + bool(e.get("full_summary")) * 3
                + len(e)
            )
        keys_sorted = sorted(keys, key=_score, reverse=True)
        canonical_key = keys_sorted[0]
        canonical = personal_contacts[canonical_key]
        for dup_key in keys_sorted[1:]:
            if dup_key in to_remove:
                continue
            dup_entry = personal_contacts[dup_key]
            _merge_contact_entries(primary=canonical, secondary=dup_entry)
            to_remove.append(dup_key)

    for key in to_remove:
        personal_contacts.pop(key, None)

    return len(to_remove)


def _sync_contacts_from_db_internal(force: bool = True) -> str:
    """Sincroniza contatos do SQLite local para personal_contacts.json e envia para o GitHub."""
    import sqlite3
    import datetime
    from pathlib import Path
    
    # Atualizar mapa de LIDs no início da sincronização
    try:
        _check_bot_paused()
    except Exception:
        pass
        
    base_dir = Path("/opt/data/.hermes")
    db_path = base_dir / "whatsapp_messages.db"
    state_db_path = base_dir / "state.db"
    pc_path = Path("/opt/data/personal_contacts.json")

    # 1. Carregar arquivo JSON local existente
    personal_contacts = {}
    metadata_updated = False
    if pc_path.exists():
        try:
            with open(pc_path, "r", encoding="utf-8") as f:
                personal_contacts = json.load(f)
                for k, v in personal_contacts.items():
                    if isinstance(v, dict):
                        personal_contacts[k] = _sanitize_classification_result(v)
        except Exception as e:
            logger.error(f"Erro ao ler {pc_path}: {e}")

    # 1b. Atualizar nomes placeholder com nomes reais do bridge (agenda do WhatsApp)
    _bridge_names = _fetch_all_bridge_contact_names()
    if _bridge_names:
        _names_updated = 0
        for _jid, _bname in _bridge_names.items():
            if _jid not in personal_contacts:
                continue
            _entry = personal_contacts[_jid]
            if not isinstance(_entry, dict):
                continue
            _cur_name = _entry.get("name") or ""
            _cur_name_norm = _normalize_text(_cur_name)
            # Substituir nomes placeholder, vazios, ou com nome do dono (dado incorreto)
            _owner_norms = {"andre alencar", "andré alencar", "andre", "andré"}
            _is_placeholder = (
                not _cur_name
                or _cur_name_norm.startswith("contato ")
                or _cur_name_norm.startswith("usuario ")
                or _cur_name_norm in _owner_norms
            )
            if _is_placeholder and _bname and _normalize_text(_bname) not in _owner_norms:
                _entry["name"] = _bname
                _names_updated += 1
                metadata_updated = True
        if _names_updated:
            logger.info(f"[sync] {_names_updated} nome(s) atualizado(s) via bridge /contacts/all")

    # 1b-fix. Limpar nomes do dono gravados incorretamente em entradas de contatos externos
    _owner_name_norms = {"andre alencar", "andré alencar", "andre", "andré"}
    _fixed_owner_names = 0
    for _k, _v in personal_contacts.items():
        if not isinstance(_v, dict):
            continue
        _cur = _normalize_text(_v.get("name") or "")
        if _cur in _owner_name_norms:
            # Tentar substituir pelo nome do bridge se disponível
            _bridge_name = (_bridge_names or {}).get(_k, "")
            if _bridge_name and _normalize_text(_bridge_name) not in _owner_name_norms:
                _v["name"] = _bridge_name
            else:
                _v["name"] = None
            _fixed_owner_names += 1
            metadata_updated = True
    if _fixed_owner_names:
        logger.info(f"[sync] {_fixed_owner_names} nome(s) do dono removido(s) de contatos externos")

    # 1c. Remover entradas do owner do arquivo (não devem estar no personal_contacts)
    owner_phone_norm = _normalize_brazilian_phone(
        "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())
    )
    if owner_phone_norm:
        owner_keys = [
            k for k in list(personal_contacts.keys())
            if _normalize_brazilian_phone(k.split("@")[0]) == owner_phone_norm
        ]
        for k in owner_keys:
            del personal_contacts[k]
            logger.info(f"[sync] Removida entrada do owner: {k}")

    # 1d. Deduplicar: mesclar entradas @lid com @s.whatsapp.net do mesmo contato
    _lid_phone_map_sync = _build_lid_phone_map(db_path if db_path.exists() else None, personal_contacts)
    _deduped = _dedup_personal_contacts(personal_contacts, _lid_phone_map_sync)
    if _deduped:
        logger.info(f"[sync] {_deduped} entrada(s) @lid mescladas e removidas (campo 'lid' adicionado)")
        metadata_updated = True

    # Limpar full_summary gerados com dados incorretos (exemplo do prompt ou respostas do bot)
    _bad_summary_markers = ("pediu orçamento de X", "comprou, elogiou atendimento", "André:")
    cleaned_summaries = 0
    for k, v in personal_contacts.items():
        if not isinstance(v, dict):
            continue
        fs = v.get("full_summary") or ""
        if any(marker in fs for marker in _bad_summary_markers):
            v.pop("full_summary", None)
            v.pop("last_summarized_at", None)
            cleaned_summaries += 1
    if cleaned_summaries:
        logger.info(f"[sync] {cleaned_summaries} full_summary(s) inválidos removidos para reprocessamento")

    # 2. Ler contatos únicos do SQLite com agregação de estatísticas para performance
    if not db_path.exists() and not state_db_path.exists():
        return "Erro: nenhum banco de dados SQLite do Hermes encontrado em /opt/data/.hermes/."

    db_contacts = {}
    classification_count = 0
    max_classifications = config.whatsapp_sync_max_classifications
    min_msg_threshold = config.whatsapp_sync_min_messages
    skipped_few_msgs = 0
    skipped_due_to_limit = 0
    hit_limit = False
    source_stats = {"state.db": 0, "whatsapp_messages.db": 0}

    # 2a. Fonte primaria: state.db.sessions WHERE source='whatsapp' (lista oficial do gateway Hermes)
    state_sessions = {}
    if state_db_path.exists():
        try:
            state_conn = sqlite3.connect(str(state_db_path))
            state_cursor = state_conn.cursor()
            state_cursor.execute("""
                SELECT user_id, MAX(started_at) as last_ts, COUNT(*) as session_count
                FROM sessions
                WHERE source = 'whatsapp' AND user_id IS NOT NULL
                GROUP BY user_id
                ORDER BY last_ts DESC
            """)
            for user_id, last_ts, session_count in state_cursor.fetchall():
                state_sessions[user_id] = {"last_ts": last_ts, "session_count": session_count}
            source_stats["state.db"] = len(state_sessions)
            logger.info(f"sync: {len(state_sessions)} contatos WhatsApp em state.db.sessions")
        except Exception as e:
            logger.error(f"sync: erro lendo state.db.sessions: {e}")

    # 2b. Fonte complementar: whatsapp_messages.db (mapa chat_id -> sender_name + historico)
    bridge_contacts = {}
    if db_path.exists():
        try:
            bridge_conn = sqlite3.connect(str(db_path))
            bridge_cursor = bridge_conn.cursor()
            bridge_cursor.execute("""
                SELECT chat_id,
                       MAX(CASE WHEN from_me=0 THEN sender_name ELSE NULL END) as name,
                       COUNT(*) as msg_count,
                       MIN(timestamp) as min_ts,
                       MAX(timestamp) as max_ts
                FROM messages
                WHERE chat_id NOT LIKE '%@g.us%' AND chat_id IS NOT NULL
                GROUP BY chat_id
            """)
            for chat_id, name, msg_count, min_ts, max_ts in bridge_cursor.fetchall():
                bridge_contacts[chat_id] = {
                    "name": name,
                    "msg_count": msg_count,
                    "min_ts": min_ts,
                    "max_ts": max_ts,
                }
            source_stats["whatsapp_messages.db"] = len(bridge_contacts)
            logger.info(f"sync: {len(bridge_contacts)} contatos em whatsapp_messages.db")
        except Exception as e:
            logger.error(f"sync: erro lendo whatsapp_messages.db: {e}")

    # 2c. Consolida lista unica: state.db (autoritativo) + bridge (fallback para quem nao tem sessao)
    all_chat_ids = []
    seen = set()
    for user_id in state_sessions.keys():
        seen.add(user_id)
        all_chat_ids.append(user_id)
    for chat_id in bridge_contacts.keys():
        if chat_id not in seen:
            seen.add(chat_id)
            all_chat_ids.append(chat_id)
    logger.info(f"sync: {len(all_chat_ids)} contatos unicos para processar")

    try:
        conn = sqlite3.connect(str(db_path)) if db_path.exists() else None
        state_conn = sqlite3.connect(str(state_db_path)) if state_db_path.exists() else None
        owner_phone_clean = _normalize_brazilian_phone(
            "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())
        )
        for chat_id in all_chat_ids:
            if not chat_id:
                continue
            resolved_chat = _resolve_phone_from_jid(chat_id)
            phone = resolved_chat.split("@")[0]
            # Nunca classificar o próprio dono
            if owner_phone_clean and _normalize_brazilian_phone(phone) == owner_phone_clean:
                continue
            
            # Verificar se já existe por JID, JID resolvido ou por número
            exists = False
            existing_key = None
            for key in list(personal_contacts.keys()):
                if key in [chat_id, resolved_chat, phone]:
                    exists = True
                    existing_key = key
                    break
            
            # Coletar estatisticas e nome das duas fontes
            name = None
            msg_count = 0
            min_ts = None
            max_ts = None

            # Bridge: nome + contagem + timestamps reais
            if chat_id in bridge_contacts:
                name = bridge_contacts[chat_id].get("name")
                msg_count = max(msg_count, bridge_contacts[chat_id].get("msg_count", 0))
                min_ts = bridge_contacts[chat_id].get("min_ts")
                max_ts = bridge_contacts[chat_id].get("max_ts")
            
            # State: contagem de sessoes + ultimo acesso
            if chat_id in state_sessions:
                msg_count = max(msg_count, state_sessions[chat_id].get("session_count", 0))
                state_last = state_sessions[chat_id].get("last_ts")
                if state_last:
                    if max_ts is None or state_last > max_ts:
                        max_ts = state_last
                    if min_ts is None or state_last < min_ts:
                        min_ts = state_last

            # Decidir se precisa de atualização (novo contato ou contato existente sem os novos campos, ou com novas mensagens)
            needs_update = not exists
            is_stale = False
            if exists and existing_key:
                existing_data = personal_contacts[existing_key]
                old_defaults = [
                    "Conversa inicial.", "Conversa muito curta.",
                    "Conversa inicial de suporte/atendimento.", "Conversa inicial.",
                    "Pendente de classificação.",
                ]
                summary_val = existing_data.get("summary") or ""
                # Summaries gerados pelo extrator NL (update manual) também são considerados pendentes
                is_nl_generated_summary = (
                    summary_val.startswith("André atualiza") or
                    summary_val.startswith("Atualizar informações") or
                    summary_val == "Pendente de classificação."
                )
                has_old_default_summary = summary_val in old_defaults or is_nl_generated_summary

                # Verifica se houve novas mensagens desde a última classificação
                has_new_messages = False
                if "last_interaction" in existing_data:
                    if max_ts and max_ts > existing_data.get("last_interaction", 0):
                        has_new_messages = True
                else:
                    has_new_messages = True

                # force=True (sync manual) reclassifica qualquer contato com histórico no DB
                has_db_history = msg_count > 0
                if (force and has_db_history) or has_old_default_summary or has_new_messages or not existing_data.get("summary") or not existing_data.get("intent") or not existing_data.get("frequency"):
                    needs_update = True
                    is_stale = True
            
            if not needs_update:
                continue

            # Resolucao de nome: tenta bridge Baileys quando o nome for generico/ausente.
            # Isso preenche o "Contato {phone}" que aparecia para quem nao tem sender_name no log.
            bridge_name = None
            existing_name = personal_contacts.get(existing_key, {}).get("name") if existing_key else None
            if not name or (isinstance(name, str) and name.startswith("Contato ")):
                bridge_name = _resolve_contact_name_from_bridge(chat_id)
            best_name, name_source = _best_contact_name(chat_id, bridge_name, name, phone)
            if name_source == "bridge":
                logger.info(f"Nome resolvido via Baileys para {chat_id}: {best_name}")
            name = best_name

            if msg_count < min_msg_threshold:
                # Criar fallback direto sem gastar chamada de IA para conversas com pouquíssimas mensagens
                skipped_few_msgs += 1
                target_key = existing_key if existing_key else resolved_chat
                existing_data = personal_contacts.get(target_key, {})

                # Preservação/migração de manual_relationship (inclui @lid correspondente)
                man_rel = _resolve_man_rel(existing_data, personal_contacts)

                # Se for stale (antigo e incompleto), não reaproveitamos as propriedades padrão antigas
                rel_val = man_rel or ("Cliente" if is_stale else (existing_data.get("relationship") or "Cliente"))
                tone_val = "polido e profissional" if is_stale else (existing_data.get("tone") or "polido e profissional")
                guide_val = "Responda de forma prestativa." if is_stale else (existing_data.get("guidelines") or "Responda de forma prestativa.")
                
                existing_saved_name = existing_data.get("name") or ""
                _esn_norm = _normalize_text(existing_saved_name)
                _is_bad_name = (not existing_saved_name or re.match(r"^Contato\s+\d+$", existing_saved_name) or _esn_norm in {"andre alencar", "andré alencar", "andre", "andré"})
                resolved_name = (name if (_is_bad_name and name) else (None if _is_bad_name else existing_saved_name))
                personal_contacts[target_key] = {
                    "name": resolved_name,
                    "relationship": rel_val,
                    "manual_relationship": man_rel,
                    "lid": existing_data.get("lid"),
                    "notes": existing_data.get("notes"),
                    "product": existing_data.get("product"),
                    "tone": tone_val,
                    "nickname": existing_data.get("nickname"),
                    "pet_name": existing_data.get("pet_name"),
                    "frequent_greeting": existing_data.get("frequent_greeting"),
                    "summary": existing_data.get("summary") or "Conversa muito curta.",
                    "intent": existing_data.get("intent") or "Contato inicial.",
                    "frequency": existing_data.get("frequency") or "esporádica",
                    "guidelines": guide_val,
                    "last_interaction": max_ts or existing_data.get("last_interaction", 0)
                }
                continue

            if classification_count >= max_classifications:
                hit_limit = True
                skipped_due_to_limit += 1
                target_key = existing_key if existing_key else resolved_chat
                existing_data = personal_contacts.get(target_key, {})

                # Preservação/migração de manual_relationship (inclui @lid correspondente)
                man_rel = _resolve_man_rel(existing_data, personal_contacts)

                rel_val = man_rel or ("Cliente" if is_stale else (existing_data.get("relationship") or "Cliente"))
                tone_val = "polido e profissional" if is_stale else (existing_data.get("tone") or "polido e profissional")
                guide_val = "Responda de forma prestativa." if is_stale else (existing_data.get("guidelines") or "Responda de forma prestativa.")

                existing_saved_name = existing_data.get("name") or ""
                _esn_norm = _normalize_text(existing_saved_name)
                _is_bad_name = (not existing_saved_name or re.match(r"^Contato\s+\d+$", existing_saved_name) or _esn_norm in {"andre alencar", "andré alencar", "andre", "andré"})
                resolved_name = (name if (_is_bad_name and name) else (None if _is_bad_name else existing_saved_name))
                personal_contacts[target_key] = {
                    "name": resolved_name,
                    "relationship": rel_val,
                    "manual_relationship": man_rel,
                    "lid": existing_data.get("lid"),
                    "notes": existing_data.get("notes"),
                    "product": existing_data.get("product"),
                    "tone": tone_val,
                    "nickname": existing_data.get("nickname"),
                    "pet_name": existing_data.get("pet_name"),
                    "frequent_greeting": existing_data.get("frequent_greeting"),
                    "summary": existing_data.get("summary") or "Pendente de classificação.",
                    "intent": existing_data.get("intent") or "Contato recente.",
                    "frequency": existing_data.get("frequency") or "esporádica",
                    "guidelines": guide_val,
                    "last_interaction": max_ts or existing_data.get("last_interaction", 0)
                }
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
            chat_history = ""
            try:
                if conn is not None and chat_id in bridge_contacts:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT from_me, sender_name, body FROM messages
                        WHERE chat_id = ? AND body IS NOT NULL AND body != ''
                        ORDER BY timestamp DESC LIMIT 15
                    """, (chat_id,))
                    rows_msgs = cur.fetchall()
                    rows_msgs.reverse()
                    
                    history_lines = []
                    for f_me, s_name, msg_body in rows_msgs:
                        sender_lbl = "André" if f_me else (s_name or name or "Contato")
                        history_lines.append(f"[{sender_lbl}]: {msg_body}")
                    chat_history = "\n".join(history_lines)
                elif state_conn is not None:
                    # Fallback: state.db.messages (conteudo das sessoes)
                    cur = state_conn.cursor()
                    cur.execute("""
                        SELECT m.role, m.content FROM messages m
                        JOIN sessions s ON m.session_id = s.id
                        WHERE s.user_id = ? AND s.source = 'whatsapp' AND m.content IS NOT NULL
                        ORDER BY m.timestamp DESC LIMIT 15
                    """, (chat_id,))
                    rows_msgs = cur.fetchall()
                    rows_msgs.reverse()
                    history_lines = []
                    for role, content in rows_msgs:
                        sender_lbl = "André" if role == "assistant" else (name or "Contato")
                        history_lines.append(f"[{sender_lbl}]: {content[:300]}")
                    chat_history = "\n".join(history_lines)
            except Exception as db_err:
                logger.error(f"Erro ao ler histórico para {chat_id}: {db_err}")
                chat_history = ""
            
            db_contacts[chat_id] = {
                "name": name,
                "history": chat_history,
                "stats": stats_info,
                "existing_key": existing_key,
                "is_stale": is_stale,
                "max_ts": max_ts,  # propagado para o merge (bug-fix: evita usar variável de escopo outer)
            }
            classification_count += 1
        if conn is not None:
            conn.close()
        if state_conn is not None:
            state_conn.close()
    except Exception as e:
        return f"Erro ao ler banco de dados SQLite: {e}"

    # 3. Mesclar dados mantendo os já existentes com classificação inteligente via LLM
    # Paralelizar as chamadas ao LLM (I/O-bound) usando ThreadPoolExecutor
    updated = False
    added_count = 0
    contact_items = list(db_contacts.items())

    def _classify_item(item):
        """Classifica um item de db_contacts e retorna (chat_id, info, classification)."""
        chat_id, info = item
        classification = _classify_contact_via_llm(
            info["name"], info["history"], info["stats"]
        )
        return chat_id, info, classification

    max_workers = min(4, len(contact_items)) if contact_items else 1
    classified_results: list[tuple] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_classify_item, item): item[0] for item in contact_items}
        for future in as_completed(future_map):
            try:
                classified_results.append(future.result())
            except Exception as classify_err:
                chat_id_failed = future_map[future]
                logger.error(f"Erro ao classificar contato {chat_id_failed}: {classify_err}")

    for chat_id, info, classification in classified_results:
        name = info["name"]
        existing_key = info["existing_key"]
        is_stale = info.get("is_stale", False)
        max_ts = info.get("max_ts")  # propagado corretamente do dict

        resolved_chat = _resolve_phone_from_jid(chat_id)
        phone = resolved_chat.split("@")[0]
        target_key = existing_key if existing_key else resolved_chat
        existing_data = personal_contacts.get(target_key, {})

        if is_stale:
            man_rel = _resolve_man_rel(existing_data, personal_contacts)

            personal_contacts[target_key] = {
                "name": (name if (not existing_data.get("name") or re.match(r"^Contato\s+\d+$", existing_data.get("name") or "")) else existing_data.get("name")) or f"Contato {phone}",
                "relationship": man_rel or classification.get("relationship", "Cliente"),
                "manual_relationship": man_rel,
                "notes": existing_data.get("notes"),
                "product": existing_data.get("product") or classification.get("product"),
                "tone": classification.get("tone", "polido e profissional"),
                "nickname": existing_data.get("nickname") or classification.get("nickname"),
                "pet_name": existing_data.get("pet_name") or classification.get("pet_name"),
                "frequent_greeting": classification.get("frequent_greeting"),
                "summary": classification.get("summary", "Conversa inicial."),
                "full_summary": existing_data.get("full_summary"),
                "last_summarized_at": existing_data.get("last_summarized_at"),
                "intent": classification.get("intent", "Suporte/Atendimento."),
                "frequency": classification.get("frequency", "esporádica"),
                "guidelines": classification.get("guidelines", "Responda de forma prestativa."),
                "last_interaction": max_ts or existing_data.get("last_interaction", 0)
            }
        else:
            man_rel = _resolve_man_rel(existing_data, personal_contacts)

            personal_contacts[target_key] = {
                "name": (name if (not existing_data.get("name") or re.match(r"^Contato\s+\d+$", existing_data.get("name") or "")) else existing_data.get("name")) or f"Contato {phone}",
                "relationship": man_rel or existing_data.get("relationship") or classification.get("relationship", "Cliente"),
                "manual_relationship": man_rel,
                "notes": existing_data.get("notes"),
                "product": existing_data.get("product") or classification.get("product"),
                "tone": existing_data.get("tone") or classification.get("tone", "polido e profissional"),
                "nickname": existing_data.get("nickname") or classification.get("nickname"),
                "pet_name": existing_data.get("pet_name") or classification.get("pet_name"),
                "frequent_greeting": existing_data.get("frequent_greeting") or classification.get("frequent_greeting"),
                "summary": existing_data.get("summary") or classification.get("summary", "Conversa inicial."),
                "full_summary": existing_data.get("full_summary"),
                "last_summarized_at": existing_data.get("last_summarized_at"),
                "intent": existing_data.get("intent") or classification.get("intent", "Suporte/Atendimento."),
                "frequency": existing_data.get("frequency") or classification.get("frequency", "esporádica"),
                "guidelines": existing_data.get("guidelines") or classification.get("guidelines", "Responda de forma prestativa."),
                "last_interaction": max_ts or existing_data.get("last_interaction", 0)
            }
        added_count += 1
        updated = True

    # Atualizar full_summary para contatos com sessões novas
    full_summary_updated = _sync_full_summaries(
        personal_contacts=personal_contacts,
        state_db_path=state_db_path if state_db_path.exists() else None,
        max_contacts=max_classifications or 10,
    )
    if full_summary_updated > 0:
        updated = True
        logger.info(f"[sync] full_summary atualizado para {full_summary_updated} contato(s)")

    # Aprendizado de estilo de escrita
    _style_log = ""
    try:
        if _should_run_style_learning():
            logger.info("[style-learning] Novas mensagens detectadas, iniciando análise de estilo...")
            _messages_by_rel = _collect_andre_messages_by_relationship(personal_contacts)
            if _messages_by_rel:
                groups_info = ", ".join(f"{r}({len(m)})" for r, m in _messages_by_rel.items())
                logger.info(f"[style-learning] Grupos coletados: {groups_info}")
                # LLM gera só os padrões; Python garante os exemplos no formato correto
                _llm_patterns = _extract_style_patterns_via_llm(_messages_by_rel)
                _style_section = _build_style_section_with_patterns(_messages_by_rel, _llm_patterns)
                if _style_section:
                    if _update_soul_whatsapp_with_examples(_style_section):
                        logger.info("[style-learning] SOUL_WHATSAPP.md atualizado com exemplos reais de escrita.")
                        _style_log = "- 🧠 SOUL_WHATSAPP.md atualizado com padrões de escrita reais."
                    else:
                        logger.warning("[style-learning] Falha ao salvar SOUL_WHATSAPP.md.")
                        _style_log = "- ⚠️ Style learning: falha ao salvar SOUL_WHATSAPP.md."
            else:
                logger.warning("[style-learning] Nenhum grupo com mensagens suficientes encontrado.")
                _style_log = "- ⚠️ Style learning: sem mensagens classificadas suficientes."
        else:
            logger.info("[style-learning] Sem mensagens novas desde o último aprendizado, pulando.")
    except Exception as _sl_err:
        logger.warning(f"[style-learning] Erro inesperado, ignorando: {_sl_err}")
        _style_log = f"- ⚠️ Style learning: erro inesperado ({_sl_err})."

    # Preservar campos manuais do owner nos resultados classificados
    for target_key, contact_data in personal_contacts.items():
        for preserved_field in ("nickname", "pet_name", "notes", "manual_relationship", "full_summary", "last_summarized_at"):
            pass  # já preservados acima nas atribuições individuais

    # Preparar mensagem de resultado
    result_messages = []
    if updated or metadata_updated or skipped_few_msgs > 0 or skipped_due_to_limit > 0:
        # Salvar JSON localmente
        try:
            with open(pc_path, "w", encoding="utf-8") as f:
                json.dump(personal_contacts, f, indent=2, ensure_ascii=False)

            result_messages.append(f"Sucesso! Mapeados e mesclados {added_count + skipped_few_msgs + skipped_due_to_limit} contatos localmente.")
            if added_count > 0:
                result_messages.append(f"- {added_count} contatos classificados via IA.")
            if full_summary_updated > 0:
                result_messages.append(f"- {full_summary_updated} resumos de histórico atualizados.")
            if skipped_few_msgs > 0:
                result_messages.append(f"- {skipped_few_msgs} contatos curtos configurados com valores padrão.")
            if skipped_due_to_limit > 0:
                result_messages.append(f"- {skipped_due_to_limit} contatos adicionados pendentes de classificação (limite de IA atingido).")
            if hit_limit:
                result_messages.append(f"⚠️ Limite de {max_classifications} chamadas de IA atingido nesta execução. Os contatos restantes foram inseridos como pendentes e serão classificados dinamicamente.")
            if _style_log:
                result_messages.append(_style_log)
            result_str = "\n".join(result_messages)
        except Exception as e:
            return f"Erro ao salvar personal_contacts.json localmente: {e}"
    else:
        result_str = "Nenhum contato novo ou pendente encontrado para adicionar."
        if _style_log:
            result_str += f"\n{_style_log}"

    # 4. Sincronizar com GitHub
    config_repo = config.config_repo
    config_token = config.config_github_token
    setup_user = config.hermes_setup_github_user

    if config_repo and config_token:
        if "/" in config_repo:
            repo_parts = config_repo.split("/")
            repo_user = repo_parts[0]
            repo_name = repo_parts[1]
        else:
            repo_user = setup_user or "empreendedorserial"
            repo_name = config_repo

        try:
            content = pc_path.read_bytes()
            ok = _github_put_file(
                repo_user=repo_user,
                repo_name=repo_name,
                token=config_token,
                github_path="personal_contacts.json",
                content=content,
                commit_msg="Update personal_contacts.json from WhatsApp database history",
            )
            if ok:
                result_str += "\n✓ personal_contacts.json sincronizado com o GitHub com sucesso!"
            else:
                result_str += "\n⚠️ Falha ao sincronizar com GitHub."
        except Exception as e:
            result_str += f"\n⚠️ Falha ao sincronizar com GitHub: {e}"
    else:
        result_str += "\nℹ️ GitHub não configurado na stack, sincronizado apenas localmente."

    return result_str


# ── Style Learning ─────────────────────────────────────────────────────────────

_SOUL_LEARNING_STATE_PATH = Path("/opt/data/.hermes/soul_learning_state.json")
_SOUL_WHATSAPP_PATH = Path("/opt/data/SOUL_WHATSAPP.md")
_STYLE_SENTINEL = "## EXEMPLOS REAIS DE ESCRITA"
_MEDIA_FILTER_PREFIXES = ("<Media omitted>", "image omitted", "video omitted", "audio omitted", "sticker omitted")
_OWNER_NAME_NORMS = {"andre alencar", "andré alencar", "andre", "andré"}


def _should_run_style_learning() -> bool:
    """Retorna True se há mensagens novas do André desde o último aprendizado."""
    try:
        bridge_db = Path("/opt/data/.hermes/whatsapp_messages.db")
        state_db = Path("/opt/data/.hermes/state.db")

        if not bridge_db.exists() and not state_db.exists():
            logger.warning("[style-learning] Nenhum banco SQLite encontrado (bridge_db nem state.db), pulando.")
            return False

        last_run_ts = 0
        if _SOUL_LEARNING_STATE_PATH.exists():
            try:
                state = json.loads(_SOUL_LEARNING_STATE_PATH.read_text(encoding="utf-8"))
                last_run_ts = state.get("last_run_ts", 0)
            except Exception:
                last_run_ts = 0

        # Preferir bridge_db; fallback para state.db
        db_to_check = bridge_db if bridge_db.exists() else state_db
        from_me_query = (
            "SELECT MAX(timestamp) FROM messages WHERE from_me=1"
            if bridge_db.exists()
            else "SELECT MAX(timestamp) FROM messages WHERE role='user'"
        )

        with sqlite3.connect(str(db_to_check)) as conn:
            cur = conn.cursor()
            cur.execute(from_me_query)
            row = cur.fetchone()
            max_ts = row[0] if row and row[0] else 0

        if not bridge_db.exists():
            logger.info("[style-learning] whatsapp_messages.db ausente, usando state.db para checar timestamp.")

        should_run = max_ts > last_run_ts
        logger.info(f"[style-learning] max_ts={max_ts}, last_run_ts={last_run_ts}, vai rodar={should_run}")
        return should_run
    except Exception as e:
        logger.warning(f"[style-learning] Erro em _should_run_style_learning: {e}")
        return False


def _collect_andre_messages_by_relationship(
    personal_contacts: dict,
    limit_per_contact: int = 20,
) -> dict[str, list[str]]:
    """Coleta mensagens do André (from_me=1) agrupadas por relacionamento.

    Retorna dict vazio se nenhum banco disponível ou nenhum contato classificado.
    """
    try:
        bridge_db = Path("/opt/data/.hermes/whatsapp_messages.db")
        state_db = Path("/opt/data/.hermes/state.db")

        use_bridge = bridge_db.exists()
        use_state = not use_bridge and state_db.exists()

        if not use_bridge and not use_state:
            logger.warning("[style-learning] Nenhum banco disponível para coletar mensagens.")
            return {}

        # Reverse lookup: phone_norm → relationship e nome
        phone_to_rel: dict[str, str] = {}
        phone_to_name: dict[str, str] = {}
        # Mapa extra: raw prefix (LID ou telefone sem @) → rel/nome
        raw_to_rel: dict[str, str] = {}
        raw_to_name: dict[str, str] = {}
        _owner_name_norm = _normalize_text(config.whatsapp_owner_number or "")
        for key, data in personal_contacts.items():
            rel = data.get("manual_relationship") or data.get("relationship") or "Cliente"
            # Preferir nickname; usar name só se não for o nome do próprio André
            name = data.get("nickname") or data.get("name") or ""
            _name_norm = _normalize_text(name)
            if _name_norm in ("andre alencar", "andré alencar", "andre", "andré"):
                name = ""
            elif _name_norm.startswith("contato ") or _name_norm.startswith("usuario ") or _name_norm.startswith("desconhecido"):
                name = ""
            raw_prefix = key.split("@")[0]  # ex: "265231477510271" (lid) ou "5586..." (phone)
            phone_norm = _normalize_brazilian_phone("".join(c for c in raw_prefix if c.isdigit()))
            phone_to_rel[phone_norm] = rel
            raw_to_rel[raw_prefix] = rel
            if name:
                phone_to_name[phone_norm] = name
                raw_to_name[raw_prefix] = name

        result: dict[str, list[str]] = {}

        if use_bridge:
            owner_phone = _normalize_brazilian_phone(
                "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())
            )

            with sqlite3.connect(str(bridge_db)) as conn:
                cur = conn.cursor()

                # Cross-reference @lid → telefone via arquivos lid-mapping-{phone}.json da sessão
                lid_phone_map: dict[str, str] = {}
                import re as _re
                _session_dir = Path("/opt/data/.hermes/platforms/whatsapp/session")
                if _session_dir.exists():
                    for _f in _session_dir.iterdir():
                        _m = _re.match(r'^lid-mapping-(\d+)\.json$', _f.name)
                        if not _m:
                            continue
                        _phone = _m.group(1)
                        try:
                            import json as _json
                            _lid = _json.loads(_f.read_text()).strip().strip('"')
                            if _lid:
                                lid_phone_map[_lid] = _phone
                        except Exception:
                            pass
                # Fallback: sender_id das mensagens recebidas em chats @lid
                cur.execute("""
                    SELECT DISTINCT chat_id, sender_id FROM messages
                    WHERE from_me=0 AND chat_id LIKE '%@lid%'
                    AND sender_id IS NOT NULL
                    AND sender_id NOT LIKE '%@lid%'
                    AND sender_id NOT LIKE '%@g.us%'
                """)
                for _cid, _sid in cur.fetchall():
                    _lid = _cid.split("@")[0]
                    _phone = _sid.split("@")[0].split(":")[0]
                    if _phone and _phone.isdigit() and _lid not in lid_phone_map:
                        lid_phone_map[_lid] = _phone

                cur.execute(
                    """
                    SELECT chat_id, MAX(timestamp) as last_ts FROM messages
                    WHERE from_me=1 AND (sender_name IS NULL OR sender_name != 'Bot')
                    AND chat_id NOT LIKE '%@g.us%'
                    GROUP BY chat_id
                    ORDER BY last_ts DESC
                    """
                )
                chat_ids = [
                    row[0] for row in cur.fetchall()
                    if _normalize_brazilian_phone("".join(c for c in row[0].split("@")[0].split(":")[0] if c.isdigit())) != owner_phone
                ]

                # Mapa reverso: telefone → lid (para contatos @s.whatsapp.net cujo entry é @lid)
                _phone_to_lid = {v: k for k, v in lid_phone_map.items()}

                cutoff_ts = int(time.time()) - 90 * 24 * 3600
                total_manual = 0
                for chat_id in chat_ids:
                    raw = chat_id.split("@")[0].split(":")[0]
                    digits = "".join(c for c in raw if c.isdigit())
                    phone_norm = _normalize_brazilian_phone(digits)

                    # Resolver relacionamento com 4 estratégias
                    rel = raw_to_rel.get(raw)
                    contact_name = raw_to_name.get(raw)
                    # Estratégia 2: @lid chat → via lid_phone_map
                    if rel is None and "@lid" in chat_id:
                        _alt_phone = lid_phone_map.get(raw, "")
                        if _alt_phone:
                            _palt = _normalize_brazilian_phone("".join(c for c in _alt_phone if c.isdigit()))
                            rel = raw_to_rel.get(_alt_phone, phone_to_rel.get(_palt))
                            contact_name = raw_to_name.get(_alt_phone, phone_to_name.get(_palt))
                    # Estratégia 3: @s.whatsapp.net chat cujo entry é @lid → via phone_to_lid
                    # @lid entry tem precedência (mais específico e atualizado)
                    if "@lid" not in chat_id:
                        _lid_from_phone = _phone_to_lid.get(digits) or _phone_to_lid.get(phone_norm)
                        if _lid_from_phone:
                            _lid_rel = raw_to_rel.get(_lid_from_phone)
                            _lid_name = raw_to_name.get(_lid_from_phone)
                            if _lid_rel:
                                rel = _lid_rel
                            if _lid_name:
                                contact_name = _lid_name
                    # Estratégia 4: fallback pelo telefone normalizado
                    if rel is None:
                        rel = phone_to_rel.get(phone_norm, "Geral")
                    if contact_name is None:
                        contact_name = phone_to_name.get(phone_norm, rel)

                    # Buscar mensagens do André
                    cur.execute(
                        """
                        SELECT body, timestamp FROM messages
                        WHERE from_me=1 AND (sender_name IS NULL OR sender_name != 'Bot')
                        AND chat_id=? AND timestamp >= ?
                        AND body IS NOT NULL AND length(trim(body)) > 1
                        AND body NOT LIKE '<Media omitted>%'
                        AND body NOT LIKE '[image received]%'
                        AND body NOT LIKE '[audio received]%'
                        AND body NOT LIKE '[video received]%'
                        AND body NOT LIKE '[sticker received]%'
                        AND body NOT LIKE '[document received]%'
                        AND length(body) <= 300
                        ORDER BY timestamp DESC LIMIT ?
                        """,
                        (chat_id, cutoff_ts, 100),
                    )
                    andre_rows = cur.fetchall()

                    # Buscar mensagens do contato (janela estendida para pegar respostas)
                    cur.execute(
                        """
                        SELECT body, timestamp FROM messages
                        WHERE from_me=0 AND chat_id=? AND timestamp >= ?
                        AND body IS NOT NULL AND length(trim(body)) > 1
                        AND body NOT LIKE '<Media omitted>%' AND length(body) <= 300
                        """,
                        (chat_id, cutoff_ts - 86400),
                    )
                    contact_rows = cur.fetchall()

                    msgs = []
                    used_contact_ts: set = set()
                    used_contact_bodies: set = set()
                    for andre_msg, ts in andre_rows:
                        if any(andre_msg.lower().startswith(p.lower()) for p in _MEDIA_FILTER_PREFIXES):
                            continue
                        # Mensagem do contato mais próxima dentro de 24h
                        # (cada timestamp e cada conteúdo usado uma única vez)
                        candidates = sorted(
                            ((abs(cts - ts), cts, cb) for cb, cts in contact_rows
                             if abs(cts - ts) <= 86400
                             and cts not in used_contact_ts
                             and " ".join(cb.split()) not in used_contact_bodies),
                            key=lambda x: x[0],
                        )
                        contact_msg = None
                        if candidates:
                            _, nearest_cts, nearest_cb = candidates[0]
                            contact_msg = " ".join(nearest_cb.split())
                            used_contact_ts.add(nearest_cts)
                            used_contact_bodies.add(contact_msg)
                        msgs.append({"contact": contact_msg, "andre": andre_msg, "contact_name": contact_name})
                    if msgs:
                        total_manual += len(msgs)
                        result.setdefault(rel, []).extend(msgs)

                logger.info(f"[style-learning] {total_manual} mensagens manuais coletadas de {len(chat_ids)} chats. Grupos: {dict((r, len(m)) for r, m in result.items())}")

        elif use_state:
            logger.warning("[style-learning] whatsapp_messages.db ausente — impossível distinguir mensagens manuais do André. Style learning ignorado.")
            return {}

        # Cap de 100 por grupo (sample aleatório)
        import random
        for rel in result:
            if len(result[rel]) > 100:
                result[rel] = random.sample(result[rel], 100)

        # Remover grupos sem nenhuma mensagem
        filtered = {rel: msgs for rel, msgs in result.items() if len(msgs) >= 1}
        if len(filtered) < len(result):
            dropped = [r for r in result if r not in filtered]
            logger.info(f"[style-learning] Grupos descartados por estar vazios: {dropped}")
        return filtered

    except Exception as e:
        logger.warning(f"[style-learning] Erro em _collect_andre_messages_by_relationship: {e}")
        return {}


def _sanitize_sensitive(text: str) -> str | None:
    """Remove mensagens com dados sensíveis. Retorna None se deve ser descartada."""
    import re
    if not text:
        return None
    # Descartar mensagens com padrões sensíveis
    _SENSITIVE_PATTERNS = [
        r"\b\d{4,6}\b.*senha|senha.*\b\d{4,6}\b",   # senha + número
        r"senha|password|pin\b",                       # palavras de senha
        r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",            # CPF
        r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",      # CNPJ
        r"ag[eê]ncia\s*:?\s*\d{3,6}",                # agência bancária
        r"conta\s*:?\s*\d{4,}",                       # número de conta
        r"cart[aã]o\s*:?\s*[\d\s]{13,19}",           # número de cartão
        r"\b\d{13,19}\b",                              # número de cartão longo
        r"cvv|cvc\s*:?\s*\d{3}",                     # CVV
        r"saldo.*R\$\s*[\d.,]+",                      # saldo bancário
        r"R\$\s*[\d.,]{4,}",                          # valores altos (R$ 1.000+)
        r"chave\s+pix.*@|@.*chave\s+pix",            # chave pix com email
        r"token|código de verificação|código de acesso",  # tokens de auth
    ]
    text_lower = text.lower()
    for pattern in _SENSITIVE_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return None
    return text


def _build_style_section_with_patterns(messages_by_relationship: dict, llm_patterns: str | None) -> str:
    """Combina padrões do LLM com exemplos de diálogo gerados pelo Python.

    O LLM fornece análise de padrões; o Python garante o formato exato dos exemplos.
    """
    from datetime import datetime
    hoje = datetime.now().strftime("%d/%m/%Y")

    # Extrai padrões por relacionamento do output do LLM
    patterns_by_rel: dict[str, str] = {}
    if llm_patterns:
        current_rel = None
        current_lines: list[str] = []
        for line in llm_patterns.splitlines():
            if line.startswith("### "):
                if current_rel:
                    patterns_by_rel[current_rel] = "\n".join(current_lines).strip()
                current_rel = line[4:].strip()
                current_lines = []
            elif current_rel:
                current_lines.append(line)
        if current_rel:
            patterns_by_rel[current_rel] = "\n".join(current_lines).strip()

    lines = [
        _STYLE_SENTINEL,
        f"> Gerado automaticamente em {hoje}.\n",
    ]
    for rel, msgs in messages_by_relationship.items():
        lines.append(f"### {rel}")
        # Padrões do LLM (busca pelo nome exato ou prefixo)
        pattern_text = patterns_by_rel.get(rel, "")
        if not pattern_text:
            for k, v in patterns_by_rel.items():
                if k.lower().startswith(rel.lower()) or rel.lower().startswith(k.lower()):
                    pattern_text = v
                    break
        if pattern_text:
            lines.append(pattern_text)
        lines.append("")
        lines.append("**Exemplos reais de diálogos (copiados literalmente):**")
        for item in msgs:
            if isinstance(item, dict):
                andre_text = _sanitize_sensitive(item.get("andre", ""))
                if not andre_text:
                    continue
                contact_text = _sanitize_sensitive(item.get("contact") or "")
                label = item.get("contact_name") or rel
                if _normalize_text(label) in _OWNER_NAME_NORMS:
                    label = rel
                if contact_text:
                    lines.append(f'- {label}: "{contact_text}"')
                    lines.append(f'- André: "{andre_text}"')
                    lines.append("")
                else:
                    lines.append(f'- André: "{andre_text}"')
            else:
                sanitized = _sanitize_sensitive(item)
                if sanitized:
                    lines.append(f'- André: "{sanitized}"')
        lines.append("")

    return "\n".join(lines)


def _build_style_section_directly(messages_by_relationship: dict) -> str:
    """Gera a seção de exemplos reais diretamente, sem LLM.

    Inclui todas as mensagens coletadas como exemplos literais.
    Usado como fallback quando o LLM falha ou como complemento garantido.
    """
    from datetime import datetime
    hoje = datetime.now().strftime("%d/%m/%Y")

    lines = [
        _STYLE_SENTINEL,
        f"> Gerado automaticamente em {hoje}.\n",
    ]
    for rel, msgs in messages_by_relationship.items():
        lines.append(f"### {rel}")
        lines.append("**Exemplos reais de diálogos do André:**")
        for item in msgs:
            if isinstance(item, dict):
                andre_text = _sanitize_sensitive(item.get("andre", ""))
                if not andre_text:
                    continue
                contact_text = _sanitize_sensitive(item.get("contact") or "")
                label = item.get("contact_name") or rel
                if _normalize_text(label) in _OWNER_NAME_NORMS:
                    label = rel
                if contact_text:
                    lines.append(f'- {label}: "{contact_text}"')
                    lines.append(f'- André: "{andre_text}"')
                    lines.append("")
                else:
                    lines.append(f'- André: "{andre_text}"')
            else:
                sanitized = _sanitize_sensitive(item)
                if sanitized:
                    lines.append(f'- André: "{sanitized}"')
        lines.append("")

    return "\n".join(lines)


def _extract_style_patterns_via_llm(messages_by_relationship: dict) -> str | None:
    """Chama o LLM para extrair padrões de escrita por relacionamento.

    O LLM gera APENAS os padrões identificados (texto analítico).
    Os exemplos de diálogo são inseridos pelo Python com formato garantido.
    Retorna seção markdown pronta para inserção no SOUL_WHATSAPP.md, ou None em falha.
    """
    from datetime import datetime

    hoje = datetime.now().strftime("%d/%m/%Y")

    # Bloco de mensagens para o LLM analisar (sem pedir que ele as reproduza)
    sections = []
    for rel, msgs in messages_by_relationship.items():
        lines = []
        for item in msgs[:30]:
            if isinstance(item, dict):
                andre_text = _sanitize_sensitive(item.get("andre", ""))
                if not andre_text:
                    continue
                contact_text = _sanitize_sensitive(item.get("contact") or "")
                label = item.get("contact_name") or rel
                if _normalize_text(label) in _OWNER_NAME_NORMS:
                    label = rel
                if contact_text:
                    lines.append(f'{label}: "{contact_text}" / André: "{andre_text}"')
                else:
                    lines.append(f'André: "{andre_text}"')
            else:
                sanitized = _sanitize_sensitive(item)
                if sanitized:
                    lines.append(f'André: "{sanitized}"')
        sections.append(f"### {rel}\n" + "\n".join(lines))

    mensagens_block = "\n\n".join(sections)

    prompt = (
        "Você é um analista de estilo de escrita do WhatsApp.\n\n"
        "Abaixo estão mensagens REAIS enviadas por André Alencar, separadas por tipo de relacionamento.\n"
        "Sua tarefa é APENAS identificar e listar os padrões de escrita de André — NÃO reproduza as mensagens.\n\n"
        "Para cada grupo, retorne SOMENTE:\n"
        "### [Nome do relacionamento]\n"
        "**Padrões identificados:**\n"
        "- [padrão 1]\n"
        "- [padrão 2]\n"
        "- [padrão 3]\n\n"
        "Analise: abreviações usadas, gírias, emojis, pontuação, formalidade, comprimento das mensagens, "
        "tom (direto, amigável, técnico), perguntas abertas ou fechadas.\n"
        "Escreva em português brasileiro. Sem texto antes ou depois dos grupos.\n\n"
        "MENSAGENS POR RELACIONAMENTO:\n\n"
        f"{mensagens_block}"
    )

    google_key = config.google_api_key
    openai_key = config.openai_api_key
    openrouter_key = config.openrouter_api_key
    classify_model = config.whatsapp_contact_classifier_model

    extract_fn = lambda r: r["candidates"][0]["content"]["parts"][0]["text"]
    extract_fn_chat = lambda r: r["choices"][0]["message"]["content"]

    # 1. Gemini (texto livre, sem forçar JSON)
    if google_key:
        model_to_use = classify_model if (classify_model and "gemini" in classify_model.lower()) else "gemini-2.0-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent?key={google_key}"
        result = _call_llm_api(
            url,
            headers={"Content-Type": "application/json"},
            payload={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 2048},
            },
            extract_fn=extract_fn,
            timeout=25,
        )
        if result:
            return result.strip()

    # 2. OpenAI
    if openai_key:
        model_to_use = classify_model if (classify_model and any(p in classify_model.lower() for p in ["gpt", "o1-", "o3-"])) else "gpt-4o-mini"
        result = _call_llm_api(
            "https://api.openai.com/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openai_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2048},
            extract_fn=extract_fn_chat,
            timeout=25,
        )
        if result:
            return result.strip()

    # 3. OpenRouter
    if openrouter_key:
        model_to_use = classify_model if (classify_model and "/" in classify_model) else "google/gemini-2.0-flash-lite"
        result = _call_llm_api(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {openrouter_key}"},
            payload={"model": model_to_use, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2048},
            extract_fn=extract_fn_chat,
            timeout=25,
        )
        if result:
            return result.strip()

    return None


def _update_soul_whatsapp_with_examples(style_section: str) -> bool:
    """Injeta a seção de exemplos no SOUL_WHATSAPP.md e faz push para o GitHub.

    Preserva o conteúdo original da persona — só substitui/adiciona a seção sentinel.
    Retorna True se o arquivo local foi salvo com sucesso.
    """
    try:
        if not _SOUL_WHATSAPP_PATH.exists():
            logger.warning("[style-learning] SOUL_WHATSAPP.md não encontrado, abortando update.")
            return False

        original = _SOUL_WHATSAPP_PATH.read_text(encoding="utf-8")

        # Garantir que a seção não começa com o sentinel duplicado
        section_body = style_section
        if section_body.startswith(_STYLE_SENTINEL):
            section_body = section_body[len(_STYLE_SENTINEL):].lstrip("\n")

        # Splice: substituir se existir, senão adicionar ao final
        sentinel_pos = original.find(_STYLE_SENTINEL)
        if sentinel_pos != -1:
            base = original[:sentinel_pos].rstrip()
        else:
            base = original.rstrip()

        updated = f"{base}\n\n{_STYLE_SENTINEL}\n{section_body}"
        _SOUL_WHATSAPP_PATH.write_text(updated, encoding="utf-8")

        # Atualizar arquivo de estado
        try:
            bridge_db = Path("/opt/data/.hermes/whatsapp_messages.db")
            max_ts = 0
            if bridge_db.exists():
                with sqlite3.connect(str(bridge_db)) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT MAX(timestamp) FROM messages WHERE from_me=1")
                    row = cur.fetchone()
                    max_ts = row[0] if row and row[0] else 0
            _SOUL_LEARNING_STATE_PATH.write_text(
                json.dumps({"last_run_ts": int(time.time()), "last_message_ts": max_ts}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[style-learning] Falha ao salvar estado: {e}")

        # Push para GitHub
        config_repo = config.config_repo
        config_token = config.config_github_token
        if config_repo and config_token:
            if "/" in config_repo:
                repo_parts = config_repo.split("/")
                repo_user, repo_name = repo_parts[0], repo_parts[1]
            else:
                repo_user = config.hermes_setup_github_user or "empreendedorserial"
                repo_name = config_repo

            from datetime import datetime
            commit_date = datetime.now().strftime("%Y-%m-%d")
            try:
                ok = _github_put_file(
                    repo_user=repo_user,
                    repo_name=repo_name,
                    token=config_token,
                    github_path="SOUL_WHATSAPP.md",
                    content=updated.encode("utf-8"),
                    commit_msg=f"[auto] Update SOUL_WHATSAPP.md style examples - {commit_date}",
                )
                if not ok:
                    logger.warning("[style-learning] Falha ao fazer push de SOUL_WHATSAPP.md para o GitHub.")
            except Exception as e:
                logger.warning(f"[style-learning] Erro no push do GitHub: {e}")

        return True

    except Exception as e:
        logger.warning(f"[style-learning] Erro em _update_soul_whatsapp_with_examples: {e}")
        return False


def _github_put_file(
    repo_user: str,
    repo_name: str,
    token: str,
    github_path: str,
    content: bytes,
    commit_msg: str,
    branch: str = "main",
    timeout: int = 10,
) -> bool:
    """Sobe um arquivo para o GitHub via API REST (GET sha → PUT content).

    Args:
        repo_user: Dono do repositório (ex: "empreendedorserial").
        repo_name: Nome do repositório.
        token: Token de acesso pessoal do GitHub.
        github_path: Caminho do arquivo no repositório (ex: "personal_contacts.json").
        content: Conteúdo binário do arquivo.
        commit_msg: Mensagem de commit.
        branch: Branch de destino (padrão: "main").
        timeout: Timeout em segundos para cada requisição HTTP.

    Returns:
        True se criado/atualizado com sucesso, False caso contrário.
    """
    content_b64 = base64.b64encode(content).decode("utf-8")
    file_url = f"https://api.github.com/repos/{repo_user}/{repo_name}/contents/{github_path}"
    base_headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Hermes-Agent-Plugin",
    }

    # 1. Obter SHA atual (necessário para atualizar arquivo existente)
    sha = None
    try:
        req_get = urllib.request.Request(file_url, headers=base_headers)
        with urllib.request.urlopen(req_get, timeout=timeout) as resp:
            sha = json.loads(resp.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning(f"_github_put_file: erro ao buscar SHA de {github_path}: {e}")
    except Exception as e:
        logger.warning(f"_github_put_file: erro inesperado buscando SHA: {e}")

    # 2. Criar ou atualizar o arquivo (retry em caso de 409 — SHA desatualizado)
    for attempt in range(3):
        put_data: dict = {"message": commit_msg, "content": content_b64, "branch": branch}
        if sha:
            put_data["sha"] = sha
        try:
            req_put = urllib.request.Request(
                file_url,
                data=json.dumps(put_data).encode("utf-8"),
                headers={**base_headers, "Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req_put, timeout=timeout) as resp:
                return resp.status in [200, 201]
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < 2:
                logger.warning(f"_github_put_file: 409 Conflict (tentativa {attempt + 1}), rebuscando SHA...")
                time.sleep(1 + attempt)
                try:
                    req_get = urllib.request.Request(file_url, headers=base_headers)
                    with urllib.request.urlopen(req_get, timeout=timeout) as resp:
                        sha = json.loads(resp.read().decode("utf-8")).get("sha")
                except Exception:
                    pass
                continue
            logger.error(f"_github_put_file: falha ao enviar {github_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"_github_put_file: falha ao enviar {github_path}: {e}")
            return False
    return False


def _find_contact_matches(identifier: str) -> list[tuple[str, str, str]]:
    """Retorna lista de (key, display_name, phone) que batem com o identifier por nome/apelido.

    Usado para detectar ambiguidade antes de chamar _update_contact_fields.
    Não busca por número (quando identifier é número, é inequívoco).
    """
    pc_path = Path("/opt/data/personal_contacts.json")
    if not pc_path.exists():
        return []
    try:
        personal_contacts = json.loads(pc_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    owner_phone = "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())

    def _is_owner(key: str) -> bool:
        phone = key.split("@")[0]
        return bool(owner_phone) and (phone == owner_phone or _normalize_brazilian_phone(phone) == _normalize_brazilian_phone(owner_phone))

    id_norm = _normalize_text(identifier)
    matches: list[tuple[str, str, str]] = []

    for key, data in personal_contacts.items():
        if _is_owner(key):
            continue
        name = data.get("name") or ""
        nick = data.get("nickname") or ""
        pet = data.get("pet_name") or ""
        phone = key.split("@")[0].split(":")[0]
        display = name or nick or phone

        name_norm = _normalize_text(name)
        nick_norm = _normalize_text(nick)
        pet_norm = _normalize_text(pet)

        # Exact match em qualquer campo de nome
        if id_norm in (name_norm, nick_norm, pet_norm):
            matches.append((key, display, phone))
        # Substring match em name (ex: "Pedro" encontra "Pedro Alves")
        elif name_norm and id_norm in name_norm:
            matches.append((key, display, phone))

    return matches


def _update_contact_fields(identifier: str, fields: dict) -> str:
    """Atualiza campos específicos de um contato em personal_contacts.json pelo nome ou número.

    identifier: nome, apelido, pet_name ou número de telefone (parcial aceito)
    fields: dict com os campos a atualizar (ex: {"relationship": "Filho", "notes": "..."})
    Retorna string de resultado para exibir ao owner.
    """
    pc_path = Path("/opt/data/personal_contacts.json")
    if not pc_path.exists():
        return "❌ personal_contacts.json não encontrado."

    try:
        with open(str(pc_path), "r", encoding="utf-8") as f:
            personal_contacts = json.load(f)
    except Exception as e:
        return f"❌ Erro ao ler personal_contacts.json: {e}"

    id_norm = _normalize_text(identifier)
    matched_key = None

    # Número do owner — nunca deve ser alvo de update via comando
    owner_phone = "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())

    def _is_owner_key(key: str) -> bool:
        phone = key.split("@")[0]
        return bool(owner_phone) and (phone == owner_phone or _normalize_brazilian_phone(phone) == _normalize_brazilian_phone(owner_phone))

    # 1. Busca exata por número/JID (apenas se identifier parece ser um número)
    if re.match(r"^\+?[\d\s\-]+$", identifier):
        id_digits = id_norm.replace(" ", "").replace("-", "")
        id_norm_br = _normalize_brazilian_phone(id_digits)
        for key in personal_contacts:
            if _is_owner_key(key):
                continue
            phone = key.split("@")[0].split(":")[0]
            phone_norm_br = _normalize_brazilian_phone(phone)
            if id_digits in phone or phone in id_digits or id_norm_br == phone_norm_br:
                matched_key = key
                break

    # 2. Match exato de name (prioridade máxima)
    if not matched_key:
        for key, data in personal_contacts.items():
            if _is_owner_key(key):
                continue
            if _normalize_text(data.get("name") or "") == id_norm:
                matched_key = key
                break

    # 3. Match exato de nickname ou pet_name
    if not matched_key:
        for key, data in personal_contacts.items():
            if _is_owner_key(key):
                continue
            for field in ["nickname", "pet_name"]:
                if _normalize_text(data.get(field) or "") == id_norm:
                    matched_key = key
                    break
            if matched_key:
                break

    # 4. Match parcial (substring) em name — fallback
    if not matched_key:
        best_score = 0
        for key, data in personal_contacts.items():
            if _is_owner_key(key):
                continue
            name_norm = _normalize_text(data.get("name") or "")
            if name_norm and id_norm in name_norm:
                score = len(name_norm)
                if score > best_score:
                    matched_key = key
                    best_score = score

    # 5. Busca por sender_name no whatsapp_messages.db (contatos com nome genérico "Contato XXXX")
    if not matched_key:
        bridge_db = Path("/opt/data/.hermes/whatsapp_messages.db")
        if bridge_db.exists():
            try:
                with sqlite3.connect(str(bridge_db)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT chat_id, MAX(sender_name) as name
                        FROM messages
                        WHERE chat_id NOT LIKE '%@g.us%'
                        GROUP BY chat_id
                        """,
                    )
                    all_rows = cur.fetchall()
                    logger.info(
                        f"[update-contact] Passo 5: buscando '{identifier}' entre {len(all_rows)} chat_ids no DB. "
                        f"sender_names não-nulos: {[r[1] for r in all_rows if r[1]][:10]}"
                    )
                    for chat_id_row, sender_name in all_rows:
                        if _is_owner_key(chat_id_row):
                            continue
                        sn_norm = _normalize_text(sender_name or "")
                        # Match: id_norm é substring do sender_name OU sender_name é substring do id_norm
                        if sn_norm and (id_norm in sn_norm or sn_norm in id_norm):
                            logger.info(f"[update-contact] Passo 5: match sender_name='{sender_name}' chat_id={chat_id_row}")
                            phone_row = chat_id_row.split("@")[0]
                            for key in personal_contacts:
                                if _is_owner_key(key):
                                    continue
                                if key.split("@")[0] == phone_row:
                                    matched_key = key
                                    break
                            if not matched_key:
                                matched_key = chat_id_row if "@" in chat_id_row else f"{phone_row}@s.whatsapp.net"
                                personal_contacts[matched_key] = {
                                    "name": sender_name,
                                    "relationship": "Cliente",
                                    "manual_relationship": None,
                                    "notes": None,
                                    "product": None,
                                    "tone": "polido e profissional",
                                    "nickname": None,
                                    "pet_name": None,
                                    "frequent_greeting": None,
                                    "summary": "Pendente de classificação.",
                                    "intent": "Contato inicial.",
                                    "frequency": "esporádica",
                                    "guidelines": "Responda de forma prestativa.",
                                    "last_interaction": time.time(),
                                }
                                logger.info(f"[update-contact] Criada entrada para {sender_name} ({matched_key}) via DB lookup")
                            break
            except sqlite3.Error as e:
                logger.warning(f"[update-contact] Erro ao buscar sender_name no DB: {e}")

    # 6. Busca pelo nome no store de contatos do Baileys via bridge /contacts/search
    if not matched_key:
        try:
            search_url = f"{BRIDGE_URL}/contacts/search?name={urllib.parse.quote(identifier, safe='')}"
            with urllib.request.urlopen(search_url, timeout=5) as resp:
                search_result = json.loads(resp.read().decode())
            bridge_results = search_result.get("results", [])
            logger.info(f"[update-contact] Passo 6: bridge retornou {len(bridge_results)} resultado(s) para '{identifier}'")

            # Filtrar owner e entradas sem jid
            valid_results = [
                e for e in bridge_results
                if e.get("jid") and not _is_owner_key(e.get("jid", ""))
            ]

            # Passar 1: tentar match em personal_contacts existente
            for entry in valid_results:
                jid = entry.get("jid", "")
                real_name = entry.get("name", "")
                phone_row = jid.split("@")[0]
                for key in personal_contacts:
                    if _is_owner_key(key):
                        continue
                    if key.split("@")[0] == phone_row:
                        logger.info(f"[update-contact] Passo 6: match existente '{real_name}' → {key}")
                        matched_key = key
                        break
                if matched_key:
                    break

            # Passar 2: nenhum match existente — escolher o resultado com nome mais próximo
            if not matched_key and valid_results:
                id_lower = identifier.lower()

                def _name_score(e):
                    n = (e.get("name") or "").lower()
                    if not n:
                        return 0
                    if n == id_lower:
                        return 3
                    if id_lower in n or n in id_lower:
                        return 2
                    # Quantidade de palavras em comum
                    common = set(id_lower.split()) & set(n.split())
                    return len(common)

                best = max(valid_results, key=_name_score)
                best_jid = best.get("jid", "")
                best_name = best.get("name", "")
                best_phone = best_jid.split("@")[0]
                matched_key = best_jid if "@" in best_jid else f"{best_phone}@s.whatsapp.net"
                personal_contacts[matched_key] = {
                    "name": best_name,
                    "relationship": "Cliente",
                    "manual_relationship": None,
                    "notes": None, "product": None,
                    "tone": "polido e profissional",
                    "nickname": None, "pet_name": None,
                    "frequent_greeting": None,
                    "summary": "Pendente de classificação.",
                    "intent": "Contato inicial.",
                    "frequency": "esporádica",
                    "guidelines": "Responda de forma prestativa.",
                    "last_interaction": time.time(),
                }
                logger.info(f"[update-contact] Passo 6: nova entrada criada para '{best_name}' ({matched_key}) — melhor match de {len(valid_results)} resultados")
        except Exception as e:
            logger.warning(f"[update-contact] Passo 6: erro ao consultar bridge: {e}")

    if not matched_key:
        return f"❌ Contato '{identifier}' não encontrado em personal_contacts.json nem no histórico de mensagens."

    contact = personal_contacts[matched_key]
    contact_name = contact.get("name") or contact.get("nickname") or matched_key
    logger.info(f"[update-contact] '{identifier}' → matched_key={matched_key} name='{contact_name}' fields={list(fields.keys())}")

    # Campos protegidos que não podem ser sobrescritos por este comando
    protected = {"last_interaction"}
    updated_fields = []
    for field, value in fields.items():
        if field in protected:
            continue
        contact[field] = value
        updated_fields.append(field)

    if not updated_fields:
        return f"⚠️ Nenhum campo válido para atualizar em '{contact_name}'."

    personal_contacts[matched_key] = contact

    # Atualizar entrada espelhada (@lid ↔ @s.whatsapp.net) com os mesmos campos
    mirror_key = None
    if "@lid" in matched_key:
        # Procurar @s.whatsapp.net que aponte para este @lid (via campo 'lid')
        lid_val = matched_key
        for k, v in personal_contacts.items():
            if "@s.whatsapp.net" in k and isinstance(v, dict) and v.get("lid") == lid_val:
                mirror_key = k
                break
    elif "@s.whatsapp.net" in matched_key:
        # Usar campo 'lid' para encontrar a entrada @lid correspondente
        lid_val = contact.get("lid")
        if lid_val and lid_val in personal_contacts:
            mirror_key = lid_val
    if mirror_key:
        mirror = personal_contacts[mirror_key]
        for field, value in fields.items():
            if field not in protected:
                mirror[field] = value
        personal_contacts[mirror_key] = mirror
        logger.info(f"[update-contact] espelhado em mirror_key={mirror_key}")
    else:
        logger.warning(f"[update-contact] sem mirror encontrado para matched_key={matched_key} (campo lid='{contact.get('lid')}')")

    try:
        with open(str(pc_path), "w", encoding="utf-8") as f:
            json.dump(personal_contacts, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"❌ Erro ao salvar personal_contacts.json: {e}"

    # Push para GitHub em background
    try:
        threading.Thread(target=_push_personal_contacts_to_github, daemon=True).start()
    except Exception:
        pass

    fields_str = ", ".join(f"`{k}`: {v!r}" for k, v in fields.items() if k not in protected)
    return f"✅ Contato *{contact_name}* ({matched_key}) atualizado.\nCampos: {fields_str}"


def _push_personal_contacts_to_github() -> bool:
    """Envia o arquivo personal_contacts.json local diretamente para o repositório do GitHub."""
    pc_path = Path("/opt/data/personal_contacts.json")
    if not pc_path.exists():
        return False

    config_repo = config.config_repo
    config_token = config.config_github_token
    setup_user = config.hermes_setup_github_user

    if not config_repo or not config_token:
        return False

    if "/" in config_repo:
        repo_parts = config_repo.split("/")
        repo_user = repo_parts[0]
        repo_name = repo_parts[1]
    else:
        repo_user = setup_user or "empreendedorserial"
        repo_name = config_repo

    try:
        content = pc_path.read_bytes()
        ok = _github_put_file(
            repo_user=repo_user,
            repo_name=repo_name,
            token=config_token,
            github_path="personal_contacts.json",
            content=content,
            commit_msg="Manual/Agent update of personal_contacts.json",
        )
        if ok:
            logger.info("✓ personal_contacts.json sincronizado com o GitHub com sucesso via push detectado.")
        return ok
    except Exception as e:
        logger.error(f"Falha ao sincronizar personal_contacts.json manual com o GitHub: {e}")
    return False



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

    logger.info("📦 Instalando libs Google API no venv...")
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
            logger.info("✅ Libs Google API instaladas com sucesso.")
        else:
            logger.error(f"Falha ao instalar libs Google: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"Erro ao instalar libs Google: {e}")


def _pull_and_merge_configurations():
    """Baixa as configurações do repositório privado do GitHub do cliente e faz merge com o local."""
    # Atualizar mapa de LIDs no início da puxada periódica
    try:
        _check_bot_paused()
    except Exception:
        pass

    config_repo = config.config_repo
    config_token = config.config_github_token
    setup_user = config.hermes_setup_github_user
    dev_user = config.dev_github_user

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
                    logger.info(f"✓ {path_str} atualizado do GitHub.")
        except Exception as e:
            logger.error(f"Falha ao baixar {path_str} de {url}: {e}")

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
        logger.error(f"Falha ao copiar personas para perfis locais: {copy_err}")

    # 2. Sincronizar personal_contacts.json (merge)
    personal_contacts_path = Path("/opt/data/personal_contacts.json")
    local_contacts = {}
    if personal_contacts_path.exists():
        try:
            with open(personal_contacts_path, "r", encoding="utf-8") as f:
                local_contacts = json.load(f)
                for k, v in local_contacts.items():
                    if isinstance(v, dict):
                        local_contacts[k] = _sanitize_classification_result(v)
        except Exception as e:
            logger.error(f"Erro ao carregar local personal_contacts.json: {e}")

    remote_url = f"{config_base_url}/personal_contacts.json"
    remote_contacts = None
    try:
        req = urllib.request.Request(remote_url)
        if config_token:
            req.add_header("Authorization", f"token {config_token}")
        req.add_header("User-Agent", "Hermes-Agent-Plugin")
        with urllib.request.urlopen(req, timeout=10) as response:
            remote_contacts = json.loads(response.read().decode("utf-8"))
            if isinstance(remote_contacts, dict):
                for k, v in remote_contacts.items():
                    if isinstance(v, dict):
                        remote_contacts[k] = _sanitize_classification_result(v)
            logger.info(f"✓ personal_contacts.json remoto carregado com sucesso.")
    except Exception as e:
        logger.warning(f"Não foi possível baixar personal_contacts.json do GitHub: {e}")

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
            logger.info(f"✓ Contatos mesclados localmente.")
        except Exception as e:
            logger.error(f"Erro ao salvar personal_contacts.json mesclado: {e}")


def _self_update_plugin_code() -> bool:
    """Atualiza o código do plugin a partir do repositório Git. Retorna True se houve mudanças no próprio plugin."""
    github_user = config.github_user
    code_token = config.dev_github_token

    raw_root = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main"
    plugin_dir = Path("/opt/data/.hermes/plugins/whatsapp-manager")

    # NUNCA usar Path(__file__).parent como fallback — isso gravaria dentro do
    # repositório git do container e quebraria o git pull do Hermes.
    # Se o plugin_dir não existir, criar ele. Se não conseguir, abortar.
    if not plugin_dir.exists():
        try:
            plugin_dir.mkdir(parents=True, exist_ok=True)
        except Exception as mkdir_err:
            logger.info(f"Code Update: Não foi possível criar plugin_dir: {mkdir_err}. Abortando update.")
            return False

    if (plugin_dir / ".git").exists():
        try:
            import subprocess
            git_url = f"https://github.com/{github_user}/hermes-whatsapp-mixed.git"
            
            # Fetch origin main using the token header if available
            fetch_cmd = ["git"]
            if code_token:
                fetch_cmd.extend(["-c", f"http.extraHeader=Authorization: token {code_token}"])
            fetch_cmd.extend(["fetch", git_url, "main"])
            
            subprocess.run(fetch_cmd, cwd=str(plugin_dir), check=True, capture_output=True)
            
            local_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(plugin_dir), check=True, capture_output=True, text=True).stdout.strip()
            remote_hash = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], cwd=str(plugin_dir), check=True, capture_output=True, text=True).stdout.strip()
            
            if local_hash != remote_hash:
                # Reset local modifications to avoid merge conflicts
                subprocess.run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=str(plugin_dir), check=True, capture_output=True)
                logger.info(f"Code Update (Git): Código atualizado via Git para o commit {remote_hash[:7]}.")
                return True
            else:
                logger.info("Code Update (Git): Sem novas atualizações no Git.")
                return False
        except Exception as git_err:
            logger.error(f"Code Update (Git): Falha ao atualizar via Git: {git_err}. Tentando fallback por downloads individuais...")
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
                    # Normaliza line endings para comparação (evita loop por CRLF vs LF)
                    content_normalized = content.replace(b"\r\n", b"\n")
                    local_normalized = local_path.read_bytes().replace(b"\r\n", b"\n") if local_path.exists() else b""
                    if local_normalized != content_normalized:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        local_path.write_bytes(content_normalized)
                        logger.info(f"Code Update: {filename} atualizado com sucesso.")
                        if filename in ["whatsapp_manager.py", "bridge.js"]:
                            updated_any = True
        except Exception as e:
            logger.error(f"Code Update: Falha ao atualizar {filename}: {e}")

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
                    content_normalized = content.replace(b"\r\n", b"\n")
                    local_normalized = local_path.read_bytes().replace(b"\r\n", b"\n") if local_path.exists() else b""
                    if local_normalized != content_normalized:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        local_path.write_bytes(content_normalized)
                        logger.info(f"Code Update: {relative_path} atualizado.")
        except Exception as e:
            logger.error(f"Code Update: Falha ao atualizar skill {relative_path}: {e}")

    return updated_any


# ---------------------------------------------------------------------------
# Helpers extraídos de pre_llm_call() — testáveis unitariamente
# ---------------------------------------------------------------------------

def _resolve_chat_id(sender_id: str) -> str:
    """Resolve o chat_id canônico a partir de um sender_id (JID ou LID).

    Retorna o JID limpo sem device-suffix (ex: "5511999@s.whatsapp.net"),
    consultando o mapa _sender_to_chat preenchido pelo pre_gateway_dispatch.
    """
    chat_id = _sender_to_chat.get(sender_id, "")
    if not chat_id and sender_id:
        parts = sender_id.split("@")
        if len(parts) == 2:
            jid_part, domain_part = parts
            chat_id = f"{jid_part.split(':')[0]}@{domain_part}"
    return chat_id


_CONTACT_QUERY_PATTERNS = [
    r"conversa\w*\s+com\s+([A-ZÀ-Úa-zà-ú]{2,})",
    r"histórico\s+d[eo]\s+([A-ZÀ-Úa-zà-ú]{2,})",
    r"o\s+que\s+([A-ZÀ-Úa-zà-ú]{2,})\s+(?:disse|falou|mandou|perguntou|escreveu)",
    r"(?:falar|falei|falaste|fale)\s+com\s+([A-ZÀ-Úa-zà-ú]{2,})",
    r"mensagens?\s+d[eo]\s+([A-ZÀ-Úa-zà-ú]{2,})",
    r"([A-ZÀ-Úa-zà-ú]{2,})\s+(?:me\s+)?(?:mandou|disse|perguntou|falou|escreveu)",
    r"acessa\w*\s+(?:a\s+)?conversa\w*\s+(?:com\s+)?(?:a\s+|o\s+)?([A-ZÀ-Úa-zà-ú]{2,})",
]

# Pronomes e palavras comuns que não são nomes de contato
_CONTACT_QUERY_STOPWORDS = {
    "ela", "ele", "eles", "elas", "dele", "dela", "deles", "delas",
    "você", "voce", "me", "mim", "nos", "nós", "lhe", "lhes",
    "isso", "este", "este", "essa", "essa", "aquele", "aquela",
    "qual", "que", "quem", "como", "quando", "onde", "porque",
    "mais", "menos", "muito", "pouco", "tudo", "nada", "algo",
    "hoje", "ontem", "amanhã", "agora", "antes", "depois",
    # palavras comuns após preposições que não são nomes
    "minha", "meu", "meus", "minhas", "sua", "seu", "seus", "suas",
    "informacoes", "informações", "dados", "contato", "contatos",
    "perfil", "registro", "sistema", "banco", "arquivo",
    "filha", "filho", "filhos", "filhas", "mae", "pai", "irmao", "irma",
    "amigo", "amiga", "cliente", "vendedor", "parente",
    "nome", "apelido", "numero", "telefone", "relacao", "relacionamento",
    "pois", "para", "com", "por", "mas", "sim", "nao",
}


def _normalize_text(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def _detect_contact_query(text: str) -> str | None:
    for pattern in _CONTACT_QUERY_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) >= 2 and _normalize_text(candidate) not in _CONTACT_QUERY_STOPWORDS:
                return candidate
    return None


def _search_contact_by_name(query: str) -> tuple[str | None, dict | None]:
    personal_contacts = _load_personal_contacts()
    query_norm = _normalize_text(query)
    best_key, best_data, best_score = None, None, 0
    for key, data in personal_contacts.items():
        for field in ["name", "nickname", "pet_name"]:
            value = data.get(field) or ""
            value_norm = _normalize_text(value)
            if value_norm and (query_norm in value_norm or value_norm in query_norm):
                score = len(value_norm)
                if score > best_score:
                    best_key, best_data, best_score = key, data, score
    return best_key, best_data


def _fetch_cross_session_history(phone: str, limit: int = 30) -> str:
    rows: list = []

    bridge_db = Path("/opt/data/.hermes/whatsapp_messages.db")
    if bridge_db.exists():
        try:
            with sqlite3.connect(str(bridge_db)) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT from_me, sender_name, body, timestamp
                    FROM messages
                    WHERE chat_id LIKE ? AND body IS NOT NULL AND body != ''
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (f"{phone}%", limit),
                )
                rows = cur.fetchall()
        except sqlite3.Error as e:
            logger.warning(f"[cross-session] Erro ao ler whatsapp_messages.db: {e}")

    if not rows:
        state_db = Path("/opt/data/.hermes/state.db")
        if state_db.exists():
            try:
                with sqlite3.connect(str(state_db)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT m.role, NULL, m.content, m.timestamp
                        FROM messages m JOIN sessions s ON m.session_id = s.id
                        WHERE s.user_id LIKE ? AND s.source = 'whatsapp'
                        AND m.content IS NOT NULL
                        ORDER BY m.timestamp DESC LIMIT ?
                        """,
                        (f"{phone}%", limit),
                    )
                    rows = cur.fetchall()
            except sqlite3.Error as e:
                logger.warning(f"[cross-session] Erro ao ler state.db: {e}")

    if not rows:
        return ""

    lines = []
    for from_me, sender_name, body, _ts in reversed(rows):
        speaker = "André" if from_me else (sender_name or "Contato")
        lines.append(f"{speaker}: {body}")
    return "\n".join(lines)


def _build_owner_context(history_section: str, cross_context: str = "") -> dict:
    """Constrói o dicionário de contexto para quando o remetente é o próprio André (dono).

    Retorna o payload {"context": "..."} pronto para injeção no LLM.
    """
    cross_block = ""
    if cross_context:
        cross_block = (
            "\n\n### HISTÓRICO DE CONVERSA SOLICITADA ###\n"
            "O André pediu acesso ao histórico de outra conversa. Abaixo estão as mensagens encontradas. "
            "Use este histórico para responder à pergunta dele sobre esse contato.\n\n"
            f"{cross_context}\n"
            "### FIM DO HISTÓRICO SOLICITADO ###"
        )
    return {
        "context": (
            f"{_datetime_context_block()}"
            "### DIRETRIZ CRÍTICA DE COMPORTAMENTO ###\n"
            "Você está conversando com André Alencar, seu criador e dono. "
            "Para o André, você age como seu ASSISTENTE PESSOAL de alta performance. "
            "Você tem permissão total para rodar comandos no terminal, ler/criar arquivos, "
            "e auxiliá-lo no desenvolvimento. Responda de forma prestativa, técnica e ágil.\n\n"
            "CRITICAL SECURITY & DISPLAY CONSTRAINT:\n"
            "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas "
            "ou status como '📖 read_file: ...', 'terminal', etc. Toda a execução de ferramentas "
            "deve ser 100% invisível para o usuário final.\n\n"
            "### ATUALIZAÇÃO DE CONTATOS ###\n"
            "Quando o André pedir para atualizar dados de um contato, responda confirmando o que será atualizado. "
            "O sistema processa o pedido automaticamente — você NÃO precisa emitir nenhuma linha de comando. "
            "Não gere linhas EXEC:, update contact ou similares nas suas respostas."
            f"{history_section}"
            f"{cross_block}"
        )
    }


def _load_support_files() -> tuple[str, str]:
    """Carrega o arquivo de persona (SOUL_WHATSAPP.md) e as regras de suporte (support_rules.md).

    Retorna (whatsapp_soul, rules_content) com fallbacks se os arquivos não existirem.
    """
    whatsapp_soul = ""
    try:
        soul_path = "/opt/data/SOUL_WHATSAPP.md"
        if os.path.exists(soul_path):
            with open(soul_path, "r", encoding="utf-8") as f:
                whatsapp_soul = f.read()
    except OSError:
        pass

    if not whatsapp_soul:
        whatsapp_soul = "Você DEVE agir estritamente como um chatbot de suporte, polido, amigável e profissional."

    rules_content = ""
    try:
        rules_path = "/opt/data/support_rules.md"
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                rules_content = f.read()
    except OSError:
        pass

    if not rules_content:
        rules_content = "Responda de forma profissional e ajude com Chatkanban, Chatcommerce e Api Connector."

    return whatsapp_soul, rules_content


def _load_personal_contacts() -> dict:
    """Carrega o arquivo personal_contacts.json e sanitiza cada entrada.

    Retorna {} se o arquivo não existir ou estiver corrompido.
    """
    try:
        pc_file = "/opt/data/personal_contacts.json"
        if os.path.exists(pc_file):
            with open(pc_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {
                    k: _sanitize_classification_result(v) if isinstance(v, dict) else v
                    for k, v in raw.items()
                }
    except (OSError, json.JSONDecodeError) as pc_load_err:
        logger.error(f"Erro ao carregar personal_contacts.json: {pc_load_err}")
    return {}


def _datetime_context_block() -> str:
    """Retorna bloco com data/hora atual, dia da semana e tipo de dia para injetar no contexto do LLM."""
    from datetime import datetime as _dt
    import os as _os
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        tz_name = _os.getenv("TZ", "America/Sao_Paulo")
        now = _dt.now(_ZoneInfo(tz_name))
    except Exception:
        now = _dt.now()
    weekday_names = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    weekday = weekday_names[now.weekday()]
    is_weekend = now.weekday() >= 5
    _feriados_fixos = {"01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-25"}
    today_mmdd = now.strftime("%m-%d")
    is_holiday = today_mmdd in _feriados_fixos
    day_type = "feriado" if is_holiday else ("fim de semana" if is_weekend else "dia útil")
    return (
        f"### DATA E HORA ATUAL ###\n"
        f"{weekday}, {now.strftime('%d/%m/%Y')} — {now.strftime('%H:%M')} ({day_type})\n"
        f"### FIM DATA E HORA ###\n\n"
    )


def _owner_status_context_block(reveal_status: bool = True) -> str:
    """Retorna bloco de instrução sobre o status atual do dono para injetar no contexto do LLM.

    reveal_status=True  → amigos/parentes: pode revelar o que André está fazendo se perguntado
    reveal_status=False → clientes/desconhecidos: só diz que está ocupado, sem detalhes
    """
    status = _get_active_owner_status()
    if not status:
        return ""

    from datetime import datetime as _dt
    owner_name = config.whatsapp_owner_name or "André"
    description = status.get("description", "ocupado")
    until_iso = status.get("until_iso")
    until_str = ""
    if until_iso:
        try:
            until_str = f" até as {_dt.fromisoformat(until_iso).strftime('%H:%M')}"
        except Exception:
            pass

    if reveal_status:
        status_detail = f"{owner_name} está {description}{until_str} e não pode responder agora."
        reveal_instruction = (
            f"Se perguntarem onde {owner_name} está ou por que demora a responder, você pode mencionar que ele está {description}{until_str}. "
            f"Mas NUNCA revele isso espontaneamente — só se a pessoa perguntar diretamente."
        )
    else:
        status_detail = f"{owner_name} está temporariamente indisponível{until_str}."
        reveal_instruction = (
            f"NUNCA mencione o status ou indisponibilidade espontaneamente. Responda normalmente como se fosse o próprio {owner_name}. "
            f"SOMENTE se a pessoa perguntar explicitamente 'você é um bot?', 'é uma IA?', 'é robô?' ou similar, "
            f"confirme discretamente que é o assistente de {owner_name} e que ele está indisponível no momento{until_str}."
        )

    return (
        f"\n### STATUS ATUAL DO DONO ###\n"
        f"{status_detail}\n"
        f"{reveal_instruction}\n"
        f"### FIM DO STATUS ###\n\n"
    )


def _build_personal_prompt(contact_info: dict, relationship: str, history_section: str) -> dict:
    """Constrói o payload de contexto para contatos pessoais (Amigo, Parente, etc.).

    Inclui nome, relacionamento, tom, apelidos, saudação frequente e diretrizes.
    Retorna {"context": "..."}.
    """
    name = contact_info.get("name", "Contato Pessoal")
    tone = contact_info.get("tone", "informal e amigável")
    guidelines = contact_info.get("guidelines", "Responda como André.")

    nickname = contact_info.get("nickname")
    pet_name = contact_info.get("pet_name")
    frequent_greeting = contact_info.get("frequent_greeting")
    summary = contact_info.get("summary")
    intent = contact_info.get("intent")
    frequency = contact_info.get("frequency")
    notes = contact_info.get("notes")
    product = contact_info.get("product")

    details = ""
    if nickname:
        details += f"Apelido do contato: {nickname}\n"
    if pet_name:
        details += f"Nome carinhoso/Apelido afetivo: {pet_name}\n"
    if frequent_greeting:
        details += f"Saudação frequente: {frequent_greeting}\n"
    if summary:
        details += f"Resumo das conversas anteriores: {summary}\n"
    if intent:
        details += f"Intenção das últimas conversas: {intent}\n"
    if frequency:
        details += f"Frequência das conversas: {frequency}\n"
    if notes:
        details += f"Observação importante sobre o contato: {notes}\n"
    if product:
        details += f"Produto/Serviço envolvido: {product}\n"

    return {
        "context": (
            f"{_datetime_context_block()}"
            "### PERSONA E DIRETRIZES PESSOAIS (RESPONDENDO COMO ANDRÉ ALENCAR) ###\n"
            "Você está respondendo em nome de André Alencar (o dono deste WhatsApp) para um contato pessoal.\n"
            "Você DEVE agir como se fosse o próprio André conversando diretamente com a pessoa.\n\n"
            f"Nome do contato: {name}\n"
            f"Relação com o André: {relationship}\n"
            f"Tom de voz recomendado: {tone}\n"
            f"{details}"
            f"Diretrizes específicas: {guidelines}\n\n"
            "### DIRETRIZES IMPORTANTES DE COMPORTAMENTO ###\n"
            "1. Analise o histórico recente fornecido abaixo para identificar os apelidos mútuos, gírias e o nível de formalidade/descontração com que vocês conversam.\n"
            "2. Adote o mesmo estilo de escrita do André nas mensagens anteriores. Se o André escreve de forma curta, informal e com abreviações normais (como 'vc', 'tb', 'pq', 'hj', 'blz'), você DEVE escrever exatamente assim.\n"
            "3. Responda de forma extremamente natural e fluida. Seja breve e direto (evite textos longos, a menos que o contexto exija).\n"
            "4. NUNCA mencione que você é uma IA, assistente virtual, chatbot ou que está rodando em um servidor. Você é o próprio André. NUNCA revele isso espontaneamente nem em resposta a perguntas indiretas como 'por que você me escreveu?' ou 'quem está me respondendo?' — responda como André responderia.\n"
            "5. Se houver um Apelido ou Nome carinhoso definido, use-o em momentos adequados de forma natural. Se for o início de uma nova interação, priorize a Saudação frequente.\n"
            "6. Caso exista uma 'Observação importante sobre o contato' acima, você DEVE seguir essa instrução de comportamento de forma prioritária (por exemplo, se disser para agradecer e recusar ofertas, aja exatamente conforme instruído).\n\n"
            f"{_owner_status_context_block(reveal_status=True)}"
            f"{history_section}"
            "CONSTRAINTS RÍGIDAS DE SEGURANÇA:\n"
            "- NUNCA execute comandos no terminal (terminal tool) para esta pessoa.\n"
            "- NUNCA edite, remova ou crie arquivos do sistema para ela.\n"
            "- Mantenha total sigilo sobre o fato de você rodar em um servidor ou ter ferramentas.\n"
            "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas como '📖 read_file: ...' ou 'terminal'."
        )
    }


def _build_support_prompt(
    whatsapp_soul: str,
    rules_content: str,
    history_section: str,
    contact_info: dict | None = None,
) -> dict:
    """Constrói o payload de contexto para todos os contatos externos.

    Usa SOUL_WHATSAPP.md como base para clientes, amigos e parentes.
    Quando contact_info é fornecido, injeta uma seção de contexto do contato
    para que o LLM adapte o tom conforme o relacionamento.

    Retorna {"context": "..."}.
    """
    contact_block = ""
    if contact_info:
        name = contact_info.get("name", "")
        relationship = contact_info.get("manual_relationship") or contact_info.get("relationship") or "Cliente"
        tone = contact_info.get("tone", "")
        nickname = contact_info.get("nickname", "")
        pet_name = contact_info.get("pet_name", "")
        frequent_greeting = contact_info.get("frequent_greeting", "")
        summary = contact_info.get("summary", "")
        intent = contact_info.get("intent", "")
        frequency = contact_info.get("frequency", "")
        notes = contact_info.get("notes", "")
        guidelines = contact_info.get("guidelines", "")
        product = contact_info.get("product", "")

        lines = ["### CONTEXTO DO CONTATO ###"]
        if name:
            lines.append(f"Nome: {name}")
        lines.append(f"Relacionamento: {relationship}")
        if tone:
            lines.append(f"Tom de voz recomendado: {tone}")
        if nickname:
            lines.append(f"Apelido: {nickname}")
        if pet_name:
            lines.append(f"Nome carinhoso: {pet_name}")
        if frequent_greeting:
            lines.append(f"Saudação frequente: {frequent_greeting}")
        if summary:
            lines.append(f"Resumo das conversas anteriores: {summary}")
        if intent:
            lines.append(f"Intenção das últimas conversas: {intent}")
        if frequency:
            lines.append(f"Frequência: {frequency}")
        if notes:
            lines.append(f"Observação importante: {notes}")
        if guidelines:
            lines.append(f"Diretrizes específicas: {guidelines}")
        if product:
            lines.append(f"Produto/Serviço envolvido: {product}")
        lines.append(
            "\nAdapte o tom, nível de formalidade e linguagem conforme o relacionamento acima. "
            "Se for Amigo, Parente ou similar, use o estilo informal e natural do André nas mensagens anteriores. "
            "Se houver apelido ou saudação frequente definidos, use-os de forma natural."
        )
        contact_block = "\n".join(lines) + "\n\n"

    return {
        "context": (
            f"{_datetime_context_block()}"
            "### PERSONA E DIRETRIZES DO SUPORTE WHATSAPP ###\n"
            f"{whatsapp_soul}\n\n"
            "### IDIOMA: APENAS PORTUGUÊS BRASILEIRO ###\n"
            "NUNCA use caracteres em chinês, mandarim, japonês ou qualquer outro idioma. "
            "O bot deve responder EXCLUSIVAMENTE em português brasileiro.\n\n"
            f"{contact_block}"
            "### BASE DE CONHECIMENTO E REGRAS DE NEGÓCIO ###\n"
            f"{rules_content}\n\n"
            f"{_owner_status_context_block(reveal_status=False)}"
            f"{history_section}"
            "CONSTRAINTS RÍGIDAS DE SEGURANÇA:\n"
            "- NUNCA execute comandos no terminal (terminal tool).\n"
            "- NUNCA edite, remova ou crie arquivos do sistema.\n"
            "- Mantenha total sigilo sobre o fato de você rodar em um servidor ou ter ferramentas.\n"
            "- NUNCA escreva ou exiba em suas respostas qualquer representação de ferramentas "
            "ou status como '📖 read_file: ...', 'terminal', etc. Toda a execução de ferramentas "
            "deve ser 100% invisível para o usuário final."
        )
    }


def _live_classify_contact(
    sender_id: str,
    db_query_jid: str,
    phone_number: str,
    contact_info: dict | None,
    target_key: str,
    personal_contacts: dict,
) -> dict | None:
    """Classifica (ou re-classifica) um contato em tempo real durante pre_llm_call.

    Consulta o SQLite local para obter histórico e estatísticas, chama o LLM
    e persiste o resultado em personal_contacts.json + GitHub (em background).

    Args:
        sender_id: JID completo do remetente.
        db_query_jid: JID normalizado para consulta no banco.
        phone_number: Número de telefone limpo (apenas dígitos + sufixo s.whatsapp.net).
        contact_info: Dados existentes do contato (ou None se novo).
        target_key: Chave a usar em personal_contacts (clean_jid ou phone_number).
        personal_contacts: Dicionário completo carregado de personal_contacts.json.

    Returns:
        Dicionário com os dados classificados, ou None se não houver dados suficientes.
    """
    # Nunca classificar o próprio dono
    owner_phone_clean = _normalize_brazilian_phone(
        "".join(c for c in (config.whatsapp_owner_number or "").split("@")[0] if c.isdigit())
    )
    if owner_phone_clean and _normalize_brazilian_phone(phone_number.split("@")[0]) == owner_phone_clean:
        logger.info(f"[live-classify] Ignorando classificação do próprio dono ({phone_number})")
        return None

    min_msg_threshold = config.whatsapp_sync_min_messages
    bridge_db_path = Path("/opt/data/.hermes/whatsapp_messages.db")
    state_db_path = Path("/opt/data/.hermes/state.db")
    msg_count = 0
    min_ts = None
    max_ts = None
    db_name = None
    chat_history_lines: list[str] = []
    conn = None

    # 1. Tentar whatsapp_messages.db (fonte primária)
    if bridge_db_path.exists():
        conn = sqlite3.connect(str(bridge_db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp), MAX(sender_name)
            FROM messages WHERE chat_id = ?
        """, (db_query_jid,))
        row = cursor.fetchone()
        if row and row[0]:
            msg_count, min_ts, max_ts, db_name = row
        if (not msg_count) and phone_number:
            cursor.execute("""
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp), MAX(sender_name)
                FROM messages WHERE chat_id LIKE ?
            """, (f"{phone_number}%",))
            fetched = cursor.fetchone()
            if fetched and fetched[0]:
                msg_count, min_ts, max_ts, db_name = fetched
        if not msg_count:
            cursor.execute("""
                SELECT from_me, sender_name, body FROM messages
                WHERE chat_id = ? AND body IS NOT NULL AND body != ''
                ORDER BY timestamp DESC LIMIT 15
            """, (db_query_jid,))
            rows_msgs = cursor.fetchall()
            rows_msgs.reverse()
            for f_me, s_name, msg_body in rows_msgs:
                sender_lbl = "André" if f_me else (s_name or "Contato")
                chat_history_lines.append(f"[{sender_lbl}]: {msg_body}")

    # 2. Fallback: state.db (sessions + messages do gateway)
    if (not msg_count) and state_db_path.exists():
        try:
            state_conn = sqlite3.connect(str(state_db_path))
            sc = state_conn.cursor()
            sc.execute("""
                SELECT COUNT(*), MAX(started_at) FROM sessions
                WHERE source = 'whatsapp' AND user_id = ?
            """, (db_query_jid,))
            row = sc.fetchone()
            if row and row[0]:
                msg_count = row[0]
                max_ts = row[1] or max_ts
            sc.execute("""
                SELECT m.role, m.content FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ? AND s.source = 'whatsapp' AND m.content IS NOT NULL
                ORDER BY m.timestamp DESC LIMIT 15
            """, (db_query_jid,))
            rows_msgs = sc.fetchall()
            rows_msgs.reverse()
            for role, content in rows_msgs:
                sender_lbl = "André" if role == "assistant" else (db_name or "Contato")
                chat_history_lines.append(f"[{sender_lbl}]: {(content or '')[:300]}")
            state_conn.close()
        except Exception as state_err:
            logger.warning(f"live sync: erro lendo state.db: {state_err}")

    if not msg_count:
        return None

    # 3. Montar stats e histórico
    stats_info = f"Total messages: {msg_count}."
    if min_ts and max_ts:
        try:
            first_date = datetime.datetime.fromtimestamp(min_ts).strftime('%Y-%m-%d')
            last_date = datetime.datetime.fromtimestamp(max_ts).strftime('%Y-%m-%d')
            stats_info += f" First message date: {first_date}. Last message date: {last_date}."
        except (ValueError, OSError):
            pass

    name = (contact_info.get("name") if contact_info else None) or db_name or f"Contato {phone_number}"

    # 4. Classificar (ou reusar se poucas mensagens)
    if msg_count < min_msg_threshold:
        prev_rel = contact_info.get("relationship") if contact_info else None
        if prev_rel and prev_rel not in ["Cliente", "Vendedor", "Pendente de classificação"]:
            classification = {
                "relationship": prev_rel,
                "tone": contact_info.get("tone", "informal e amigável"),
                "nickname": contact_info.get("nickname"),
                "pet_name": contact_info.get("pet_name"),
                "frequent_greeting": contact_info.get("frequent_greeting"),
                "summary": contact_info.get("summary", "Conversa muito curta."),
                "intent": "Contato inicial.",
                "frequency": contact_info.get("frequency", "esporádica"),
                "guidelines": contact_info.get("guidelines", "Responda como André."),
            }
        else:
            classification = {
                "relationship": "Cliente",
                "tone": "polido e profissional",
                "nickname": None, "pet_name": None,
                "frequent_greeting": None,
                "summary": "Conversa muito curta.",
                "intent": "Contato inicial.",
                "frequency": "esporádica",
                "guidelines": "Responda de forma prestativa.",
            }
    else:
        if conn:
            cursor.execute("""
                SELECT from_me, sender_name, body FROM messages
                WHERE chat_id = ? AND body IS NOT NULL AND body != ''
                ORDER BY timestamp DESC LIMIT 15
            """, (db_query_jid,))
            rows_msgs = cursor.fetchall()
            rows_msgs.reverse()
            history_lines = [
                f"[{'André' if f_me else (s_name or name or 'Contato')}]: {msg_body}"
                for f_me, s_name, msg_body in rows_msgs
            ]
            chat_history = "\n".join(history_lines)
        else:
            chat_history = "\n".join(chat_history_lines)
        classification = _classify_contact_via_llm(name, chat_history, stats_info)

    if conn is not None:
        conn.close()

    # 5. Mesclar com dados existentes preservando campos manuais
    man_rel = (contact_info.get("manual_relationship") if contact_info else None)
    if not man_rel and contact_info and contact_info.get("relationship") in ["Vendedor", "Amigo", "AmigoProximo", "Parente", "Filho"]:
        man_rel = contact_info.get("relationship")

    new_data = {
        "name": name,
        "relationship": man_rel or classification.get("relationship", "Cliente"),
        "manual_relationship": man_rel,
        "notes": contact_info.get("notes") if contact_info else None,
        "product": (contact_info.get("product") if contact_info else None) or classification.get("product"),
        "tone": classification.get("tone", "polido e profissional"),
        "nickname": classification.get("nickname"),
        "pet_name": classification.get("pet_name"),
        "frequent_greeting": classification.get("frequent_greeting"),
        "summary": classification.get("summary", "Conversa inicial."),
        "intent": classification.get("intent", "Suporte/Atendimento."),
        "frequency": classification.get("frequency", "esporádica"),
        "guidelines": classification.get("guidelines", "Responda de forma prestativa."),
        "last_interaction": time.time(),
    }

    # 6. Persistir localmente
    personal_contacts[target_key] = new_data
    try:
        with open("/opt/data/personal_contacts.json", "w", encoding="utf-8") as f:
            json.dump(personal_contacts, f, indent=2, ensure_ascii=False)
    except OSError as write_err:
        logger.error(f"Erro ao gravar personal_contacts.json no live sync: {write_err}")

    # 7. Push ao GitHub em background
    def _push_bg():
        try:
            config_repo = config.config_repo
            config_token = config.config_github_token
            setup_user = config.hermes_setup_github_user
            dev_user = config.dev_github_user
            if config_repo and config_token:
                repo_user, repo_name = (
                    config_repo.split("/") if "/" in config_repo
                    else (setup_user or dev_user or "empreendedorserial", config_repo)
                )
                _github_put_file(
                    repo_user=repo_user, repo_name=repo_name, token=config_token,
                    github_path="personal_contacts.json",
                    content=Path("/opt/data/personal_contacts.json").read_bytes(),
                    commit_msg=f"Live update personal_contacts.json for {name}",
                )
        except Exception as push_err:
            logger.error(f"Erro no push do live sync para o GitHub: {push_err}")

    threading.Thread(target=_push_bg, daemon=True).start()
    return new_data


def commit_file_to_repo(repo_user, repo_name, config_token, local_path, github_path, default_url):
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
            logger.error(f"Erro ao baixar template {github_path}: {dl_err}")

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
                    logger.info(f"✓ Arquivo '{github_path}' inicializado no repositório.")
        except Exception as put_err:
            logger.error(f"Erro ao commitar {github_path}: {put_err}")


def _transcribe_outgoing_audio(event, media_info: dict) -> None:
    """Transcreve áudios enviados pelo André e persiste no banco.

    Permite que o style learning capture mensagens de voz do André como texto.
    """
    try:
        transcription = _process_media_message(event)
        if not transcription:
            return

        display_text = f'[Áudio: "{transcription}"]'

        event.text = display_text
        if hasattr(event, "body"):
            event.body = display_text
        for attr in ["raw", "raw_event", "payload", "data"]:
            if hasattr(event, attr):
                val = getattr(event, attr)
                if isinstance(val, dict):
                    val["body"] = display_text
                    val["text"] = display_text

        db_path = Path("/opt/data/.hermes/whatsapp_messages.db")
        if db_path.exists() and media_info.get("message_id"):
            _persist_transcription_to_db(str(db_path), media_info["message_id"], display_text)

        logger.info(f"[audio-out] Áudio enviado transcrito: {transcription[:80]}...")
    except Exception as e:
        logger.warning(f"[audio-out] Erro ao transcrever áudio enviado: {e}")


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


    # Processamento de Mídia (Áudio e Imagem) via Gemini
    media_info = _get_media_info(event)
    if media_info["has_media"] and media_info["media_urls"]:
        media_type = media_info["media_type"]
        if media_type in ["ptt", "audio", "image"]:
            result_text = _process_media_message(event)
            if result_text:
                if media_type in ["ptt", "audio"]:
                    display_text = f'[Áudio: "{result_text}"]'
                else:
                    display_text = f'[Imagem: {result_text}]'
                
                # Atualizar o evento em memória
                event.text = display_text
                if hasattr(event, "body"):
                    event.body = display_text
                for attr in ["raw", "raw_event", "payload", "data"]:
                    if hasattr(event, attr):
                        val = getattr(event, attr)
                        if isinstance(val, dict):
                            val["body"] = display_text
                            val["text"] = display_text
                
                # Atualizar o banco SQLite local do Hermes em background
                db_path = Path("/opt/data/.hermes/whatsapp_messages.db")
                if db_path.exists() and media_info["message_id"]:
                    _persist_transcription_to_db(str(db_path), media_info["message_id"], display_text)


    # Identificar remetente (com resolução de LID para número de telefone clássico)
    sender_id = event.source.user_id or ""
    resolved_sender = _resolve_phone_from_jid(sender_id)
    clean_sender = "".join(c for c in resolved_sender.split("@")[0].split(":")[0] if c.isdigit())

    # Identificar dono (André)
    owner_number = config.whatsapp_owner_number
    if not owner_number:
        return None  # Não definido → plugin não faz nada

    clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())
    is_owner = (_normalize_brazilian_phone(clean_sender) == _normalize_brazilian_phone(clean_owner))

    # Transcrever áudios ENVIADOS pelo André para enriquecer o style learning
    if is_owner and media_info["has_media"] and media_info["media_type"] in ["ptt", "audio"]:
        _transcribe_outgoing_audio(event, media_info)
    elif is_owner and not media_info["has_media"]:
        # Fallback: bridge pode não setar has_media para from_me=1; detectar pelo texto placeholder
        _raw_text = (getattr(event, "text", "") or "").strip()
        if _raw_text in ("[audio received]", "[ptt received]"):
            # Tentar obter caminho do áudio direto do evento (raw/payload)
            _audio_path = None
            for _attr in ["raw", "raw_event", "payload", "data"]:
                _raw_val = getattr(event, _attr, None)
                if isinstance(_raw_val, dict):
                    _urls = _raw_val.get("mediaUrls") or _raw_val.get("media_urls") or []
                    if isinstance(_urls, list) and _urls:
                        _audio_path = _urls[0]
                        break
                    elif isinstance(_urls, str) and _urls:
                        _audio_path = _urls
                        break
            # Fallback: arquivo mais recente no cache
            if not _audio_path:
                _audio_cache = Path("/opt/data/.hermes/audio_cache")
                if _audio_cache.exists():
                    _audio_files = sorted(_audio_cache.glob("aud_*.ogg"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if _audio_files:
                        _audio_path = str(_audio_files[0])
            if _audio_path and Path(_audio_path).exists():
                # Setar atributos no evento para _get_media_info() os encontrar
                event.has_media = True
                event.media_type = "ptt"
                event.media_urls = [_audio_path]
                media_info["has_media"] = True
                media_info["media_type"] = "ptt"
                media_info["media_urls"] = [_audio_path]
                logger.info(f"[audio-out] Transcrição via fallback: {Path(_audio_path).name}")
                _transcribe_outgoing_audio(event, media_info)

    # Identificar chat
    chat_id = str(event.source.chat_id) if event.source.chat_id else ""
    resolved_chat = _resolve_phone_from_jid(chat_id)
    clean_chat = "".join(c for c in resolved_chat.split("@")[0].split(":")[0] if c.isdigit())
    is_self_chat = (clean_sender == clean_chat)

    # Detectar from_me via raw_message do evento (campo correto no Hermes)
    _raw_msg = getattr(event, "raw_message", None) or {}
    if isinstance(_raw_msg, str):
        try:
            import ast as _ast
            _raw_msg = _ast.literal_eval(_raw_msg)
        except Exception:
            _raw_msg = {}
    _is_from_me = bool(_raw_msg.get("fromMe") or _raw_msg.get("from_me"))

    # Persistir mensagens manuais do André no SQLite (Hermes não grava from_me=1 automaticamente)
    # Nota: para from_me=1, sender_id==chat_id, então is_self_chat seria True erroneamente.
    # Usamos _is_from_me + verificar que não é self-chat pelo chat_id (@g.us excluído também)
    _is_group = "@g.us" in chat_id
    _chat_phone = "".join(c for c in chat_id.split("@")[0].split(":")[0] if c.isdigit())
    _owner_phone_clean = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())
    _is_real_self_chat = _normalize_brazilian_phone(_chat_phone) == _normalize_brazilian_phone(_owner_phone_clean)
    if _is_from_me and not _is_group and not _is_real_self_chat and chat_id:
        _ts = getattr(event, "timestamp", None)
        if hasattr(_ts, "timestamp"):
            _ts = int(_ts.timestamp())
        else:
            _ts = int(_ts) if _ts else int(time.time())
        _msg_id = (
            _raw_msg.get("messageId")
            or media_info.get("message_id")
            or getattr(event, "message_id", None)
            or f"owner_{chat_id}_{_ts}"
        )
        _persist_owner_message_to_db(
            chat_id=chat_id,
            message_id=_msg_id,
            body=(event.text or "").strip(),
            timestamp=_ts,
            sender_name="André Alencar",
        )

    msg_text = (event.text or "").strip()

    # Comando para sincronizar e importar contatos do SQLite para personal_contacts.json e GitHub
    normalized_msg = msg_text.strip().lower().replace("_", " ").replace("-", " ")
    try:
        logger.info(
            f"[debug] sender='{sender_id}' (clean='{clean_sender}', norm='{_normalize_brazilian_phone(clean_sender)}')"
            f" owner='{owner_number}' (clean='{clean_owner}', norm='{_normalize_brazilian_phone(clean_owner)}')"
            f" is_owner={is_owner} msg='{msg_text}' normalized='{normalized_msg}'"
        )
    except Exception as log_e:
        logger.error(f"Erro ao gravar debug log: {log_e}")
    # Cartão de contato compartilhado pelo owner — guardar pendência para próximo comando
    if is_owner and "[CONTACT_CARD:" in msg_text:
        card_start = msg_text.index("[CONTACT_CARD:")
        card_end = msg_text.index("]", card_start)
        card_content = msg_text[card_start + len("[CONTACT_CARD:"): card_end].strip()
        # Pode haver múltiplos cartões separados por ';'
        first_card = card_content.split(";")[0].strip()
        parts = first_card.split("|")
        card_name = parts[0].strip() if len(parts) > 0 else ""
        card_phone = parts[1].strip() if len(parts) > 1 else ""
        if card_phone or card_name:
            _pending_contact_card[sender_id] = {"name": card_name, "phone": card_phone}
            logger.info(f"[contact-card] Cartão guardado: name='{card_name}' phone='{card_phone}'")
        return {"action": "skip", "reason": "contact-card-stored"}

    sync_keywords = [
        "sync contacts", "sync contatos", "sincronizar contatos",
        "sincronize contatos", "sincronize os contatos", "sincronizar os contatos",
        "importar contatos", "atualizar contatos", "atualize contatos",
        "atualize os contatos", "atualizar os contatos",
    ]

    is_sync_cmd = is_owner and any(kw in normalized_msg for kw in sync_keywords)

    if is_owner and is_sync_cmd:
        logger.info("Comando de sincronização detectado (forçando atualização).")
        chat_id = str(event.source.chat_id) if event.source.chat_id else ""

        if _sync_running.is_set():
            response_msg = "⏳ Sincronização já em andamento. Aguarde a conclusão."
        else:
            _run_sync_in_background(force=True, chat_id=chat_id)
            response_msg = "⏳ Sincronização iniciada em segundo plano. Você será notificado quando concluir."
        
        # Enviar de volta
        if chat_id:
            try:
                url = f"{BRIDGE_URL}/send"
                payload = json.dumps({
                    "chatId": chat_id,
                    "message": response_msg
                }).encode("utf-8")
                req = urllib.request.Request(url, data=payload, method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pass
            except Exception as send_err:
                logger.error(f"Erro ao enviar resposta do comando: {send_err}")
        
        return {"action": "skip", "reason": "sync-contacts-command"}

    # Comando: update contact <nome> <campo>=<valor> [campo=valor ...]
    # Exemplo: "update contact Isabel relationship=Filha notes=minha filha mais velha"
    if is_owner and re.match(r"^update\s+contact\s+", normalized_msg, re.IGNORECASE):
        chat_id = str(event.source.chat_id) if event.source.chat_id else ""
        try:
            # Extrai: "update contact <identifier> <field>=<value> ..."
            remainder = re.sub(r"^update\s+contact\s+", "", msg_text, flags=re.IGNORECASE).strip()
            # Separa o identificador dos campos (identifier é tudo antes do primeiro campo=valor)
            field_match = re.search(r"\s+\w+=", remainder)
            if field_match:
                identifier = remainder[: field_match.start()].strip()
                fields_str = remainder[field_match.start():].strip()
            else:
                identifier = remainder
                fields_str = ""

            if not identifier:
                response_msg = "❌ Uso: `update contact <nome ou número> campo=valor [campo=valor ...]`"
            elif not fields_str:
                response_msg = f"❌ Nenhum campo especificado. Uso: `update contact {identifier} campo=valor`"
            else:
                fields: dict = {}
                for part in re.findall(r"(\w+)=([^\s=]+(?:\s+[^\s=]+)*?)(?=\s+\w+=|$)", fields_str):
                    fields[part[0]] = part[1].strip()
                if fields:
                    response_msg = _update_contact_fields(identifier, fields)
                else:
                    response_msg = "❌ Não foi possível parsear os campos. Use o formato `campo=valor`."
        except Exception as uc_err:
            response_msg = f"❌ Erro ao atualizar contato: {uc_err}"

        if chat_id:
            try:
                url = f"{BRIDGE_URL}/send"
                payload = json.dumps({"chatId": chat_id, "message": response_msg}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pass
            except Exception as send_err:
                logger.error(f"Erro ao enviar resposta update contact: {send_err}")

        return {"action": "skip", "reason": "update-contact-command"}

    if is_owner:
        # Verificar se há pendência aguardando número e a mensagem atual é um número
        pending = _pending_contact_update.get(sender_id)
        if pending and re.match(r"^\+?[\d\s\(\)\-]{7,}$", msg_text.strip()):
            phone_digits = re.sub(r"\D", "", msg_text.strip())
            pend_fields = pending["fields"]
            pend_name = pending.get("name", "")

            # Pendência de desambiguação: escolher entre múltiplos candidatos
            if pending.get("type") == "disambiguate":
                candidates = pending.get("candidates", [])
                del _pending_contact_update[sender_id]
                selected_key = None
                for key, display, phone in candidates:
                    phone_norm = _normalize_brazilian_phone(re.sub(r"\D", "", phone))
                    digits_norm = _normalize_brazilian_phone(phone_digits)
                    if phone_digits in phone or phone in phone_digits or phone_norm == digits_norm:
                        selected_key = key
                        break
                if selected_key:
                    result = _update_contact_fields(selected_key.split("@")[0], pend_fields)
                else:
                    result = f"❌ Número {phone_digits} não corresponde a nenhum dos candidatos listados."
                chat_id = str(event.source.chat_id) if event.source.chat_id else ""
                logger.info(f"[update-nl] Desambiguação resolvida: {result}")
                if chat_id:
                    try:
                        payload = json.dumps({"chatId": chat_id, "message": result}).encode("utf-8")
                        req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                        req.add_header("Content-Type", "application/json")
                        with urllib.request.urlopen(req, timeout=10):
                            pass
                    except Exception as e:
                        logger.error(f"[update-nl] Erro ao enviar resposta de desambiguação: {e}")
                return {"action": "skip", "reason": "update-contact-disambiguate"}

            del _pending_contact_update[sender_id]
            result = _update_contact_fields(phone_digits, pend_fields)
            # Se encontrou pelo número mas o name ainda é genérico, atualizar o nome também
            if "não encontrado" not in result and "name" not in pend_fields:
                pend_fields["name"] = pend_name
                _update_contact_fields(phone_digits, {"name": pend_name})
            logger.info(f"[update-nl] Pendência resolvida com número {phone_digits}: {result}")
            chat_id = str(event.source.chat_id) if event.source.chat_id else ""
            if chat_id:
                try:
                    payload = json.dumps({"chatId": chat_id, "message": result}).encode("utf-8")
                    req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                except Exception as e:
                    logger.error(f"[update-nl] Erro ao enviar resposta de pendência: {e}")
            return {"action": "skip", "reason": "update-contact-pending"}

        # Classificar intenção via LLM — substitui regex de triggers
        intent_result = _classify_owner_intent(msg_text)
        intent_label = intent_result.get("intent", "")
        intent_type = intent_result.get("intent_type", "other")
        logger.info(f"[update-nl] Intenção detectada: type={intent_type} intent='{intent_label}'")

        # Processar comando de status do dono
        if intent_result.get("is_status"):
            if intent_result.get("is_clear"):
                _clear_owner_status()
                chat_id = str(event.source.chat_id) if event.source.chat_id else ""
                if chat_id:
                    try:
                        payload = json.dumps({"chatId": chat_id, "message": "✅ Status limpo. Voltando ao modo normal."}).encode("utf-8")
                        req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                        req.add_header("Content-Type", "application/json")
                        with urllib.request.urlopen(req, timeout=10):
                            pass
                    except Exception as e:
                        logger.error(f"[owner-status] Erro ao confirmar limpeza: {e}")
            else:
                description = intent_result.get("description", "ocupado")
                until_iso = intent_result.get("until_iso")
                _save_owner_status(description, until_iso, msg_text)
                chat_id = str(event.source.chat_id) if event.source.chat_id else ""
                until_str = ""
                if until_iso:
                    try:
                        from datetime import datetime as _dt
                        until_str = f" até as {_dt.fromisoformat(until_iso).strftime('%H:%M')}"
                    except Exception:
                        pass
                confirm = f"✅ Status definido: *{description}*{until_str}. Vou avisar seus contatos enquanto isso."
                if chat_id:
                    try:
                        payload = json.dumps({"chatId": chat_id, "message": confirm}).encode("utf-8")
                        req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                        req.add_header("Content-Type", "application/json")
                        with urllib.request.urlopen(req, timeout=10):
                            pass
                    except Exception as e:
                        logger.error(f"[owner-status] Erro ao confirmar status: {e}")
            return {"action": "skip", "reason": "owner-status-set"}

        nl_contact_name = intent_result.get("contact_name") if intent_result.get("is_update") else None

        if nl_contact_name:
            chat_id = str(event.source.chat_id) if event.source.chat_id else ""
            logger.info(f"[update-nl] Pedido de atualização detectado para '{nl_contact_name}': '{msg_text}'")

            # Usar LLM apenas para extrair campos explicitamente mencionados pelo owner
            # Campos auto-gerados pelo classificador (summary, tone, guidelines, etc.) são excluídos
            # para não sobrescrever dados reais com valores inventados
            try:
                extracted = _extract_update_fields_via_llm(nl_contact_name, msg_text)
                owner_update_fields = {"name", "relationship", "manual_relationship", "nickname", "pet_name", "notes", "product", "frequent_greeting"}
                fields_to_update = {k: v for k, v in extracted.items() if k in owner_update_fields and v is not None}

                # Garantir manual_relationship quando relationship for definido pelo owner
                if "relationship" in fields_to_update and "manual_relationship" not in fields_to_update:
                    fields_to_update["manual_relationship"] = fields_to_update["relationship"]

                # Se não extraiu relacionamento mas a mensagem menciona "filho/filha", inferir
                # kw → (relationship enum, manual_relationship label)
                rel_keywords = {
                    "filho": ("Filho", "Filho"), "filha": ("Filho", "Filha"),
                    "parente": ("Parente", "Parente"), "irmão": ("Parente", "Irmão"),
                    "irmã": ("Parente", "Irmã"), "amigo": ("Amigo", "Amigo"),
                    "amiga": ("Amigo", "Amiga"), "cliente": ("Cliente", "Cliente"),
                    "vendedor": ("Vendedor", "Vendedor"), "namorada": ("AmigoProximo", "Namorada"),
                    "namorado": ("AmigoProximo", "Namorado"), "esposa": ("Parente", "Esposa"),
                    "marido": ("Parente", "Marido"), "esposo": ("Parente", "Esposo"),
                }
                if "relationship" not in fields_to_update:
                    for kw, (rel, man_rel) in rel_keywords.items():
                        if kw in msg_text.lower():
                            fields_to_update["relationship"] = rel
                            fields_to_update["manual_relationship"] = man_rel
                            break

                if fields_to_update:
                    # Extrair número de telefone da mensagem (ex: "edite o contato 5511996472188")
                    _phone_in_msg = None
                    _phone_match = re.search(r"\b(\+?[\d]{10,15})\b", msg_text)
                    if _phone_match:
                        _phone_in_msg = re.sub(r"\D", "", _phone_match.group(1))
                        logger.info(f"[update-nl] Número encontrado na mensagem: {_phone_in_msg}")

                    # Se há cartão pendente, tentar pelo número do cartão diretamente (evita busca por nome que pode falhar)
                    card = _pending_contact_card.get(sender_id)
                    if _phone_in_msg:
                        result = _update_contact_fields(_phone_in_msg, fields_to_update)
                        logger.info(f"[update-nl] Tentativa via número da mensagem ({_phone_in_msg}): {result}")
                        if "não encontrado" not in result:
                            response_msg = result
                            card = None
                        else:
                            result = None
                    elif card and card.get("phone") and "name" in fields_to_update:
                        # Atualiza pelo número do cartão e aplica nome da mensagem
                        result = _update_contact_fields(card["phone"], fields_to_update)
                        logger.info(f"[update-nl] Tentativa via cartão pendente ({card['phone']}): {result}")
                        if "não encontrado" not in result:
                            del _pending_contact_card[sender_id]
                            response_msg = result
                            card = None  # já resolvido
                    else:
                        result = None
                    if card is not None or result is None:
                        # Verificar ambiguidade antes de atualizar por nome
                        candidates = _find_contact_matches(nl_contact_name)
                        if len(candidates) > 1:
                            # Múltiplos matches — pedir confirmação com número
                            _pending_contact_update[sender_id] = {
                                "type": "disambiguate",
                                "candidates": candidates,
                                "fields": fields_to_update,
                                "name": nl_contact_name,
                            }
                            lines = [f"❓ Encontrei {len(candidates)} contatos com nome *{nl_contact_name}*:"]
                            for i, (key, display, phone) in enumerate(candidates, 1):
                                lines.append(f"  {i}. {display} — {phone}")
                            lines.append("\nQual é o número do contato que deseja atualizar?")
                            result = "\n".join(lines)
                            logger.info(f"[update-nl] Ambiguidade detectada para '{nl_contact_name}': {len(candidates)} candidatos")
                        else:
                            result = _update_contact_fields(nl_contact_name, fields_to_update)
                    logger.info(f"[update-nl] Resultado: {result}")
                    if "não encontrado" in result:
                        # Tentar com cartão de contato compartilhado anteriormente
                        card = _pending_contact_card.get(sender_id)
                        if card and card.get("phone"):
                            if card.get("name"):
                                fields_to_update["name"] = card["name"]
                            result = _update_contact_fields(card["phone"], fields_to_update)
                            logger.info(f"[update-nl] Resultado via cartão ({card['phone']}): {result}")
                            if "não encontrado" not in result:
                                del _pending_contact_card[sender_id]
                                response_msg = result
                            else:
                                # Contato não existe — verificar variação de 9º dígito antes de criar
                                pc_path = Path("/opt/data/personal_contacts.json")
                                try:
                                    with open(str(pc_path), "r", encoding="utf-8") as _f:
                                        _pc = json.load(_f)
                                    card_phone = card["phone"]
                                    card_norm = _normalize_brazilian_phone(card_phone)
                                    existing_key = next(
                                        (k for k in _pc if "@s.whatsapp.net" in k and
                                         _normalize_brazilian_phone(k.split("@")[0]) == card_norm),
                                        None
                                    )
                                    if existing_key:
                                        # Atualizar entrada existente (variação de 9º dígito)
                                        for field, value in fields_to_update.items():
                                            if value is not None:
                                                _pc[existing_key][field] = value
                                        with open(str(pc_path), "w", encoding="utf-8") as _f:
                                            json.dump(_pc, _f, ensure_ascii=False, indent=2)
                                        del _pending_contact_card[sender_id]
                                        upd_name = _pc[existing_key].get("name") or existing_key
                                        logger.info(f"[update-nl] Contato existente atualizado via normalização: {existing_key}")
                                        response_msg = f"✅ Contato *{upd_name}* ({existing_key}) atualizado."
                                    else:
                                        new_key = f"{card_phone}@s.whatsapp.net"
                                        new_name = fields_to_update.get("name") or card.get("name") or nl_contact_name
                                        _pc[new_key] = {
                                            "name": new_name,
                                            "relationship": fields_to_update.get("relationship", "Cliente"),
                                            "manual_relationship": fields_to_update.get("manual_relationship", fields_to_update.get("relationship", "Cliente")),
                                            "nickname": fields_to_update.get("nickname"),
                                            "notes": None, "product": None, "tone": "polido e profissional",
                                            "frequent_greeting": None, "summary": "Pendente de classificação.",
                                            "intent": "Contato inicial.", "frequency": "esporádica",
                                            "guidelines": "Responda de forma prestativa.",
                                            "last_interaction": time.time(),
                                        }
                                        with open(str(pc_path), "w", encoding="utf-8") as _f:
                                            json.dump(_pc, _f, ensure_ascii=False, indent=2)
                                        del _pending_contact_card[sender_id]
                                        logger.info(f"[update-nl] Novo contato criado via cartão: {new_key} name='{new_name}'")
                                        response_msg = f"✅ Contato *{new_name}* ({new_key}) criado com sucesso."
                                except Exception as _ce:
                                    logger.error(f"[update-nl] Erro ao criar contato via cartão: {_ce}")
                                    _pending_contact_update[sender_id] = {"name": nl_contact_name, "fields": fields_to_update}
                                    response_msg = f"Não encontrei '{nl_contact_name}' nem pelo número do cartão. Qual é o número do WhatsApp? (Ex: 5511999998888)"
                        else:
                            _pending_contact_update[sender_id] = {
                                "name": nl_contact_name,
                                "fields": fields_to_update,
                            }
                            response_msg = (
                                f"Não encontrei '{nl_contact_name}' nos seus contatos. "
                                f"Compartilhe o cartão do contato ou informe o número (Ex: 5511999998888)"
                            )
                    else:
                        _pending_contact_card.pop(sender_id, None)
                        response_msg = result
                else:
                    logger.warning(f"[update-nl] Nenhum campo extraído para '{nl_contact_name}'")
                    response_msg = f"⚠️ Não consegui identificar o que atualizar para '{nl_contact_name}'. Use: `update contact {nl_contact_name} campo=valor`"
            except Exception as nl_err:
                logger.error(f"[update-nl] Erro ao extrair campos: {nl_err}")
                response_msg = f"❌ Erro ao processar atualização de '{nl_contact_name}': {nl_err}"

            if chat_id and response_msg:
                try:
                    url = f"{BRIDGE_URL}/send"
                    payload = json.dumps({"chatId": chat_id, "message": response_msg}).encode("utf-8")
                    req = urllib.request.Request(url, data=payload, method="POST")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                except Exception as send_err:
                    logger.error(f"[update-nl] Erro ao enviar resposta: {send_err}")

            return {"action": "skip", "reason": "update-contact-nl"}

    # Se for mensagem manual enviada pelo dono no WhatsApp para outro contato, pulamos a resposta do LLM
    if is_owner and not is_self_chat:
        return {"action": "skip", "reason": "owner-manual-message"}

    # Ignorar mensagens de status do bot (stop_bot/start_bot responses)
    if msg_text in [
        "🐼 *Bot Paused*\n\nO chatbot está descansando. Use `start_bot` para retomar.",
        "🚀 *Bot Ativo*\n\nO chatbot voltou a funcionar!",
        "⏸️ *Atendimento do WhatsApp pausado.* Os clientes não receberão respostas da IA a partir de agora.",
        "▶️ *Atendimento do WhatsApp ativo.* A IA voltará a responder os clientes automaticamente."
    ]:
        return {"action": "skip", "reason": "bot-status-message"}

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

        # Verificar status ativo do dono — resposta proativa só para amigos/parentes
        owner_status = _get_active_owner_status()
        if owner_status and chat_id:
            try:
                pc_path = Path("/opt/data/personal_contacts.json")
                contact_data = {}
                if pc_path.exists():
                    pc = json.loads(pc_path.read_text(encoding="utf-8"))
                    contact_data = pc.get(sender_id, pc.get(chat_id, {}))
                contact_name = contact_data.get("name") or contact_data.get("nickname") or ""
                relationship = contact_data.get("relationship") or ""
                manual_rel = contact_data.get("manual_relationship") or ""
                rel_label = manual_rel or relationship

                is_close = (
                    relationship in ("AmigoProximo", "Parente", "Filho", "Amigo")
                    or rel_label.lower() in ("namorada", "namorado", "esposa", "marido", "mãe", "pai", "filho", "filha", "irmão", "irmã", "avó", "avô")
                )

                if is_close:
                    logger.info(f"[owner-status] Respondendo proativamente para {contact_name or sender_id} (rel={rel_label})")
                    status_response = _generate_status_response(contact_name, relationship, manual_rel, owner_status)
                    payload = json.dumps({"chatId": chat_id, "message": status_response}).encode("utf-8")
                    req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                    logger.info(f"[owner-status] Resposta de status enviada para {chat_id}")
                else:
                    logger.info(f"[owner-status] Status ativo mas contato é cliente/desconhecido — LLM responde normalmente")
            except Exception as e:
                logger.error(f"[owner-status] Erro ao verificar status: {e}")
            # Mensagem segue para processamento normal (salva no histórico, André vê depois)
    else:
        # Para o dono, salvar chat_id e texto da mensagem atual
        chat_id = str(event.source.chat_id) if event.source.chat_id else ""
        if chat_id and sender_id:
            _sender_to_chat[sender_id] = chat_id
        if sender_id and msg_text:
            _last_owner_text[sender_id] = msg_text

    # Roteamento Dinâmico de Modelos (Dono vs Clientes)
    try:
        session_key = gateway._session_key_for_source(event.source)
        if session_key:
            owner_model = config.whatsapp_owner_model
            owner_provider = config.whatsapp_owner_provider
            client_model = config.whatsapp_client_model
            client_provider = config.whatsapp_client_provider
            
            if is_owner:
                gateway._session_model_overrides[session_key] = {
                    "model": owner_model,
                    "provider": owner_provider
                }
            else:
                gateway._session_model_overrides[session_key] = {
                    "model": client_model,
                    "provider": client_provider
                }
    except Exception as e:
        logger.error(f"Erro ao aplicar override de modelo: {e}")

    return None


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

    owner_number = config.whatsapp_owner_number
    if not owner_number:
        return None

    clean_sender = "".join(c for c in sender_id.split("@")[0].split(":")[0] if c.isdigit()) if sender_id else ""
    clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())

    # ── Modo A: André (dono) ──────────────────────────────────────────────
    if _normalize_brazilian_phone(clean_sender) == _normalize_brazilian_phone(clean_owner):
        chat_id = _resolve_chat_id(sender_id)
        history_context = _fetch_chat_history(chat_id, limit=50) if chat_id else ""
        history_section = (
            "\n\n### HISTÓRICO DE MENSAGENS ANTERIORES ###\n"
            "Abaixo está o histórico recente da conversa para você entender o contexto anterior. "
            "NÃO responda novamente a essas mensagens do histórico, use-as apenas como contexto "
            "para responder à nova mensagem do André.\n\n"
            f"{history_context}"
        ) if history_context else ""

        # Detectar se André está perguntando sobre outra conversa/contato
        cross_context = ""
        current_text = _last_owner_text.get(sender_id, "")
        detected_name = _detect_contact_query(current_text)
        if detected_name:
            contact_key, contact_data = _search_contact_by_name(detected_name)
            if contact_key:
                phone = contact_key.split("@")[0]
                cross_history = _fetch_cross_session_history(phone, limit=30)
                if cross_history:
                    contact_name = contact_data.get("name", detected_name) if contact_data else detected_name
                    cross_context = f"Conversa com {contact_name} ({phone}):\n\n{cross_history}"
                    logger.info(f"[cross-session] Injetando histórico de {contact_name} ({phone})")
                else:
                    logger.warning(f"[cross-session] Contato '{detected_name}' encontrado mas sem histórico nos DBs")
            else:
                logger.info(f"[cross-session] Nome '{detected_name}' não encontrado em personal_contacts")

        return _build_owner_context(history_section, cross_context=cross_context)

    # ── Modo B: Cliente / Contato pessoal ────────────────────────────────
    is_first_turn = context.get("is_first_turn", False) if context else False
    if is_first_turn:
        try:
            delay_s = config.whatsapp_first_response_delay_s
            if delay_s > 0:
                logger.info(f"Aplicando delay de {delay_s}s para a primeira resposta ao cliente...")
                time.sleep(delay_s)
        except (ValueError, OSError) as e:
            logger.error(f"Erro ao aplicar delay: {e}")

    whatsapp_soul, rules_content = _load_support_files()
    personal_contacts = _load_personal_contacts()

    # Resolver JIDs e telefone
    db_query_jid = sender_id
    parts_db = sender_id.split("@")
    if len(parts_db) == 2:
        jid_part, domain_part = parts_db
        db_query_jid = f"{jid_part.split(':')[0]}@{domain_part}"

    resolved_sender = _resolve_phone_from_jid(sender_id)
    clean_jid = resolved_sender
    parts = resolved_sender.split("@")
    if len(parts) == 2:
        jid_part, domain_part = parts
        clean_jid = f"{jid_part.split(':')[0]}@{domain_part}"
    phone_number = clean_jid.split("@")[0]

    chat_id = _resolve_chat_id(sender_id)
    history_context = _fetch_chat_history(chat_id, limit=50) if chat_id else ""
    history_section = (
        "### HISTÓRICO DE MENSAGENS ANTERIORES ###\n"
        "Abaixo está o histórico recente da conversa para você entender o contexto anterior. "
        "NÃO responda novamente a essas mensagens do histórico, use-as apenas como contexto "
        "para responder à nova mensagem do cliente.\n\n"
        f"{history_context}\n\n"
    ) if history_context else ""

    # Buscar info de contato no JSON
    contact_info = personal_contacts.get(clean_jid) or personal_contacts.get(phone_number)

    # Verificar se precisa de classificação em tempo real
    needs_live_classify = False
    target_key = clean_jid
    live_classify_threshold_seconds = config.whatsapp_live_classify_cooldown
    if contact_info:
        old_defaults = ["Conversa inicial.", "Conversa muito curta.", "Conversa inicial de suporte/atendimento.", "Pendente de classificação."]
        has_old_default_summary = contact_info.get("summary") in old_defaults
        if has_old_default_summary or not contact_info.get("summary") or not contact_info.get("intent") or not contact_info.get("frequency"):
            needs_live_classify = True
            if phone_number in personal_contacts:
                target_key = phone_number
        else:
            last_interaction_ts = contact_info.get("last_interaction", 0)
            if last_interaction_ts and (time.time() - last_interaction_ts) > live_classify_threshold_seconds:
                needs_live_classify = True
                logger.info(f"Re-classificando {phone_number}: última interação há {int((time.time() - last_interaction_ts) / 60)} min.")
                if phone_number in personal_contacts:
                    target_key = phone_number
    else:
        needs_live_classify = True
        target_key = clean_jid

    if needs_live_classify:
        try:
            new_contact_data = _live_classify_contact(
                sender_id=sender_id,
                db_query_jid=db_query_jid,
                phone_number=phone_number,
                contact_info=contact_info,
                target_key=target_key,
                personal_contacts=personal_contacts,
            )
            if new_contact_data is not None:
                contact_info = new_contact_data
        except Exception as live_err:
            logger.error(f"Erro na classificação em tempo real do contato: {live_err}")

    return _build_support_prompt(whatsapp_soul, rules_content, history_section, contact_info=contact_info)


_sync_running = threading.Event()  # garante que apenas um sync roda por vez


def _run_sync_in_background(force: bool, chat_id: str | None = None) -> None:
    """Executa o sync de contatos em thread daemon, notificando o owner ao terminar."""
    if _sync_running.is_set():
        logger.info("[sync-bg] Sync já em andamento, ignorando nova solicitação.")
        return

    def _worker():
        _sync_running.set()
        try:
            result = _sync_contacts_from_db_internal(force=force)
            logger.info(f"[sync-bg] Concluído: {result}")
            if chat_id:
                try:
                    payload = json.dumps({"chatId": chat_id, "message": f"👤 *Sincronização concluída*\n\n{result}"}).encode()
                    req = urllib.request.Request(f"{BRIDGE_URL}/send", data=payload, method="POST")
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                except Exception as e:
                    logger.warning(f"[sync-bg] Falha ao notificar owner: {e}")
        finally:
            _sync_running.clear()

    threading.Thread(target=_worker, daemon=True).start()


def _run_periodic_sync():
    import time
    from pathlib import Path
    pc_path = Path("/opt/data/personal_contacts.json")

    last_code_check = time.time()
    last_git_pull = time.time()
    # Sync de contatos: inicializa no passado para não disparar imediatamente no boot
    # O primeiro sync periódico acontece após WHATSAPP_SYNC_INTERVAL_HOURS horas
    sync_interval_hours = int(os.getenv("WHATSAPP_SYNC_INTERVAL_HOURS", "24"))
    sync_interval_s = sync_interval_hours * 3600
    last_contact_sync = time.time()  # não roda no boot

    last_pc_mtime = 0.0
    if pc_path.exists():
        try:
            last_pc_mtime = os.path.getmtime(pc_path)
        except Exception:
            pass

    # Aguarda 60 segundos após o boot antes de iniciar as verificações em loop
    time.sleep(60)

    while True:
        # 1. Verificar se personal_contacts.json foi modificado localmente
        if pc_path.exists():
            try:
                current_mtime = os.path.getmtime(pc_path)
                if current_mtime > last_pc_mtime:
                    logger.info(f"Modificação local detectada em {pc_path}. Sincronizando com o GitHub...")
                    if _push_personal_contacts_to_github():
                        last_pc_mtime = current_mtime
                    else:
                        last_pc_mtime = current_mtime
            except Exception as e:
                logger.error(f"Erro ao monitorar modificações locais de contatos: {e}")

        # 2. Puxar configurações do GitHub (a cada 1 hora)
        if time.time() - last_git_pull >= 3600:
            last_git_pull = time.time()
            try:
                logger.info("Iniciando puxada periódica de configurações do GitHub...")
                _pull_and_merge_configurations()
                if pc_path.exists():
                    last_pc_mtime = os.path.getmtime(pc_path)
            except Exception as e:
                logger.error(f"Erro na puxada periódica de configurações: {e}")

        # 3. Sync periódico de contatos em background (intervalo configurável via env)
        if time.time() - last_contact_sync >= sync_interval_s:
            last_contact_sync = time.time()
            logger.info(f"[sync-bg] Disparando sync periódico (intervalo={sync_interval_hours}h)...")
            _run_sync_in_background(force=False, chat_id=None)

        # 4. Verificar atualizações de código a cada 24 horas (86400 segundos)
        if time.time() - last_code_check >= 86400:
            last_code_check = time.time()
            try:
                logger.info("Verificando atualizações de código do plugin...")
                if _self_update_plugin_code():
                    logger.info("Código do plugin atualizado! Reiniciando container...")
                    os._exit(0)
            except Exception as e:
                logger.error(f"Erro ao checar auto-update de código: {e}")

        time.sleep(60)


_EXEC_PATTERN = re.compile(
    r"^EXEC:\s*update\s+contact\s+(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def post_llm_call(*args, **kwargs):
    """Intercepta resposta do LLM e executa linhas EXEC: update contact <nome> campo=valor."""
    logger.info(f"[post_llm_call] chamado — kwargs keys: {list(kwargs.keys())} args count: {len(args)}")
    # Hermes pode não passar 'platform' no post_llm_call — assumir whatsapp (este é o plugin whatsapp)
    platform = kwargs.get("platform")
    if not platform:
        ctx = next((a for a in args if isinstance(a, dict)), None)
        platform = (ctx or {}).get("platform") or "whatsapp"

    if platform != "whatsapp":
        return None

    # Verificar se é sessão do owner via session_id (contém o JID do sender)
    session_id = kwargs.get("session_id", "")
    owner_number = config.whatsapp_owner_number
    if owner_number and session_id:
        clean_session = "".join(c for c in session_id.split("@")[0].split(":")[0] if c.isdigit())
        clean_owner = "".join(c for c in owner_number.split("@")[0].split(":")[0] if c.isdigit())
        if clean_session and clean_owner and _normalize_brazilian_phone(clean_session) != _normalize_brazilian_phone(clean_owner):
            logger.debug(f"[post_llm_call] sessão {session_id!r} não é do owner, pulando")
            return None

    response_text = kwargs.get("assistant_response") or ""
    if not response_text:
        logger.debug(f"[post_llm_call] assistant_response vazio. kwargs keys: {list(kwargs.keys())}")
        return None

    matches = _EXEC_PATTERN.findall(response_text)
    logger.info(f"[post_llm_call] response_text len={len(response_text)}, EXEC matches={len(matches)}, session={session_id!r}")
    if not matches:
        return None

    logger.info(f"[post_llm_call] {len(matches)} EXEC(s) encontrados: {matches}")

    exec_results = []
    for match in matches:
        match = match.strip()
        field_pos = re.search(r"\s+\w+=", match)
        if not field_pos:
            logger.warning(f"[post_llm_call] EXEC sem campos: '{match}'")
            continue
        identifier = match[: field_pos.start()].strip()
        fields_str = match[field_pos.start():].strip()
        fields: dict = {}
        for k, v in re.findall(r"(\w+)=([^\s=]+(?:\s+[^\s=]+)*?)(?=\s+\w+=|$)", fields_str):
            raw_val = v.strip()
            fields[k.strip()] = None if raw_val.upper() == "NULL" else raw_val
        logger.info(f"[post_llm_call] Executando: update contact '{identifier}' campos={fields}")
        if identifier and fields:
            result = _update_contact_fields(identifier, fields)
            exec_results.append(result)
            logger.info(f"[post_llm_call] Resultado: {result}")
        else:
            logger.warning(f"[post_llm_call] identifier='{identifier}' ou fields={fields} inválidos")

    if not exec_results:
        return None

    cleaned = _EXEC_PATTERN.sub("", response_text).strip()
    # Hermes espera dict com a chave correta da resposta
    return {"assistant_response": cleaned}


# ── Comentário de separação ─────────────────────────────────────────────────
# Helpers extraídos acima são testáveis diretamente sem instanciar register().
# ────────────────────────────────────────────────────────────────────────────

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
            logger.info("🔄 Migrando sessão antiga para o novo caminho...")
            for f in old_session.iterdir():
                if f.is_file():
                    try:
                        shutil.copy2(f, new_session / f.name)
                    except Exception as cp_err:
                        logger.error(f"Erro ao copiar {f.name}: {cp_err}")
            shutil.rmtree(old_session, ignore_errors=True)
        if not old_session.exists():
            try:
                old_session.symlink_to(new_session, target_is_directory=True)
                logger.info("✅ Link de compatibilidade da sessão criado.")
            except Exception as link_err:
                logger.error(f"Erro ao criar link simbólico da sessão: {link_err}")

        # 1. Copiar bridge.js do plugin para o volume
        source_bridge = plugin_dir / "bridge.js"
        # Para suportar caso o arquivo esteja na pasta whatsapp-manager do plugin
        if not source_bridge.exists():
            source_bridge = plugin_dir / "whatsapp-manager" / "bridge.js"
        target_bridge = target_bridge_dir / "bridge.js"
        if source_bridge.exists():
            if not target_bridge.exists() or source_bridge.read_bytes() != target_bridge.read_bytes():
                shutil.copy2(source_bridge, target_bridge)
                logger.info(f"bridge.js atualizado em {target_bridge}")

        # 2. Copiar package.json do plugin para o volume
        source_pkg = plugin_dir / "package.json"
        if not source_pkg.exists():
            source_pkg = plugin_dir / "whatsapp-manager" / "package.json"
        target_pkg = target_bridge_dir / "package.json"
        if source_pkg.exists():
            if not target_pkg.exists() or source_pkg.read_bytes() != target_pkg.read_bytes():
                shutil.copy2(source_pkg, target_pkg)
                logger.info(f"package.json atualizado em {target_pkg}")

        # Auto-criação do repositório privado se necessário (Executado no boot de forma 100% transparente)
        try:
            config_repo = config.config_repo
            config_token = config.config_github_token
            setup_user = config.hermes_setup_github_user

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
                            logger.info(f"✓ Repositório privado '{repo_user}/{repo_name}' já existe no GitHub.")
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        logger.warning(f"Repositório '{repo_user}/{repo_name}' não existe. Tentando criar automaticamente...")
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
                                    logger.info(f"✓ Repositório privado '{repo_user}/{repo_name}' criado com sucesso no GitHub!")
                                    time.sleep(3) # Aguarda o GitHub provisionar o branch main

                                    raw_base = "https://raw.githubusercontent.com/empreendedorserial/hermes-whatsapp-mixed/main/deploy"
                                    commit_file_to_repo(repo_user, repo_name, config_token, "/opt/data/SOUL.md", "SOUL.md", f"{raw_base}/SOUL.md")
                                    commit_file_to_repo(repo_user, repo_name, config_token, "/opt/data/SOUL_WHATSAPP.md", "SOUL_WHATSAPP.md", f"{raw_base}/SOUL_WHATSAPP.md")
                                    commit_file_to_repo(repo_user, repo_name, config_token, "/opt/data/SOUL_EMAIL.md", "SOUL_EMAIL.md", f"{raw_base}/SOUL_EMAIL.md")
                                    commit_file_to_repo(repo_user, repo_name, config_token, "/opt/data/support_rules.md", "support_rules.md", f"{raw_base}/support_rules.md")
                                    commit_file_to_repo(repo_user, repo_name, config_token, "/opt/data/personal_contacts.json", "personal_contacts.json", f"{raw_base}/personal_contacts.json.example")
                        except Exception as create_err:
                            logger.error(f"Erro ao criar repositório: {create_err}")
                except Exception as check_err:
                    logger.error(f"Erro ao verificar repositório no GitHub: {check_err}")
        except Exception as repo_err:
            logger.error(f"Erro no processo automático de configuração de repositório: {repo_err}")

        # 3. Bootstrap automático de personas e regras (se ausentes no volume)
        github_user = config.github_user
        raw_base_url = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main/deploy"

        personal_contacts_path = Path("/opt/data/personal_contacts.json")
        if not personal_contacts_path.exists():
            logger.info("Inicializando personal_contacts.json...")
            try:
                personal_contacts_path.write_text("{}", encoding="utf-8")
                logger.info("✓ personal_contacts.json criado.")
            except Exception as pc_err:
                logger.error(f"Erro ao inicializar personal_contacts.json: {pc_err}")

        bootstrap_files = {
            "/opt/data/SOUL.md": f"{raw_base_url}/SOUL.md",
            "/opt/data/SOUL_WHATSAPP.md": f"{raw_base_url}/SOUL_WHATSAPP.md",
            "/opt/data/SOUL_EMAIL.md": f"{raw_base_url}/SOUL_EMAIL.md",
            "/opt/data/support_rules.md": f"{raw_base_url}/support_rules.md",
        }

        for path_str, url in bootstrap_files.items():
            path_obj = Path(path_str)
            if not path_obj.exists():
                logger.info(f"Inicializando {path_str} a partir de {url}...")
                try:
                    with urllib.request.urlopen(url, timeout=10) as response:
                        content = response.read()
                        path_obj.write_bytes(content)
                        logger.info(f"✓ {path_str} baixado com sucesso.")
                except Exception as dl_err:
                    logger.error(f"Erro ao baixar {path_str}: {dl_err}")

        # Garantir cópia das personas para os respectivos perfis se existirem
        soul_whatsapp_path = Path("/opt/data/SOUL_WHATSAPP.md")
        profile_wa_soul = Path("/opt/data/.hermes/profiles/whatsapp/SOUL.md")
        if soul_whatsapp_path.exists() and not profile_wa_soul.exists():
            profile_wa_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_whatsapp_path, profile_wa_soul)
            logger.info(f"✓ Copiado SOUL_WHATSAPP.md para perfil de WhatsApp")

        soul_email_path = Path("/opt/data/SOUL_EMAIL.md")
        profile_em_soul = Path("/opt/data/.hermes/profiles/email/SOUL.md")
        if soul_email_path.exists() and not profile_em_soul.exists():
            profile_em_soul.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(soul_email_path, profile_em_soul)
            logger.info(f"✓ Copiado SOUL_EMAIL.md para perfil de E-mail")

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
                logger.info(f"✓ google_api.py atualizado em {target_google_api}")
        else:
            # Fallback: baixar do GitHub se não estiver bundled
            github_user = config.github_user
            google_api_url = f"https://raw.githubusercontent.com/{github_user}/hermes-whatsapp-mixed/main/deploy/scripts/google_api.py"
            if not target_google_api.exists():
                try:
                    with urllib.request.urlopen(google_api_url, timeout=10) as resp:
                        target_google_api.write_bytes(resp.read())
                    logger.info(f"✓ google_api.py baixado de {google_api_url}")
                except Exception as e:
                    logger.warning(f"Não foi possível obter google_api.py: {e}")

        # 5. Instalar libs Google no venv do Hermes (silencioso — só instala se ausentes)
        _ensure_google_libs()

    except Exception as setup_err:
        logger.error(f"Erro durante o bootstrap automático: {setup_err}")

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
                        logger.error(f"Erro ao registrar skill '{skill_folder.name}': {skill_err}")
            if registered:
                logger.info(f"✓ Skills registradas: {', '.join(registered)}")
    except Exception as skills_err:
        logger.error(f"Erro ao registrar skills: {skills_err}")

    # pre_gateway_dispatch local removido (usando a versão global do módulo)

    # pre_llm_call local removido (usando a versão global do módulo)

    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("post_llm_call", post_llm_call)

    # Auto-Update e Pull de Configurações no Boot
    try:
        logger.info("Puxando últimas configurações e personas do GitHub no boot...")
        _pull_and_merge_configurations()
    except Exception as pull_err:
        logger.error(f"Falha ao puxar configurações no boot: {pull_err}")

    try:
        logger.info("Verificando atualizações de código do plugin no boot...")
        if _self_update_plugin_code():
            logger.info("Código do plugin atualizado no boot! Reiniciando container...")
            os._exit(0)
    except Exception as code_err:
        logger.error(f"Falha ao verificar atualizações de código no boot: {code_err}")

    # Sync NÃO roda no boot — apenas no intervalo periódico ou sob demanda via chat.

    try:
        import threading
        t = threading.Thread(target=_run_periodic_sync, daemon=True)
        t.start()
        logger.info("✅ Agendador periódico (24h) de sincronização iniciado com sucesso.")
    except Exception as thread_err:
        logger.warning(f"Não foi possível iniciar o agendador periódico: {thread_err}")
