"""Python unit tests for the whatsapp-manager plugin."""

import os
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
             patch("whatsapp_manager._ensure_google_libs"):
            register(self.ctx)

    def tearDown(self):
        self.env_patcher.stop()

    async def test_owner_message_identification(self):
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

        res = await pre_dispatch("pre_gateway_dispatch", context)
        self.assertIsNone(res) # Owner message is not skipped
        self.assertEqual(gateway._session_model_overrides["session_1"]["model"], "gemini-3.5-flash-owner")

    async def test_client_message_when_bot_paused(self):
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
            res = await pre_dispatch("pre_gateway_dispatch", context)
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

    async def test_pre_gateway_dispatch_does_not_rewrite_or_fetch(self):
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
            res = await pre_dispatch("pre_gateway_dispatch", context)
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

        personal_json = '{"5511777777777@s.whatsapp.net": {"name": "Bruna", "relationship": "namorada", "tone": "romantico", "guidelines": "Seja fofo"}}'
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

    async def test_pre_gateway_dispatch_non_whatsapp_ignored(self):
        pre_dispatch = self.ctx.hooks.get("pre_gateway_dispatch")
        
        event = MagicMock()
        event.source.platform = "telegram"
        context = {
            "event": event,
            "gateway": MagicMock()
        }
        res = await pre_dispatch("pre_gateway_dispatch", context)
        self.assertIsNone(res)

    async def test_missing_model_env_vars_fallback(self):
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
            res = await pre_dispatch("pre_gateway_dispatch", context)
            self.assertIsNone(res)
            self.assertEqual(gateway._session_model_overrides["session_x"]["model"], "gemini-3.5-flash")

    async def test_missing_session_key_handled_gracefully(self):
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

        res = await pre_dispatch("pre_gateway_dispatch", context)
        # Should not raise exception
        self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
