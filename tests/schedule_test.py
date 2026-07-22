"""Testes do expediente do bot (WHATSAPP_HUMAN_HOURS + modos)."""

import os
import sys
import json
import datetime
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import whatsapp_manager as wm

SPEC = "seg-sab=08:00-12:00,14:00-18:00"


def dt(weekday: int, hour: int, minute: int = 0):
    """datetime num dia da semana específico (0=segunda ... 6=domingo)."""
    base = datetime.datetime(2026, 7, 20)  # 20/07/2026 é segunda-feira
    return (base + datetime.timedelta(days=weekday)).replace(hour=hour, minute=minute)


class ParseTest(unittest.TestCase):
    def test_parse_basico(self):
        sched = wm._parse_human_hours(SPEC)
        self.assertEqual(sorted(sched.keys()), [0, 1, 2, 3, 4, 5])  # seg a sab
        self.assertEqual(sched[0], [(480, 720), (840, 1080)])
        self.assertNotIn(6, sched)  # domingo sem horário humano

    def test_parse_vazio(self):
        self.assertEqual(wm._parse_human_hours(""), {})
        self.assertEqual(wm._parse_human_hours("lixo"), {})


class InHumanHoursTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "bot_schedule.json")
        self.p1 = patch.dict(os.environ, {"WHATSAPP_HUMAN_HOURS": SPEC})
        self.p2 = patch.object(wm, "_SCHEDULE_STATE_PATH", self.state)
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def test_horario_humano_seg_manha(self):
        self.assertTrue(wm._in_human_hours(dt(0, 9)))     # seg 09:00 → equipe
        self.assertTrue(wm._in_human_hours(dt(5, 15)))    # sab 15:00 → equipe

    def test_bot_ativo(self):
        self.assertFalse(wm._in_human_hours(dt(0, 12, 30)))  # almoço → bot
        self.assertFalse(wm._in_human_hours(dt(0, 19)))      # noite → bot
        self.assertFalse(wm._in_human_hours(dt(0, 7)))       # madrugada → bot
        self.assertFalse(wm._in_human_hours(dt(6, 10)))      # domingo → bot

    def test_modo_24_7_ignora_horario(self):
        wm._save_schedule_mode("24_7")
        self.assertFalse(wm._in_human_hours(dt(0, 9)))    # mesmo às 9h de segunda

    def test_volta_ao_expediente(self):
        wm._save_schedule_mode("24_7")
        wm._save_schedule_mode("expediente")
        self.assertTrue(wm._in_human_hours(dt(0, 9)))

    def test_sem_spec_sempre_ativo(self):
        with patch.dict(os.environ, {"WHATSAPP_HUMAN_HOURS": ""}):
            self.assertFalse(wm._in_human_hours(dt(0, 9)))


if __name__ == "__main__":
    unittest.main()
