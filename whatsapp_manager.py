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


def _sync_contacts_from_db_internal() -> str:
    """Sincroniza contatos do SQLite local para personal_contacts.json e envia para o GitHub."""
    import sqlite3
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

    # 2. Ler contatos únicos do SQLite
    if not db_path.exists():
        return "Erro: Banco de dados SQLite whatsapp_messages.db não encontrado."

    db_contacts = {}
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, MAX(sender_name) as name
            FROM messages
            WHERE chat_id NOT LIKE '%@g.us%' AND chat_id IS NOT NULL
            GROUP BY chat_id
        """)
        rows = cursor.fetchall()
        for chat_id, name in rows:
            if chat_id:
                db_contacts[chat_id] = name
        conn.close()
    except Exception as e:
        return f"Erro ao ler banco de dados SQLite: {e}"

    # 3. Mesclar dados mantendo os já existentes
    updated = False
    added_count = 0
    for chat_id, name in db_contacts.items():
        phone = chat_id.split("@")[0]
        
        # Verificar se já existe por JID ou por número
        exists = False
        for key in list(personal_contacts.keys()):
            if key == chat_id or key == phone:
                exists = True
                break
                
        if not exists:
            personal_contacts[chat_id] = {
                "name": name or f"Contato {phone}",
                "relationship": "cliente/contato",
                "tone": "polido e profissional",
                "guidelines": "Responda de forma prestativa."
            }
            added_count += 1
            updated = True

    if not updated:
        result_str = "Nenhum contato novo encontrado para adicionar."
    else:
        # Salvar JSON localmente
        try:
            with open(pc_path, "w", encoding="utf-8") as f:
                json.dump(personal_contacts, f, indent=2, ensure_ascii=False)
            result_str = f"Sucesso! Mapeados e mesclados {added_count} novos contatos localmente."
        except Exception as e:
            return f"Erro ao salvar personal_contacts.json localmente: {e}"

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
    async def pre_gateway_dispatch(*args, **kwargs):
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
        if is_owner and normalized_msg in sync_commands:
            print("[whatsapp-manager] Comando de sincronização detectado.")
            chat_id = str(event.source.chat_id) if event.source.chat_id else ""
            
            try:
                result_info = _sync_contacts_from_db_internal()
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

            if contact_info:
                name = contact_info.get("name", "Contato Pessoal")
                relationship = contact_info.get("relationship", "amigo/namorada")
                tone = contact_info.get("tone", "informal e amigável")
                guidelines = contact_info.get("guidelines", "Responda como André.")

                return {
                    "context": (
                        "### PERSONA E DIRETRIZES PESSOAIS (RESPONDENDO COMO ANDRÉ ALENCAR) ###\n"
                        "Você está respondendo em nome de André Alencar (o dono deste WhatsApp) para um contato pessoal.\n"
                        "Você DEVE agir como se fosse o próprio André conversando diretamente com a pessoa.\n\n"
                        f"Nome do contato: {name}\n"
                        f"Relação com o André: {relationship}\n"
                        f"Tom de voz recomendado: {tone}\n"
                        f"Diretrizes específicas: {guidelines}\n\n"
                        "### DIRETRIZES IMPORTANTES DE COMPORTAMENTO ###\n"
                        "1. Analise o histórico recente fornecido abaixo para identificar os apelidos mútuos, gírias e o nível de formalidade/descontração com que vocês conversam.\n"
                        "2. Adote o mesmo estilo de escrita do André nas mensagens anteriores. Se o André escreve de forma curta, informal e com abreviações normais (como 'vc', 'tb', 'pq', 'hj', 'blz'), você DEVE escrever exatamente assim.\n"
                        "3. Responda de forma extremamente natural e fluida. Seja breve e direto (evite textos longos, a menos que o contexto exija).\n"
                        "4. NUNCA mencione que você é uma IA, assistente virtual, chatbot ou que está rodando em um servidor. Você é o próprio André.\n\n"
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
