"""Testes do suporte a múltiplos administradores (WHATSAPP_ADMIN_NUMBERS)."""

import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import whatsapp_manager
from whatsapp_manager import _is_admin_number


class AdminNumbersTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "WHATSAPP_OWNER_NUMBER": "5591984059376",
            "WHATSAPP_ADMIN_NUMBERS": "5591992748657, 5511987654321",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_owner_is_admin(self):
        self.assertTrue(_is_admin_number("5591984059376"))

    def test_extra_admins(self):
        self.assertTrue(_is_admin_number("5591992748657"))
        self.assertTrue(_is_admin_number("5511987654321"))

    def test_admin_jid_format(self):
        self.assertTrue(_is_admin_number("5591992748657@s.whatsapp.net"))
        self.assertTrue(_is_admin_number("5591992748657:12@s.whatsapp.net"))

    def test_admin_without_ninth_digit(self):
        # JIDs do WhatsApp às vezes vêm sem o 9º dígito
        self.assertTrue(_is_admin_number("559192748657"))
        self.assertTrue(_is_admin_number("559184059376"))

    def test_stranger_is_not_admin(self):
        self.assertFalse(_is_admin_number("5599911112222"))
        self.assertFalse(_is_admin_number(""))
        self.assertFalse(_is_admin_number(None))

    def test_only_owner_when_no_admins(self):
        with patch.dict(os.environ, {"WHATSAPP_ADMIN_NUMBERS": ""}):
            self.assertTrue(_is_admin_number("5591984059376"))
            self.assertFalse(_is_admin_number("5591992748657"))

    def test_config_parses_list(self):
        self.assertEqual(
            whatsapp_manager.config.whatsapp_admin_numbers,
            ["5591992748657", "5511987654321"],
        )


if __name__ == "__main__":
    unittest.main()
