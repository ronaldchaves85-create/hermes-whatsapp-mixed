"""Python unit tests for the whatsapp-manager plugin."""

import os
import json
import unittest
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add root directory to sys.path to import whatsapp_manager
sys.path.append(str(Path(__file__).parent.parent))

from whatsapp_manager import register

class MockContext:
    def __init__(self):
        self.hooks = {}
        self.skills = {}

    def register_hook(self, name, func):
        self.hooks[name] = func

    def register_skill(self, name, path):
        self.skills[name] = path

class TestWhatsAppManagerPlugin(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = MockContext()
        # Mock os.environ
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

        personal_json = '{"5511777777777@s.whatsapp.net": {"name": "Bruna", "relationship": "namorada", "tone": "romantico", "guidelines": "Seja fofo", "summary": "Conversa carinhosa.", "intent": "Amor", "frequency": "diária"}}'
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
            self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", res["context"])
            self.assertIn("Nome do contato: Bruna", res["context"])
            self.assertIn("Relação com o André: namorada", res["context"])
            self.assertIn("Tom de voz recomendado: romantico", res["context"])
            self.assertIn("Diretrizes específicas: Seja fofo", res["context"])

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
            self.assertEqual(gateway._session_model_overrides["session_x"]["model"], "gemini-3.5-flash")

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
        self.assertEqual(res["relationship"], "cliente/contato")
        self.assertEqual(res["tone"], "polido e profissional")
        self.assertIsNone(res["nickname"])

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
                "frequency": "semanal"
            }
        }
        
        mock_file = unittest.mock.mock_open(read_data=json.dumps(existing_contacts))
        mock_open.side_effect = lambda path, *args, **kwargs: mock_file(path, *args, **kwargs)
        
        # Mock SQLite cursor and fetchall results
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_sqlite_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Mock rows returned by SELECT
        mock_cursor.fetchall.side_effect = [
            [
                ("5511777777777@s.whatsapp.net", "Bruna", 10, 1686440000, 1686450000), # Stale contact
                ("5511888888888@s.whatsapp.net", "Carlos", 5, 1686440000, 1686450000) # Non-stale contact (skipped)
            ],
            [(0, "Bruna", "oi amor te amo")] # last 15 messages body (from_me=0, sender_name=Bruna, body)
        ]
        
        # Mock LLM classification response
        mock_classify.return_value = {
            "relationship": "amigo/namorada",
            "tone": "informal e carinhoso",
            "nickname": "Bru",
            "pet_name": "amor",
            "frequent_greeting": "Oi linda",
            "summary": "Conversa carinhosa",
            "intent": "Saudação",
            "frequency": "diária",
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
        # Stale fields are updated by LLM classification
        self.assertEqual(bruna_data["name"], "Bruna")
        self.assertEqual(bruna_data["relationship"], "amigo/namorada")
        self.assertEqual(bruna_data["tone"], "informal e carinhoso")
        self.assertEqual(bruna_data["guidelines"], "Seja romântico.")
        # New fields are populated
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
            "relationship": "amigo/namorada",
            "tone": "informal e amigável",
            "nickname": "Live",
            "pet_name": None,
            "frequent_greeting": "Eae",
            "summary": "Conversa de teste ao vivo.",
            "intent": "Interagir",
            "frequency": "diária",
            "guidelines": "Seja descontraído."
        }
        
        with patch("whatsapp_manager._fetch_chat_history", return_value=""):
            res = pre_llm("pre_llm_call", context)
            
        # O LLM de classificação foi chamado on-the-fly
        mock_classify.assert_called_once()
        self.assertIsNotNone(res)
        self.assertIn("RESPONDENDO COMO ANDRÉ ALENCAR", res["context"])
        self.assertIn("Nome do contato: Live Test Contact", res["context"])
        self.assertIn("Relação com o André: amigo/namorada", res["context"])

if __name__ == "__main__":
    unittest.main()
