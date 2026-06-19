"""Python unit tests for the whatsapp-manager plugin."""

import os
import json
import unittest
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add root directory to sys.path to import whatsapp_manager
sys.path.append(str(Path(__file__).parent.parent))

import whatsapp_manager
from whatsapp_manager import register

class MockContext:
    def __init__(self):
        self.hooks = {}
        self.skills = {}

    def register_hook(self, name, func):
        self.hooks[name] = func

    def register_skill(self, name, path):
        self.skills[name] = path


class BaseWhatsAppManagerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = MockContext()
        self.env_patcher = patch.dict(os.environ, {
            "WHATSAPP_OWNER_NUMBER": "5511999999999",
            "WHATSAPP_OWNER_MODEL": "gemini-3.5-flash-owner",
            "WHATSAPP_CLIENT_MODEL": "gemini-3.5-flash-client"
        })
        self.env_patcher.start()
        
        # Avoid running bootstrap/shutil operations during register
        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.symlink_to"), \
             patch("pathlib.Path.is_symlink", return_value=True), \
             patch("pathlib.Path.write_text"), \
             patch("shutil.copy2"), \
             patch("urllib.request.urlopen"), \
             patch("whatsapp_manager._ensure_google_libs"), \
             patch("whatsapp_manager._pull_and_merge_configurations"), \
             patch("whatsapp_manager._self_update_plugin_code", return_value=False):
            register(self.ctx)

    def tearDown(self):
        self.env_patcher.stop()


class TestMessageRoutingAndDispatch(BaseWhatsAppManagerTest):
    def test_owner_message_identification(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        # Mock Event and Gateway
        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511999999999:1@s.whatsapp.net" # With device ID
        event.text = "Hello bot"
        event.source.chat_id = "5511999999999@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_1"
        gateway._session_model_overrides = {}

        context = {
            "event": event,
            "gateway": gateway
        }

        res = pre_dispatch("pre_gateway_dispatch", context)
        self.assertIsNone(res) # Owner message is not skipped
        self.assertEqual(gateway._session_model_overrides["session_1"]["model"], "gemini-3.5-flash-owner")

    def test_client_message_when_bot_paused(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        
        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511888888888@s.whatsapp.net" # Client
        event.text = "Hello"
        event.source.chat_id = "5511888888888@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_2"
        gateway._session_model_overrides = {}

        context = {
            "event": event,
            "gateway": gateway
        }

        # Mock _check_bot_paused to return True
        with patch("whatsapp_manager._check_bot_paused", return_value=True):
            res = pre_dispatch("pre_gateway_dispatch", context)
            self.assertEqual(res, {"action": "skip", "reason": "bot-pausado"})

    def test_pre_gateway_dispatch_does_not_rewrite_or_fetch(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")

        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511888888888@s.whatsapp.net" # Client
        event.text = "Hello client query"
        event.source.chat_id = "5511888888888@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_3"
        gateway._session_model_overrides = {}

        context = {
            "event": event,
            "gateway": gateway
        }

        with patch("whatsapp_manager._check_bot_paused", return_value=False), \
             patch("whatsapp_manager._check_chat_silenced", return_value=False), \
             patch("whatsapp_manager._fetch_chat_history") as mock_fetch:
            res = pre_dispatch("pre_gateway_dispatch", context)
            self.assertIsNone(res) # Should not skip or rewrite (returns None)
            mock_fetch.assert_not_called() # Should not fetch history at dispatch stage

    def test_non_whatsapp_platforms_are_ignored(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")
        
        context = {
            "platform": "telegram",
            "sender_id": "5511999999999"
        }
        res = pre_llm("pre_llm_call", context)
        self.assertIsNone(res)

    def test_pre_gateway_dispatch_non_whatsapp_ignored(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        
        event = MagicMock()
        event.source.platform = "telegram"
        context = {
            "event": event,
            "gateway": MagicMock()
        }
        res = pre_dispatch("pre_gateway_dispatch", context)
        self.assertIsNone(res)

    def test_missing_model_env_vars_fallback(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        
        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511999999999@s.whatsapp.net"
        event.text = "Hello"
        event.source.chat_id = "5511999999999@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_x"
        gateway._session_model_overrides = {}

        context = {
            "event": event,
            "gateway": gateway
        }

        # Clear env variables WHATSAPP_OWNER_MODEL and WHATSAPP_CLIENT_MODEL
        with patch.dict(os.environ, {}, clear=True):
            # We must restore WHATSAPP_OWNER_NUMBER for the owner check to pass
            os.environ["WHATSAPP_OWNER_NUMBER"] = "5511999999999"
            res = pre_dispatch("pre_gateway_dispatch", context)
            self.assertIsNone(res)
            self.assertEqual(gateway._session_model_overrides["session_x"]["model"], "gemini-3.1-flash-lite")

    def test_missing_session_key_handled_gracefully(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        
        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511999999999@s.whatsapp.net"
        event.text = "Hello"
        event.source.chat_id = "5511999999999@s.whatsapp.net"

        gateway = MagicMock()
        # Return None for session key
        gateway._session_key_for_source.return_value = None

        context = {
            "event": event,
            "gateway": gateway
        }

        res = pre_dispatch("pre_gateway_dispatch", context)
        # Should not raise exception
        self.assertIsNone(res)

    def test_owner_manual_message_to_third_party_is_skipped(self):
        """Mensagem manual do dono para terceiro (chat_id != owner) deve retornar skip."""
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        event = MagicMock()
        event.source.platform = "whatsapp"
        # Dono enviando mensagem para terceiro
        event.source.user_id = "5511999999999@s.whatsapp.net"
        event.text = "Olá, tudo bem?"
        # Chat com um terceiro (não é self-chat)
        event.source.chat_id = "5511111111111@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_manual"
        gateway._session_model_overrides = {}

        context = {"event": event, "gateway": gateway}

        res = pre_dispatch("pre_gateway_dispatch", context)
        # Deve pular o LLM pois é mensagem manual do dono para terceiro
        self.assertIsNotNone(res)
        self.assertEqual(res.get("action"), "skip")
        self.assertEqual(res.get("reason"), "owner-manual-message")

    def test_silenced_chat_client_message_is_skipped(self):
        """Mensagem de cliente em chat silenciado deve retornar skip."""
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511888888888@s.whatsapp.net"  # Cliente
        event.text = "Olá, tem novidades?"
        event.source.chat_id = "5511888888888@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_silenced"
        gateway._session_model_overrides = {}

        context = {"event": event, "gateway": gateway}

        with patch("whatsapp_manager._check_bot_paused", return_value=False), \
             patch("whatsapp_manager._check_chat_silenced", return_value=True):
            res = pre_dispatch("pre_gateway_dispatch", context)
            self.assertIsNotNone(res)
            self.assertEqual(res.get("action"), "skip")
            self.assertEqual(res.get("reason"), "conversa-silenciada")

    def test_custom_providers_env_vars(self):
        """Pre-dispatch deve honrar as variáveis WHATSAPP_OWNER_PROVIDER e WHATSAPP_CLIENT_PROVIDER."""
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        # 1. Testar Owner com provider customizado
        event_owner = MagicMock()
        event_owner.source.platform = "whatsapp"
        event_owner.source.user_id = "5511999999999@s.whatsapp.net"
        event_owner.text = "Hello"
        event_owner.source.chat_id = "5511999999999@s.whatsapp.net"

        gateway_owner = MagicMock()
        gateway_owner._session_key_for_source.return_value = "session_owner"
        gateway_owner._session_model_overrides = {}

        # 2. Testar Cliente com provider customizado
        event_client = MagicMock()
        event_client.source.platform = "whatsapp"
        event_client.source.user_id = "5511888888888@s.whatsapp.net"
        event_client.text = "Hello"
        event_client.source.chat_id = "5511888888888@s.whatsapp.net"

        gateway_client = MagicMock()
        gateway_client._session_key_for_source.return_value = "session_client"
        gateway_client._session_model_overrides = {}

        with patch.dict(os.environ, {
            "WHATSAPP_OWNER_NUMBER": "5511999999999",
            "WHATSAPP_OWNER_MODEL": "my-owner-model",
            "WHATSAPP_OWNER_PROVIDER": "openrouter",
            "WHATSAPP_CLIENT_MODEL": "my-client-model",
            "WHATSAPP_CLIENT_PROVIDER": "openai"
        }):
            # Rodar dispatch pro owner
            res_owner = pre_dispatch("pre_gateway_dispatch", {"event": event_owner, "gateway": gateway_owner})
            self.assertIsNone(res_owner)
            self.assertEqual(gateway_owner._session_model_overrides["session_owner"]["model"], "my-owner-model")
            self.assertEqual(gateway_owner._session_model_overrides["session_owner"]["provider"], "openrouter")

            # Rodar dispatch pro client
            with patch("whatsapp_manager._check_bot_paused", return_value=False), \
                 patch("whatsapp_manager._check_chat_silenced", return_value=False):
                res_client = pre_dispatch("pre_gateway_dispatch", {"event": event_client, "gateway": gateway_client})
                self.assertIsNone(res_client)
                self.assertEqual(gateway_client._session_model_overrides["session_client"]["model"], "my-client-model")
                self.assertEqual(gateway_client._session_model_overrides["session_client"]["provider"], "openai")

    @patch("whatsapp_manager._sync_contacts_from_db_internal", return_value="sync completed")
    @patch("urllib.request.urlopen")
    def test_pre_gateway_dispatch_sync_contacts_command(self, mock_urlopen, mock_sync):
        """Verifica que pre_gateway_dispatch intercepta o comando sync contacts do dono."""
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        event = MagicMock()
        event.source.platform = "whatsapp"
        event.source.user_id = "5511999999999@s.whatsapp.net" # Dono
        event.text = "sync contacts"
        event.source.chat_id = "5511888888888@s.whatsapp.net" # Envia no chat com um contato

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_sync"
        gateway._session_model_overrides = {}

        context = {"event": event, "gateway": gateway}

        res = pre_dispatch("pre_gateway_dispatch", context)
        self.assertEqual(res, {"action": "skip", "reason": "sync-contacts-command"})
        
        # Verificar que sync foi chamado com force=True
        mock_sync.assert_called_once_with(force=True)
        
        # Verificar que enviou a mensagem de volta
        called_args = mock_urlopen.call_args[0]
        called_req = called_args[0]
        self.assertIn("/send", called_req.full_url)


class TestLLMContextAndPrompting(BaseWhatsAppManagerTest):
    def test_pre_llm_call_owner_context(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")
        self.assertIsNotNone(pre_llm)

        context = {
            "platform": "whatsapp",
            "sender_id": "5511999999999:2@s.whatsapp.net" # Owner with device ID
        }

        res = pre_llm("pre_llm_call", context)
        self.assertIsNotNone(res)
        self.assertIn("ASSISTENTE PESSOAL", res["context"])

    def test_pre_llm_call_client_context(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")

        context = {
            "platform": "whatsapp",
            "sender_id": "5511888888888@s.whatsapp.net" # Client
        }

        # Mock reading of files
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data="Soul client rules")):
            res = pre_llm("pre_llm_call", context)
            self.assertIsNotNone(res)
            self.assertIn("SUPORTE WHATSAPP", res["context"])
            self.assertIn("Soul client rules", res["context"])

    def test_pre_llm_call_injects_history_with_fallback(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")

        context = {
            "platform": "whatsapp",
            "sender_id": "5511888888888:3@s.whatsapp.net" # Client JID with device suffix
        }

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data="Soul client rules")), \
             patch("whatsapp_manager._fetch_chat_history", return_value="Chat history content") as mock_fetch:
            res = pre_llm("pre_llm_call", context)
            self.assertIsNotNone(res)
            self.assertIn("### HISTÓRICO DE MENSAGENS ANTERIORES ###", res["context"])
            self.assertIn("Chat history content", res["context"])
            # Assert fallback JID derivation was used
            mock_fetch.assert_called_once_with("5511888888888@s.whatsapp.net", limit=50)

    def test_pre_llm_call_personal_contact_context(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")

        context = {
            "platform": "whatsapp",
            "sender_id": "5511777777777:2@s.whatsapp.net" # A personal contact (Bruna)
        }

        personal_contacts = {
            "5511777777777@s.whatsapp.net": {
                "name": "Bruna",
                "relationship": "namorada",
                "tone": "romantico",
                "guidelines": "Seja fofo",
                "summary": "Conversa carinhosa.",
                "intent": "Amor",
                "frequency": "diária"
            }
        }
        
        with patch("whatsapp_manager._load_support_files", return_value=("", "")), \
             patch("whatsapp_manager._load_personal_contacts", return_value=personal_contacts), \
             patch("whatsapp_manager._fetch_chat_history", return_value=""):
            res = pre_llm("pre_llm_call", context)
            self.assertIsNotNone(res)
            self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", res["context"])
            self.assertIn("Nome do contato: Bruna", res["context"])
            self.assertIn("Relação com o André: namorada", res["context"])
            self.assertIn("Tom de voz recomendado: romantico", res["context"])
            self.assertIn("Diretrizes específicas: Seja fofo", res["context"])

    def test_pre_llm_call_manual_overrides(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")

        context = {
            "platform": "whatsapp",
            "sender_id": "5511555555555:2@s.whatsapp.net"
        }

        personal_contacts = {
            "5511555555555@s.whatsapp.net": {
                "name": "Marcos (Vendedor)", 
                "relationship": "Cliente", 
                "manual_relationship": "Vendedor", 
                "notes": "Não tenho interesse no momento", 
                "product": "Curso de Inglês", 
                "tone": "técnico e direto", 
                "guidelines": "Seja breve e recuse educadamente.", 
                "summary": "Conversa comercial.", 
                "intent": "Oferecer produto.", 
                "frequency": "esporádica"
            }
        }

        with patch("whatsapp_manager._load_support_files", return_value=("", "")), \
             patch("whatsapp_manager._load_personal_contacts", return_value=personal_contacts), \
             patch("whatsapp_manager._fetch_chat_history", return_value=""):
            res = pre_llm("pre_llm_call", context)
            
        self.assertIsNotNone(res)
        self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", res["context"])
        self.assertIn("Nome do contato: Marcos (Vendedor)", res["context"])
        # manual_relationship deve prevalecer
        self.assertIn("Relação com o André: Vendedor", res["context"])
        # Notes e Product devem ser injetados
        self.assertIn("Observação importante sobre o contato: Não tenho interesse no momento", res["context"])
        self.assertIn("Produto/Serviço envolvido: Curso de Inglês", res["context"])
        self.assertIn("Caso exista uma 'Observação importante sobre o contato' acima, você DEVE seguir essa instrução de comportamento de forma prioritária", res["context"])

    def test_sanitize_classification_result_function(self):
        from whatsapp_manager import _sanitize_classification_result
        
        # Test sanitization with forbidden terms (various cases)
        forbidden_test = {
            "nickname": "pai",
            "pet_name": "MÃE",
            "relationship": "Filho",
            "tone": "informal"
        }
        res = _sanitize_classification_result(forbidden_test)
        self.assertIsNone(res["nickname"])
        self.assertIsNone(res["pet_name"])
        self.assertEqual(res["relationship"], "Filho")

        # Test with allowed/normal values
        allowed_test = {
            "nickname": "Bru",
            "pet_name": "amor",
            "relationship": "AmigoProximo"
        }
        res2 = _sanitize_classification_result(allowed_test)
        self.assertEqual(res2["nickname"], "Bru")
        self.assertEqual(res2["pet_name"], "amor")

    def test_pre_llm_call_sanitizes_legacy_forbidden_pet_names(self):
        pre_llm = self.ctx.hooks.get("pre_llm_call")

        context = {
            "platform": "whatsapp",
            "sender_id": "5511444444444@s.whatsapp.net"
        }

        # JSON containing legacy forbidden values
        personal_json = (
            '{"5511444444444@s.whatsapp.net": {'
            '"name": "Filho do André", '
            '"relationship": "Filho", '
            '"pet_name": "pai", '
            '"nickname": "pai", '
            '"tone": "informal e carinhoso", '
            '"guidelines": "Seja legal.", '
            '"summary": "Conversas familiares.", '
            '"intent": "Falar com pai.", '
            '"frequency": "diária"}}'
        )
        mock_pc_open = unittest.mock.mock_open(read_data=personal_json)
        mock_rules_open = unittest.mock.mock_open(read_data="Soul/Rules content")

        def mock_open_file(path, *args, **kwargs):
            if "personal_contacts.json" in str(path):
                return mock_pc_open(path, *args, **kwargs)
            return mock_rules_open(path, *args, **kwargs)

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open_file), \
             patch("whatsapp_manager._fetch_chat_history", return_value=""):
            res = pre_llm("pre_llm_call", context)

        self.assertIsNotNone(res)
        self.assertIn("Nome do contato: Filho do André", res["context"])
        # Ensure 'pai' is not included in the context as nickname or pet_name
        self.assertNotIn("Apelido do contato: pai", res["context"])
        self.assertNotIn("Nome carinhoso/Apelido afetivo: pai", res["context"])

    def test_build_owner_context_with_history(self):
        """Verifica se _build_owner_context inclui a diretriz e o histórico."""
        import whatsapp_manager
        res = whatsapp_manager._build_owner_context("\n\n### HISTÓRICO DE MENSAGENS ANTERIORES ###\nHistory text")
        self.assertIn("ASSISTENTE PESSOAL", res["context"])
        self.assertIn("History text", res["context"])

    def test_build_personal_prompt(self):
        """Verifica se _build_personal_prompt constrói o prompt corretamente com campos opcionais."""
        import whatsapp_manager
        contact_info = {
            "name": "Bruna",
            "tone": "carinhoso",
            "nickname": "Bru",
            "pet_name": "amor",
            "frequent_greeting": "Oi linda",
            "summary": "Resumo teste",
            "intent": "Intenção teste",
            "frequency": "diária",
            "notes": "Notas teste",
            "product": "Produto teste"
        }
        res = whatsapp_manager._build_personal_prompt(contact_info, "namorada", "History content")
        ctx = res["context"]
        self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", ctx)
        self.assertIn("Nome do contato: Bruna", ctx)
        self.assertIn("Relação com o André: namorada", ctx)
        self.assertIn("Tom de voz recomendado: carinhoso", ctx)
        self.assertIn("Apelido do contato: Bru", ctx)
        self.assertIn("Nome carinhoso/Apelido afetivo: amor", ctx)
        self.assertIn("Saudação frequente: Oi linda", ctx)
        self.assertIn("Resumo das conversas anteriores: Resumo teste", ctx)
        self.assertIn("Intenção das últimas conversas: Intenção teste", ctx)
        self.assertIn("Frequência das conversas: diária", ctx)
        self.assertIn("Observação importante sobre o contato: Notas teste", ctx)
        self.assertIn("Produto/Serviço envolvido: Produto teste", ctx)
        self.assertIn("History content", ctx)

    def test_build_support_prompt(self):
        """Verifica se _build_support_prompt inclui soul, regras e histórico."""
        import whatsapp_manager
        res = whatsapp_manager._build_support_prompt("custom soul", "custom rules", "History content")
        ctx = res["context"]
        self.assertIn("SUPORTE WHATSAPP", ctx)
        self.assertIn("custom soul", ctx)
        self.assertIn("custom rules", ctx)
        self.assertIn("History content", ctx)


class TestContactManagementAndSync(BaseWhatsAppManagerTest):
    @patch("sqlite3.connect")
    @patch("whatsapp_manager._classify_contact_via_llm")
    @patch("pathlib.Path.exists")
    @patch("builtins.open")
    def test_sync_contacts_from_db_internal_with_updates(self, mock_open, mock_exists, mock_classify, mock_sqlite_connect):
        from whatsapp_manager import _sync_contacts_from_db_internal
        
        # Mock paths exists
        mock_exists.return_value = True
        
        # Mock personal_contacts.json content
        existing_contacts = {
            "5511777777777@s.whatsapp.net": {
                "name": "Bruna",
                "relationship": "namorada",
                "tone": "romantico",
                "guidelines": "Seja fofo"
            },
            "5511888888888@s.whatsapp.net": {
                "name": "Carlos",
                "relationship": "amigo",
                "tone": "descontraído",
                "guidelines": "Fale como amigo",
                "summary": "Conversa antiga",
                "intent": "Ajuda",
                "frequency": "semanal",
                "last_interaction": 1686460000
            }
        }
        
        mock_file = unittest.mock.mock_open(read_data=json.dumps(existing_contacts))
        mock_open.side_effect = lambda path, *args, **kwargs: mock_file(path, *args, **kwargs)
        
        # Mock SQLite cursor and fetchall results
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.side_effect = [
            [
                ("5511777777777@s.whatsapp.net", 1686450000, 1),
                ("5511888888888@s.whatsapp.net", 1686450000, 1)
            ],
            [
                ("5511777777777@s.whatsapp.net", "Bruna", 10, 1686440000, 1686450000), # Stale contact
                ("5511888888888@s.whatsapp.net", "Carlos", 5, 1686440000, 1686450000) # Non-stale contact (skipped)
            ],
            [(0, "Bruna", "oi amor te amo")]
        ]
        
        # Mock LLM classification response
        mock_classify.return_value = {
            "relationship": "AmigoProximo",
            "tone": "informal e carinhoso",
            "nickname": "Bru",
            "pet_name": "amor",
            "frequent_greeting": "Oi linda",
            "summary": "Conversa carinhosa",
            "intent": "Saudação",
            "frequency": "diária",
            "product": None,
            "guidelines": "Seja romântico."
        }
        
        # Call the sync function
        with patch.dict(os.environ, {"CONFIG_REPO": ""}):
            result = _sync_contacts_from_db_internal(force=False)
            
        # Verify the classification was called only once (for Bruna)
        mock_classify.assert_called_once()
        
        # Verify write to personal_contacts.json occurred
        mock_open.assert_any_call(Path("/opt/data/personal_contacts.json"), "w", encoding="utf-8")
        
        # Retrieve what was written
        write_calls = mock_file().write.call_args_list
        written_data = "".join(call[0][0] for call in write_calls)
        written_json = json.loads(written_data)
        
        bruna_data = written_json["5511777777777@s.whatsapp.net"]
        self.assertEqual(bruna_data["name"], "Bruna")
        self.assertEqual(bruna_data["relationship"], "AmigoProximo")
        self.assertEqual(bruna_data["tone"], "informal e carinhoso")
        self.assertEqual(bruna_data["guidelines"], "Seja romântico.")
        self.assertEqual(bruna_data["nickname"], "Bru")
        self.assertEqual(bruna_data["pet_name"], "amor")
        self.assertEqual(bruna_data["summary"], "Conversa carinhosa")
        self.assertEqual(bruna_data["intent"], "Saudação")
        self.assertEqual(bruna_data["frequency"], "diária")
        
        # Carlos fields should remain exactly as they were (not stale, skipped)
        carlos_data = written_json["5511888888888@s.whatsapp.net"]
        self.assertEqual(carlos_data["relationship"], "amigo")
        self.assertEqual(carlos_data["tone"], "descontraído")
        self.assertEqual(carlos_data["guidelines"], "Fale como amigo")
        self.assertEqual(carlos_data["summary"], "Conversa antiga")

    @patch("sqlite3.connect")
    @patch("whatsapp_manager._classify_contact_via_llm")
    @patch("pathlib.Path.exists")
    @patch("builtins.open")
    def test_sync_contacts_from_db_internal_hits_limit(self, mock_open, mock_exists, mock_classify, mock_sqlite_connect):
        from whatsapp_manager import _sync_contacts_from_db_internal
        
        mock_exists.return_value = True
        mock_file = unittest.mock.mock_open(read_data="{}")
        mock_open.side_effect = lambda path, *args, **kwargs: mock_file(path, *args, **kwargs)
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.side_effect = [
            [
                ("5511777777777@s.whatsapp.net", 1686450000, 1),
                ("5511888888888@s.whatsapp.net", 1686450000, 1)
            ],
            [
                ("5511777777777@s.whatsapp.net", "Bruna", 10, 1686440000, 1686450000),
                ("5511888888888@s.whatsapp.net", "Carlos", 10, 1686440000, 1686450000)
            ],
            [(0, "Bruna", "oi amor te amo")]
        ]
        
        mock_classify.return_value = {
            "relationship": "AmigoProximo",
            "tone": "informal e carinhoso",
            "nickname": "Bru",
            "pet_name": "amor",
            "frequent_greeting": "Oi linda",
            "summary": "Conversa carinhosa",
            "intent": "Saudação",
            "frequency": "diária",
            "product": None,
            "guidelines": "Seja romântico."
        }
        
        # Limit set to 1 classification
        with patch.dict(os.environ, {"CONFIG_REPO": "", "WHATSAPP_SYNC_MAX_CLASSIFICATIONS": "1"}):
            result = _sync_contacts_from_db_internal(force=False)
            
        # Classify should be called exactly once
        mock_classify.assert_called_once()
        
        # Verify JSON write
        write_calls = mock_file().write.call_args_list
        written_data = "".join(call[0][0] for call in write_calls)
        written_json = json.loads(written_data)
        
        # Bruna is classified
        self.assertEqual(written_json["5511777777777@s.whatsapp.net"]["relationship"], "AmigoProximo")
        self.assertEqual(written_json["5511777777777@s.whatsapp.net"]["summary"], "Conversa carinhosa")
        
        # Carlos is added as Pending
        self.assertEqual(written_json["5511888888888@s.whatsapp.net"]["relationship"], "Cliente")
        self.assertEqual(written_json["5511888888888@s.whatsapp.net"]["summary"], "Pendente de classificação.")

    @patch("sqlite3.connect")
    @patch("whatsapp_manager._classify_contact_via_llm")
    @patch("pathlib.Path.exists")
    @patch("os.path.exists")
    @patch("builtins.open")
    def test_pre_llm_call_live_classification(self, mock_open, mock_os_exists, mock_path_exists, mock_classify, mock_sqlite_connect):
        pre_llm = self.ctx.hooks.get("pre_llm_call")
        
        # Um contato que não está no personal_contacts.json (classifica on-the-fly)
        context = {
            "platform": "whatsapp",
            "sender_id": "5511666666666@s.whatsapp.net"
        }
        
        # Mocks para arquivos existirem
        mock_os_exists.return_value = True
        mock_path_exists.return_value = True
        
        # personal_contacts.json vazio inicialmente
        mock_pc_open = unittest.mock.mock_open(read_data="{}")
        mock_rules_open = unittest.mock.mock_open(read_data="Soul/Rules content")
        
        def mock_open_file(path, *args, **kwargs):
            if "personal_contacts.json" in str(path):
                return mock_pc_open(path, *args, **kwargs)
            return mock_rules_open(path, *args, **kwargs)
            
        mock_open.side_effect = mock_open_file
        
        # Mock do SQLite para retornar histórico e estatísticas
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (5, 1686440000, 1686450000, "Live Test Contact")
        mock_cursor.fetchall.return_value = [
            (0, "Live Test Contact", "olá tudo bem"),
            (1, "André", "tudo bem e com vc")
        ]
        
        # Mock do LLM
        mock_classify.return_value = {
            "relationship": "AmigoProximo",
            "tone": "informal e amigável",
            "nickname": "Live",
            "pet_name": None,
            "frequent_greeting": "Eae",
            "summary": "Conversa de teste ao vivo.",
            "intent": "Interagir",
            "frequency": "diária",
            "product": None,
            "guidelines": "Seja descontraído."
        }
        
        with patch("whatsapp_manager._fetch_chat_history", return_value=""):
            res = pre_llm("pre_llm_call", context)
            
        # O LLM de classificação foi chamado on-the-fly
        mock_classify.assert_called_once()
        self.assertIsNotNone(res)
        self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", res["context"])
        self.assertIn("Nome do contato: Live Test Contact", res["context"])
        self.assertIn("Relação com o André: AmigoProximo", res["context"])

    def test_resolve_phone_from_jid_with_lid(self):
        """LID presente no cache deve ser convertido para JID de telefone clássico."""
        import whatsapp_manager
        # Preencher cache de LIDs artificialmente
        whatsapp_manager._lid_to_phone["164291240063173"] = "5511777777777"
        
        result = whatsapp_manager._resolve_phone_from_jid("164291240063173@lid")
        self.assertEqual(result, "5511777777777@s.whatsapp.net")
        
        # Com device suffix (formato LID:device@lid)
        result2 = whatsapp_manager._resolve_phone_from_jid("164291240063173:0@lid")
        self.assertEqual(result2, "5511777777777@s.whatsapp.net")
        
        # Limpar cache após teste
        whatsapp_manager._lid_to_phone.pop("164291240063173", None)

    def test_resolve_phone_from_jid_standard_jid_unchanged(self):
        """JIDs de telefone padrão devem passar sem alteração."""
        import whatsapp_manager
        result = whatsapp_manager._resolve_phone_from_jid("5511888888888@s.whatsapp.net")
        self.assertEqual(result, "5511888888888@s.whatsapp.net")
        
        # Com device suffix
        result2 = whatsapp_manager._resolve_phone_from_jid("5511888888888:3@s.whatsapp.net")
        self.assertEqual(result2, "5511888888888@s.whatsapp.net")

    def test_resolve_phone_from_jid_unknown_lid_returns_as_is(self):
        """LID não presente no cache deve ser retornado sem alteração de formato."""
        import whatsapp_manager
        # Garantir que o LID não está no cache
        whatsapp_manager._lid_to_phone.pop("999999999999999", None)
        
        result = whatsapp_manager._resolve_phone_from_jid("999999999999999@lid")
        self.assertEqual(result, "999999999999999@lid")

    def test_resolve_phone_from_jid_empty(self):
        """Verifica que _resolve_phone_from_jid trata entrada vazia."""
        import whatsapp_manager
        self.assertEqual(whatsapp_manager._resolve_phone_from_jid(""), "")
        self.assertIsNone(whatsapp_manager._resolve_phone_from_jid(None))

    def test_resolve_phone_from_jid_mapped_lid(self):
        """Verifica que _resolve_phone_from_jid resolve LIDs mapeados."""
        import whatsapp_manager
        whatsapp_manager._lid_to_phone["123456789012345"] = "5511988888888"
        try:
            res = whatsapp_manager._resolve_phone_from_jid("123456789012345@lid")
            self.assertEqual(res, "5511988888888@s.whatsapp.net")
        finally:
            whatsapp_manager._lid_to_phone.pop("123456789012345", None)

    def test_resolve_chat_id_with_internal_map(self):
        """Verifica _resolve_chat_id com entrada no dicionário _sender_to_chat."""
        import whatsapp_manager
        whatsapp_manager._sender_to_chat["test_sender@s.whatsapp.net"] = "resolved_chat@s.whatsapp.net"
        try:
            res = whatsapp_manager._resolve_chat_id("test_sender@s.whatsapp.net")
            self.assertEqual(res, "resolved_chat@s.whatsapp.net")
        finally:
            whatsapp_manager._sender_to_chat.pop("test_sender@s.whatsapp.net", None)

    def test_resolve_chat_id_fallback(self):
        """Verifica _resolve_chat_id fazendo fallback por split."""
        import whatsapp_manager
        res = whatsapp_manager._resolve_chat_id("5511999999999:2@s.whatsapp.net")
        self.assertEqual(res, "5511999999999@s.whatsapp.net")

    @patch("os.path.exists")
    @patch("builtins.open")
    def test_load_support_files_existing(self, mock_open, mock_exists):
        """Verifica se _load_support_files carrega arquivos quando eles existem."""
        import whatsapp_manager
        mock_exists.return_value = True
        
        mock_soul_open = unittest.mock.mock_open(read_data="custom soul rules")
        mock_rules_open = unittest.mock.mock_open(read_data="custom support rules")
        
        def mock_open_file(path, *args, **kwargs):
            if "SOUL_WHATSAPP.md" in str(path):
                return mock_soul_open(path, *args, **kwargs)
            return mock_rules_open(path, *args, **kwargs)
            
        mock_open.side_effect = mock_open_file
        
        soul, rules = whatsapp_manager._load_support_files()
        self.assertEqual(soul, "custom soul rules")
        self.assertEqual(rules, "custom support rules")

    @patch("os.path.exists", return_value=False)
    def test_load_support_files_missing(self, mock_exists):
        """Verifica se _load_support_files usa fallbacks quando arquivos não existem."""
        import whatsapp_manager
        soul, rules = whatsapp_manager._load_support_files()
        self.assertIn("chatbot de suporte", soul)
        self.assertIn("Chatkanban", rules)

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open")
    def test_load_personal_contacts_success(self, mock_open, mock_exists):
        """Verifica se _load_personal_contacts lê e sanitiza contatos."""
        import whatsapp_manager
        contacts_data = {
            "5511777777777@s.whatsapp.net": {
                "name": "Bruna",
                "relationship": "namorada",
                "pet_name": "pai",  # deve ser sanitizado para None
                "nickname": "Bru"
            }
        }
        mock_open.return_value.__enter__.return_value = MagicMock(read=lambda: json.dumps(contacts_data))
        
        res = whatsapp_manager._load_personal_contacts()
        self.assertIn("5511777777777@s.whatsapp.net", res)
        self.assertEqual(res["5511777777777@s.whatsapp.net"]["name"], "Bruna")
        self.assertIsNone(res["5511777777777@s.whatsapp.net"]["pet_name"])
        self.assertEqual(res["5511777777777@s.whatsapp.net"]["nickname"], "Bru")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", side_effect=OSError("Read error"))
    def test_load_personal_contacts_error(self, mock_open, mock_exists):
        """Verifica se _load_personal_contacts trata exceções de IO retornando dicionário vazio."""
        import whatsapp_manager
        res = whatsapp_manager._load_personal_contacts()
        self.assertEqual(res, {})


class TestMediaMessageProcessing(BaseWhatsAppManagerTest):
    def test_get_media_info_direct_attrs(self):
        """Verifica _get_media_info com atributos diretos no objeto."""
        event = MagicMock()
        event.has_media = True
        event.media_type = "ptt"
        event.media_urls = ["/path/to/voice.ogg"]
        event.message_id = "msg123"
        
        info = whatsapp_manager._get_media_info(event)
        self.assertTrue(info["has_media"])
        self.assertEqual(info["media_type"], "ptt")
        self.assertEqual(info["media_urls"], ["/path/to/voice.ogg"])
        self.assertEqual(info["message_id"], "msg123")

    def test_get_media_info_dict_payload(self):
        """Verifica _get_media_info com dicionário interno (raw_event)."""
        event = MagicMock(spec=[])
        event.raw_event = {
            "hasMedia": True,
            "mediaType": "image",
            "mediaUrls": "/path/to/photo.jpg",
            "messageId": "msg456"
        }
        
        info = whatsapp_manager._get_media_info(event)
        self.assertTrue(info["has_media"])
        self.assertEqual(info["media_type"], "image")
        self.assertEqual(info["media_urls"], ["/path/to/photo.jpg"])
        self.assertEqual(info["message_id"], "msg456")

    def test_get_mime_type(self):
        """Verifica se _get_mime_type retorna o tipo correto."""
        self.assertEqual(whatsapp_manager._get_mime_type("audio.ogg"), "audio/ogg")
        self.assertEqual(whatsapp_manager._get_mime_type("IMAGE.PNG"), "image/png")
        self.assertEqual(whatsapp_manager._get_mime_type("file.unknown"), "application/octet-stream")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b"mock-audio-data")
    @patch("os.remove")
    @patch("urllib.request.urlopen")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_process_media_message_audio(self, mock_urlopen, mock_remove, mock_open, mock_exists):
        """Verifica processamento de áudio com chamada do Gemini mockada."""
        event = MagicMock()
        event.has_media = True
        event.media_type = "ptt"
        event.media_urls = ["/path/to/voice.ogg"]
        
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "bom dia, tudo bem?"
                    }]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        text = whatsapp_manager._process_media_message(event)
        self.assertEqual(text, "bom dia, tudo bem?")
        mock_remove.assert_called_once_with("/path/to/voice.ogg")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b"mock-audio-data")
    @patch("os.remove")
    @patch("urllib.request.urlopen")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key", "WHATSAPP_CLIENT_MEDIA_MODEL": "gemini-custom-media-model"})
    def test_process_media_message_audio_custom_model(self, mock_urlopen, mock_remove, mock_open, mock_exists):
        """Verifica processamento de áudio usando o modelo configurado em WHATSAPP_CLIENT_MEDIA_MODEL."""
        event = MagicMock()
        event.has_media = True
        event.media_type = "ptt"
        event.media_urls = ["/path/to/voice.ogg"]
        
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "bom dia, tudo bem?"
                    }]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        text = whatsapp_manager._process_media_message(event)
        self.assertEqual(text, "bom dia, tudo bem?")
        
        # Verificar se a URL contém o modelo correto
        args, kwargs = mock_urlopen.call_args
        request_obj = args[0]
        self.assertIn("gemini-custom-media-model", request_obj.full_url)

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b"mock-image-data")
    @patch("os.remove")
    @patch("urllib.request.urlopen")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_process_media_message_multiple_images_limit(self, mock_urlopen, mock_remove, mock_open, mock_exists):
        """Verifica que o processamento de imagens se limita a no máximo 5 imagens por mensagem."""
        event = MagicMock()
        event.has_media = True
        event.media_type = "image"
        # 7 image paths
        event.media_urls = [f"/path/to/photo{i}.jpg" for i in range(7)]
        
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "Esta é uma descrição de 5 fotos unificadas."
                    }]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        text = whatsapp_manager._process_media_message(event)
        self.assertEqual(text, "Esta é uma descrição de 5 fotos unificadas.")
        
        # O open deve ter sido chamado exatamente 5 vezes (limite de 5 imagens)
        self.assertEqual(mock_open.call_count, 5)
        
        # O remove deve ter sido chamado exatamente 5 vezes
        self.assertEqual(mock_remove.call_count, 5)
        
        # Verificar se as chamadas de remoção correspondem aos primeiros 5 arquivos
        expected_removed = [unittest.mock.call(f"/path/to/photo{i}.jpg") for i in range(5)]
        mock_remove.assert_has_calls(expected_removed, any_order=True)

    @patch.dict(os.environ, {}, clear=True)
    def test_process_media_message_no_google_key(self):
        """Verifica que _process_media_message retorna None se GOOGLE_API_KEY estiver ausente."""
        import whatsapp_manager
        event = MagicMock()
        res = whatsapp_manager._process_media_message(event)
        self.assertIsNone(res)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_process_media_message_no_media(self):
        """Verifica que _process_media_message retorna None se o evento não contiver mídia."""
        import whatsapp_manager
        event = MagicMock()
        event.has_media = False
        res = whatsapp_manager._process_media_message(event)
        self.assertIsNone(res)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_process_media_message_unsupported_type(self):
        """Verifica que _process_media_message retorna None para tipos de mídia não suportados."""
        import whatsapp_manager
        event = MagicMock()
        event.has_media = True
        event.media_type = "video"
        event.media_urls = ["/path/to/video.mp4"]
        res = whatsapp_manager._process_media_message(event)
        self.assertIsNone(res)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    @patch("os.path.exists", return_value=False)
    def test_process_media_message_file_not_found(self, mock_exists):
        """Verifica que _process_media_message retorna None se o arquivo físico não existir."""
        import whatsapp_manager
        event = MagicMock()
        event.has_media = True
        event.media_type = "ptt"
        event.media_urls = ["/path/to/nonexistent.ogg"]
        res = whatsapp_manager._process_media_message(event)
        self.assertIsNone(res)

    @patch("whatsapp_manager._process_media_message", return_value="transcribed audio")
    @patch("whatsapp_manager._persist_transcription_to_db")
    @patch("pathlib.Path.exists", return_value=True)
    def test_pre_gateway_dispatch_media_audio(self, mock_exists, mock_persist, mock_process_media):
        """Verifica que pre_gateway_dispatch processa áudio, atualiza evento e persiste no banco."""
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        self.assertIsNotNone(pre_dispatch)

        event = MagicMock()
        event.source.platform = "whatsapp"
        event.has_media = True
        event.media_type = "ptt"
        event.media_urls = ["/path/to/voice.ogg"]
        event.message_id = "msg123"
        event.text = ""
        event.source.user_id = "5511888888888@s.whatsapp.net"
        event.source.chat_id = "5511888888888@s.whatsapp.net"

        gateway = MagicMock()
        gateway._session_key_for_source.return_value = "session_media"
        gateway._session_model_overrides = {}

        context = {"event": event, "gateway": gateway}

        with patch("whatsapp_manager._check_bot_paused", return_value=False), \
             patch("whatsapp_manager._check_chat_silenced", return_value=False):
            res = pre_dispatch("pre_gateway_dispatch", context)
            
        self.assertIsNone(res) # Não deve pular
        self.assertEqual(event.text, '[Áudio: "transcribed audio"]')
        mock_persist.assert_called_once_with("/opt/data/.hermes/whatsapp_messages.db", "msg123", '[Áudio: "transcribed audio"]')


class TestExternalServicesAndUpdates(BaseWhatsAppManagerTest):
    @patch("urllib.request.urlopen")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"})
    def test_classify_contact_via_llm_gemini(self, mock_urlopen):
        from whatsapp_manager import _classify_contact_via_llm
        
        # Mocking Gemini response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": '{"relationship": "amigo/namorada", "tone": "informal e carinhoso", "nickname": "Bru", "pet_name": "amor", "frequent_greeting": "Oi linda", "summary": "Conversa carinhosa", "intent": "Saudação", "frequency": "diária", "guidelines": "Seja romântico."}'
                    }]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = _classify_contact_via_llm("Bruna", "oi amor te amo", "Total messages: 10")
        self.assertEqual(res["relationship"], "amigo/namorada")
        self.assertEqual(res["nickname"], "Bru")
        self.assertEqual(res["pet_name"], "amor")
        self.assertEqual(res["frequent_greeting"], "Oi linda")
        self.assertEqual(res["summary"], "Conversa carinhosa")
        self.assertEqual(res["intent"], "Saudação")
        self.assertEqual(res["frequency"], "diária")

    @patch.dict(os.environ, {}, clear=True)
    def test_classify_contact_via_llm_fallback(self):
        from whatsapp_manager import _classify_contact_via_llm
        res = _classify_contact_via_llm("José", "olá tudo bem", "Total messages: 1")
        self.assertEqual(res["relationship"], "Cliente")
        self.assertEqual(res["tone"], "polido e profissional")
        self.assertIsNone(res["nickname"])

    @patch("whatsapp_manager.Path.exists")
    @patch("subprocess.run")
    @patch("urllib.request.urlopen")
    def test_self_update_plugin_code_git(self, mock_urlopen, mock_subrun, mock_exists):
        """Verifica que o auto-updater do plugin usa git fetch/reset quando .git existe."""
        mock_exists.return_value = True
        
        # Caso 1: hashes diferentes (deve atualizar)
        mock_result_local = MagicMock()
        mock_result_local.stdout = "local_commit_hash\n"
        mock_result_remote = MagicMock()
        mock_result_remote.stdout = "remote_commit_hash\n"
        
        mock_subrun.side_effect = [
            MagicMock(returncode=0), # fetch
            mock_result_local,       # local
            mock_result_remote,      # remote
            MagicMock(returncode=0)  # reset
        ]
        
        res = whatsapp_manager._self_update_plugin_code()
        self.assertTrue(res)
        
        # Caso 2: hashes iguais (não deve atualizar)
        mock_subrun.side_effect = [
            MagicMock(returncode=0), # fetch
            mock_result_local,       # local
            mock_result_local,       # remote (mesmo hash)
        ]
        res = whatsapp_manager._self_update_plugin_code()
        self.assertFalse(res)
        
        # Caso 3: git falha, deve cair no fallback HTTP
        mock_subrun.side_effect = Exception("Git fail")
        mock_response = MagicMock()
        mock_response.read.return_value = b"mock content"
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        with patch("pathlib.Path.read_bytes", return_value=b"different content"), \
             patch("pathlib.Path.write_bytes") as mock_write, \
             patch("pathlib.Path.mkdir"):
            res = whatsapp_manager._self_update_plugin_code()
            self.assertTrue(res)

    @patch("urllib.request.urlopen")
    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-google-key", "WHATSAPP_CONTACT_CLASSIFIER_MODEL": "gemini-1.5-pro-test"})
    def test_classify_contact_custom_model(self, mock_urlopen):
        """Verifica que o classificador de contatos utiliza o modelo configurado no ambiente."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": '{"relationship": "Amigo", "tone": "informal", "nickname": null, "pet_name": null, "frequent_greeting": null, "summary": "resumo", "intent": "intencao", "frequency": "diaria", "product": null, "guidelines": "seja gentil"}'
                    }]
                }
            }]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = whatsapp_manager._classify_contact_via_llm("Carlos", "history", "stats")
        self.assertEqual(res["relationship"], "Amigo")
        
        called_args = mock_urlopen.call_args[0]
        called_req = called_args[0]
        self.assertIn("gemini-1.5-pro-test", called_req.full_url)


class TestUtilityFunctionsAndLogs(BaseWhatsAppManagerTest):
    @patch("urllib.request.urlopen")
    def test_check_bot_paused_updates_lid_cache(self, mock_urlopen):
        """_check_bot_paused deve atualizar _lid_to_phone quando a resposta contiver lidToPhone."""
        import whatsapp_manager
        
        bridge_response = {
            "botPaused": False,
            "lidToPhone": {
                "111111111111111": "5511222222222",
                "333333333333333": "5511444444444"
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(bridge_response).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        whatsapp_manager._lid_to_phone.clear()
        
        result = whatsapp_manager._check_bot_paused()
        
        self.assertFalse(result)
        self.assertEqual(whatsapp_manager._lid_to_phone.get("111111111111111"), "5511222222222")
        self.assertEqual(whatsapp_manager._lid_to_phone.get("333333333333333"), "5511444444444")
        whatsapp_manager._lid_to_phone.clear()

    def test_custom_print_redirection(self):
        """Verifica que WARNING+/ERROR vão para stderr e INFO vai para stdout via _WMLogHandler."""
        import sys
        import logging
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        try:
            import whatsapp_manager
            whatsapp_manager.logger.warning("⚠️ Test warning message")
            whatsapp_manager.logger.info("Regular info message")
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        stderr_val = stderr_capture.getvalue()
        stdout_val = stdout_capture.getvalue()

        self.assertIn("Test warning message", stderr_val, "WARNING deveria ir para stderr")
        self.assertNotIn("Test warning message", stdout_val)
        self.assertIn("Regular info message", stdout_val, "INFO deveria ir para stdout")
        self.assertNotIn("Regular info message", stderr_val)

    @patch("sqlite3.connect")
    def test_update_db_message(self, mock_connect):
        """Verifica a atualização do banco SQLite com detecção dinâmica de colunas."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (0, "chat_id", "TEXT", 0, None, 0),
            (1, "message_id", "TEXT", 0, None, 0),
            (2, "body", "TEXT", 0, None, 0)
        ]
        mock_cursor.rowcount = 1
        
        updated = whatsapp_manager._update_db_message("/dummy/path.db", "msg123", "new body text")
        self.assertEqual(updated, 1)
        mock_cursor.execute.assert_any_call("PRAGMA table_info(messages)")
        mock_cursor.execute.assert_any_call("UPDATE messages SET body = ? WHERE message_id = ?", ("new body text", "msg123"))

    @patch("sqlite3.connect")
    def test_update_db_message_alternative_msg_id(self, mock_connect):
        """Verifica _update_db_message com coluna de ID msg_id."""
        import whatsapp_manager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (0, "chat_id", "TEXT", 0, None, 0),
            (1, "msg_id", "TEXT", 0, None, 0),
            (2, "body", "TEXT", 0, None, 0)
        ]
        mock_cursor.rowcount = 1
        
        res = whatsapp_manager._update_db_message("/dummy.db", "msg123", "new body")
        self.assertEqual(res, 1)
        mock_cursor.execute.assert_any_call("UPDATE messages SET body = ? WHERE msg_id = ?", ("new body", "msg123"))

    @patch("sqlite3.connect")
    def test_update_db_message_alternative_id(self, mock_connect):
        """Verifica _update_db_message com coluna de ID id."""
        import whatsapp_manager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (0, "chat_id", "TEXT", 0, None, 0),
            (1, "id", "TEXT", 0, None, 0),
            (2, "body", "TEXT", 0, None, 0)
        ]
        mock_cursor.rowcount = 1
        
        res = whatsapp_manager._update_db_message("/dummy.db", "msg123", "new body")
        self.assertEqual(res, 1)
        mock_cursor.execute.assert_any_call("UPDATE messages SET body = ? WHERE id = ?", ("new body", "msg123"))

    @patch("sqlite3.connect")
    def test_update_db_message_no_id_column(self, mock_connect):
        """Verifica que _update_db_message retorna -1 quando não há nenhuma coluna de ID."""
        import whatsapp_manager
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (0, "chat_id", "TEXT", 0, None, 0),
            (1, "body", "TEXT", 0, None, 0)
        ]
        
        res = whatsapp_manager._update_db_message("/dummy.db", "msg123", "new body")
        self.assertEqual(res, -1)

    @patch("sqlite3.connect", side_effect=Exception("Connection error"))
    def test_update_db_message_error(self, mock_connect):
        """Verifica que _update_db_message retorna -2 quando ocorre uma exceção."""
        import whatsapp_manager
        res = whatsapp_manager._update_db_message("/dummy.db", "msg123", "new body")
        self.assertEqual(res, -2)

    @patch("whatsapp_manager._update_db_message")
    @patch("threading.Thread")
    def test_persist_transcription_to_db_immediate(self, mock_thread, mock_update):
        """Verifica que _persist_transcription_to_db não cria thread se a inserção for imediata."""
        import whatsapp_manager
        mock_update.return_value = 1
        whatsapp_manager._persist_transcription_to_db("/dummy.db", "msg123", "body")
        mock_thread.assert_not_called()

    @patch("whatsapp_manager._update_db_message")
    def test_persist_transcription_to_db_bg_retry(self, mock_update):
        """Verifica que _persist_transcription_to_db lança thread e retenta se retorno for 0."""
        import whatsapp_manager
        mock_update.side_effect = [0, 1]
        
        with patch("time.sleep") as mock_sleep:
            whatsapp_manager._persist_transcription_to_db("/dummy.db", "msg123", "body")
            
            import time
            for _ in range(20):
                if mock_update.call_count >= 2:
                    break
                time.sleep(0.01)
                
            self.assertEqual(mock_update.call_count, 2)
            mock_sleep.assert_called_with(1)


if __name__ == "__main__":
    unittest.main()
