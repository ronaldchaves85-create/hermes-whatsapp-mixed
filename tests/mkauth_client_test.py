"""Testes unitários do mkauth_client (API MK-AUTH mockada)."""

import os
import json
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import mkauth_client
from mkauth_client import (
    MkAuthClient,
    MkAuthError,
    normalize_phone,
    normalize_cpf,
    detect_billing_intent,
    extract_cpf_from_text,
    format_titulo,
    build_mkauth_context_block,
)

ENV = {
    "MKAUTH_URL": "https://mkauth.local",
    "MKAUTH_CLIENT_ID": "botwpp",
    "MKAUTH_CLIENT_SECRET": "s3cr3t",
    "MKAUTH_ENRICH": "false",  # sem threads de fundo nos testes
}

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib3QifQ.abc123sig"


def _mock_response(body: str):
    resp = MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class NormalizationTest(unittest.TestCase):
    def test_phone_strips_ddi_and_ninth_digit(self):
        self.assertEqual(normalize_phone("5511998765432"), "1198765432")
        self.assertEqual(normalize_phone("+55 (11) 99876-5432"), "1198765432")
        self.assertEqual(normalize_phone("11998765432"), "1198765432")
        self.assertEqual(normalize_phone("1198765432"), "1198765432")

    def test_phone_landline_kept(self):
        self.assertEqual(normalize_phone("1133334444"), "1133334444")
        self.assertEqual(normalize_phone("551133334444"), "1133334444")

    def test_phone_empty(self):
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone(None), "")

    def test_cpf(self):
        self.assertEqual(normalize_cpf("123.456.789-09"), "12345678909")
        self.assertEqual(normalize_cpf("12345678909"), "12345678909")


class BillingIntentTest(unittest.TestCase):
    def test_positive(self):
        for msg in [
            "me manda o boleto",
            "preciso da 2ª via",
            "segunda via da fatura por favor",
            "quanto tá minha fatura?",
            "meu plano venceu ontem",
            "qual o pix pra pagar?",
            "minha mensalidade atrasada",
            "quero o código de barras",
            "quanto devo?",
        ]:
            self.assertTrue(detect_billing_intent(msg), msg)

    def test_negative(self):
        for msg in [
            "minha internet caiu",
            "bom dia, tudo bem?",
            "o wifi está lento",
            "",
        ]:
            self.assertFalse(detect_billing_intent(msg), msg)

    def test_extract_cpf(self):
        self.assertEqual(extract_cpf_from_text("meu cpf é 123.456.789-09"), "12345678909")
        self.assertEqual(extract_cpf_from_text("12345678909"), "12345678909")
        self.assertIsNone(extract_cpf_from_text("não sei meu cpf"))


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.client = MkAuthClient()

    def tearDown(self):
        self.env.stop()

    @patch("mkauth_client.urllib.request.urlopen")
    def test_token_via_basic_auth_and_cached(self, mock_open):
        mock_open.return_value = _mock_response(json.dumps({"token": FAKE_JWT}))
        t1 = self.client.get_token()
        t2 = self.client.get_token()
        self.assertEqual(t1, FAKE_JWT)
        self.assertEqual(t2, FAKE_JWT)
        self.assertEqual(mock_open.call_count, 1)  # segunda chamada veio do cache
        req = mock_open.call_args_list[0][0][0]
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))
        self.assertEqual(req.full_url, "https://mkauth.local/api/")

    @patch("mkauth_client.urllib.request.urlopen")
    def test_token_raw_body(self, mock_open):
        mock_open.return_value = _mock_response(FAKE_JWT)
        self.assertEqual(self.client.get_token(), FAKE_JWT)

    def test_token_without_config_raises(self):
        with patch.dict(os.environ, {"MKAUTH_URL": "", "MKAUTH_CLIENT_ID": "", "MKAUTH_CLIENT_SECRET": ""}):
            with self.assertRaises(MkAuthError):
                MkAuthClient().get_token()


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.client = MkAuthClient()
        self.client._token = FAKE_JWT
        self.client._token_ts = 9e12  # nunca expira no teste

    def tearDown(self):
        self.env.stop()

    @patch("mkauth_client.urllib.request.urlopen")
    def test_bearer_header(self, mock_open):
        mock_open.return_value = _mock_response(json.dumps({"clientes": []}))
        self.client._request("GET", "/api/cliente/listar/pagina=1")
        req = mock_open.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), f"Bearer {FAKE_JWT}")


class ClientLookupTest(unittest.TestCase):
    CLIENTES = [
        {"nome": "Maria Souza", "login": "maria", "cpf_cnpj": "123.456.789-09",
         "celular": "(11) 99876-5432", "plano": "FIBRA_100MB", "cli_ativado": "s"},
        {"nome": "João Lima", "login": "joao", "cpf_cnpj": "987.654.321-00",
         "telefone": "1133334444", "plano": "FIBRA_50MB", "cli_ativado": "s"},
    ]

    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.client = MkAuthClient()
        self.client._clients = list(self.CLIENTES)
        self.client._clients_ts = 9e12  # cache "fresco" — sem HTTP
        # monta índices manualmente como refresh_clients_cache faria
        for cli in self.CLIENTES:
            for f in ("celular", "telefone"):
                n = normalize_phone(str(cli.get(f, "")))
                if len(n) >= 10:
                    self.client._phone_index[n] = cli
            self.client._cpf_index[normalize_cpf(cli["cpf_cnpj"])] = cli

    def tearDown(self):
        self.env.stop()

    def test_find_by_whatsapp_number_with_ddi(self):
        cli = self.client.find_client_by_phone("5511998765432")
        self.assertIsNotNone(cli)
        self.assertEqual(cli["login"], "maria")

    def test_find_by_landline(self):
        cli = self.client.find_client_by_phone("551133334444")
        self.assertEqual(cli["login"], "joao")

    def test_find_by_cpf(self):
        cli = self.client.find_client_by_cpf("987.654.321-00")
        self.assertEqual(cli["login"], "joao")

    def test_not_found(self):
        self.assertIsNone(self.client.find_client_by_phone("5599911112222"))


class TitulosTest(unittest.TestCase):
    TITULOS = [
        {"valor": "89.90", "datavenc": "2026-07-10", "status": "vencido",
         "linhadig": "23790.12345 60000.123456 78901.234567 8 91230000008990"},
        {"valor": "89.90", "datavenc": "2026-08-10", "status": "aberto",
         "pix": "00020126580014BR.GOV.BCB.PIX..."},
        {"valor": "89.90", "datavenc": "2026-06-10", "status": "pago"},
    ]

    def test_filter_abertos_sorts_and_drops_paid(self):
        abertos = MkAuthClient.filter_titulos_abertos(self.TITULOS)
        self.assertEqual(len(abertos), 2)
        self.assertEqual(abertos[0]["datavenc"], "2026-07-10")  # vencido primeiro

    def test_format_titulo(self):
        out = format_titulo(self.TITULOS[0])
        self.assertIn("R$ 89,90", out)
        self.assertIn("10/07/2026", out)
        self.assertIn("Linha digitável", out)

    def test_format_titulo_pix(self):
        out = format_titulo(self.TITULOS[1])
        self.assertIn("PIX copia e cola", out)


class ContextBlockTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_disabled_returns_empty(self):
        with patch.dict(os.environ, {"MKAUTH_URL": "", "MKAUTH_CLIENT_ID": "", "MKAUTH_CLIENT_SECRET": ""}):
            self.assertEqual(build_mkauth_context_block("5511998765432"), "")

    @patch.object(mkauth_client.client, "find_client_by_phone", return_value=None)
    @patch.object(mkauth_client.client, "find_client_by_cpf", return_value=None)
    def test_unknown_number_asks_cpf(self, *_):
        block = build_mkauth_context_block("5511998765432", "me manda o boleto")
        self.assertIn("NÃO foi localizado", block)
        self.assertIn("CPF", block)

    @patch.object(mkauth_client.client, "get_titulos_by_cpf")
    @patch.object(mkauth_client.client, "find_client_by_phone")
    def test_known_client_with_open_invoice(self, mock_find, mock_titulos):
        mock_find.return_value = {"nome": "Maria Souza", "login": "maria",
                                  "cpf_cnpj": "12345678909", "plano": "FIBRA_100MB",
                                  "cli_ativado": "s"}
        mock_titulos.return_value = [
            {"valor": "89.90", "datavenc": "2026-07-25", "status": "aberto",
             "linhadig": "23790..."},
        ]
        block = build_mkauth_context_block("5511998765432", "quero a 2 via")
        self.assertIn("Maria Souza", block)
        self.assertIn("Faturas em aberto (1)", block)
        self.assertIn("R$ 89,90", block)
        self.assertIn("nunca invente", block)

    @patch.object(mkauth_client.client, "get_titulos_by_cpf", return_value=[])
    @patch.object(mkauth_client.client, "find_client_by_phone")
    def test_known_client_all_paid(self, mock_find, _):
        mock_find.return_value = {"nome": "João", "login": "joao", "cpf_cnpj": "98765432100"}
        block = build_mkauth_context_block("551133334444", "tem boleto aberto?")
        self.assertIn("Nenhuma fatura em aberto", block)

    @patch.object(mkauth_client.client, "find_client_by_phone", side_effect=RuntimeError("boom"))
    def test_never_raises(self, _):
        self.assertEqual(build_mkauth_context_block("5511998765432", "boleto"), "")


if __name__ == "__main__":
    unittest.main()


class BindingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.env = patch.dict(os.environ, {**ENV, "MKAUTH_DATA_DIR": self.tmp,
                                           "MKAUTH_ENRICH": "false"})
        self.env.start()
        self.client = mkauth_client.MkAuthClient()
        self.client._clients = [{"login": "maria", "nome": "Maria"}]  # cache "fresco"
        self.client._clients_ts = 9e12  # sem HTTP
        self.client._login_index = {"maria": {"login": "maria", "nome": "Maria"}}

    def tearDown(self):
        self.env.stop()

    def test_binding_saved_and_used(self):
        self.client.save_binding("5511998765432", "maria")
        # novo cliente (simula restart) deve carregar o vínculo do disco
        c2 = mkauth_client.MkAuthClient()
        c2._clients = [{"login": "maria", "nome": "Maria"}]
        c2._clients_ts = 9e12
        c2._login_index = {"maria": {"login": "maria", "nome": "Maria"}}
        cli = c2.find_client_by_phone("5511998765432")
        self.assertIsNotNone(cli)
        self.assertEqual(cli["nome"], "Maria")

    def test_enriched_index_used(self):
        self.client._load_persisted()
        self.client._phone_to_login["1198765432"] = "maria"
        cli = self.client.find_client_by_phone("+55 11 99876-5432")
        self.assertEqual(cli["login"], "maria")


class TitulosCacheTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.client = mkauth_client.MkAuthClient()

    def tearDown(self):
        self.env.stop()

    @patch.object(mkauth_client.MkAuthClient, "_request")
    def test_titulos_cached(self, mock_req):
        self.client._token = FAKE_JWT
        self.client._token_ts = 9e12
        mock_req.return_value = {"titulos": [{"titulo": "1", "valor": "10", "status": "aberto"}]}
        t1 = self.client._get_all_titulos()
        t2 = self.client._get_all_titulos()
        self.assertEqual(len(t1), 1)
        self.assertEqual(t1, t2)
        self.assertEqual(mock_req.call_count, 1)  # segunda veio do cache
