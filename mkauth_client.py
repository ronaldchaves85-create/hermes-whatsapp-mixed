"""Cliente da API MK-AUTH para o plugin whatsapp-manager.

Integra o bot de WhatsApp com o MK-AUTH (gestão de provedores):
  - Autenticação JWT (Basic Auth em GET /api/ → Bearer token nas demais rotas)
  - Consulta de clientes (/api/cliente/*) com cache local e índice por telefone
  - Consulta de títulos/boletos (/api/titulo/*) por CPF
  - Detecção de intenção de cobrança na mensagem do cliente
  - Montagem do bloco de contexto injetado no prompt do LLM

Variáveis de ambiente:
  MKAUTH_URL            URL base do MK-AUTH (ex: https://192.168.0.10) — HTTPS obrigatório p/ token
  MKAUTH_CLIENT_ID      Client ID criado no painel do MK-AUTH
  MKAUTH_CLIENT_SECRET  Client Secret correspondente
  MKAUTH_VERIFY_SSL     "false" para aceitar certificado autoassinado local (padrão: false)
  MKAUTH_SYNC_INTERVAL_H  Intervalo (horas) de refresh do cache de clientes (padrão: 6)
  MKAUTH_TOKEN_TTL_MIN  Validade assumida do token JWT em minutos (padrão: 50)

Uso típico (dentro do whatsapp_manager):
    import mkauth_client
    if mkauth_client.detect_billing_intent(user_msg):
        block = mkauth_client.build_mkauth_context_block(phone_number, user_msg)
"""

import os
import re
import ssl
import sys
import json
import time
import base64
import logging
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("mkauth_client")
# Handler próprio para os logs aparecerem no stdout do container (docker logs)
if not logger.handlers:
    _mk_handler = logging.StreamHandler(sys.stderr)
    _mk_handler.setFormatter(logging.Formatter("[mkauth] %(levelname)s %(message)s"))
    logger.addHandler(_mk_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# ─────────────────────────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────────────────────────


class MkAuthConfig:
    """Lê a configuração das variáveis de ambiente a cada acesso (testável)."""

    @property
    def url(self) -> str:
        return os.environ.get("MKAUTH_URL", "").rstrip("/")

    @property
    def client_id(self) -> str:
        return os.environ.get("MKAUTH_CLIENT_ID", "")

    @property
    def client_secret(self) -> str:
        return os.environ.get("MKAUTH_CLIENT_SECRET", "")

    @property
    def verify_ssl(self) -> bool:
        return os.environ.get("MKAUTH_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")

    @property
    def sync_interval_s(self) -> int:
        try:
            return int(float(os.environ.get("MKAUTH_SYNC_INTERVAL_H", "6")) * 3600)
        except ValueError:
            return 6 * 3600

    @property
    def token_ttl_s(self) -> int:
        try:
            return int(float(os.environ.get("MKAUTH_TOKEN_TTL_MIN", "50")) * 60)
        except ValueError:
            return 50 * 60

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.client_id and self.client_secret)


config = MkAuthConfig()


class MkAuthError(Exception):
    """Erro de comunicação ou autenticação com a API do MK-AUTH."""


# ─────────────────────────────────────────────────────────────────────────────
# Normalização (telefone / CPF / texto)
# ─────────────────────────────────────────────────────────────────────────────


def normalize_phone(phone: str) -> str:
    """Normaliza telefone BR para comparação: só dígitos, sem DDI 55, sem o 9º dígito.

    Ex.: "+55 (11) 99876-5432" → "1198765432"
         "5511998765432"        → "1198765432"
         "1133334444" (fixo)    → "1133334444"
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    # Celular com 9º dígito: DDD (2) + 9 + 8 dígitos = 11 dígitos
    if len(digits) == 11 and digits[2] == "9":
        digits = digits[:2] + digits[3:]
    return digits


def normalize_cpf(cpf: str) -> str:
    """Remove pontuação do CPF/CNPJ: '123.456.789-09' → '12345678909'."""
    return re.sub(r"\D", "", cpf or "")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")


# ─────────────────────────────────────────────────────────────────────────────
# Cliente HTTP da API
# ─────────────────────────────────────────────────────────────────────────────


class MkAuthClient:
    def __init__(self):
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._lock = threading.Lock()
        # Cache de clientes: lista de dicts + índices
        self._clients: list[dict] = []
        self._clients_ts: float = 0.0
        self._phone_index: dict[str, dict] = {}
        self._cpf_index: dict[str, dict] = {}

    # ── HTTP ────────────────────────────────────────────────────────────────

    def _ssl_context(self):
        if config.verify_ssl:
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _urlopen(self, req, timeout: int = 15):
        ctx = self._ssl_context()
        if ctx is not None:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return urllib.request.urlopen(req, timeout=timeout)

    def get_token(self, force: bool = False) -> str:
        """Obtém (e cacheia) o token JWT via Basic Auth em GET /api/."""
        with self._lock:
            if not force and self._token and (time.time() - self._token_ts) < config.token_ttl_s:
                return self._token

            if not config.enabled:
                raise MkAuthError("MK-AUTH não configurado (MKAUTH_URL / MKAUTH_CLIENT_ID / MKAUTH_CLIENT_SECRET).")

            creds = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()
            req = urllib.request.Request(
                f"{config.url}/api/",
                headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
                method="GET",
            )
            try:
                with self._urlopen(req) as resp:
                    body = resp.read().decode("utf-8", errors="replace").strip()
            except urllib.error.HTTPError as e:
                raise MkAuthError(f"Falha ao gerar token (HTTP {e.code}). Verifique client_id/secret e HTTPS.") from e
            except (urllib.error.URLError, OSError) as e:
                raise MkAuthError(f"MK-AUTH inacessível em {config.url}: {e}") from e

            token = self._extract_token(body)
            if not token:
                raise MkAuthError(f"Resposta inesperada ao gerar token: {body[:120]!r}")

            self._token = token
            self._token_ts = time.time()
            logger.info("[mkauth] Token JWT renovado.")
            return token

    @staticmethod
    def _extract_token(body: str) -> str | None:
        """Extrai o JWT da resposta — aceita JSON {token|jwt|access_token} ou corpo cru."""
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                for key in ("token", "jwt", "access_token", "Token"):
                    if data.get(key):
                        return str(data[key]).strip()
            if isinstance(data, str) and data.count(".") == 2:
                return data.strip()
        except (ValueError, TypeError):
            pass
        # Corpo cru parecendo um JWT (header.payload.signature)
        candidate = body.strip().strip('"')
        if candidate.count(".") == 2 and len(candidate) > 20:
            return candidate
        return None

    def _request(self, method: str, path: str, payload: dict | None = None, _retry: bool = True):
        """Chamada autenticada. Renova o token e tenta 1x novamente em caso de 401."""
        token = self.get_token()
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{config.url}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with self._urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                logger.info("[mkauth] 401 — renovando token e repetindo a chamada.")
                self.get_token(force=True)
                return self._request(method, path, payload, _retry=False)
            raise MkAuthError(f"HTTP {e.code} em {path}") from e
        except (urllib.error.URLError, OSError) as e:
            raise MkAuthError(f"Erro de rede em {path}: {e}") from e

        try:
            return json.loads(body)
        except ValueError:
            raise MkAuthError(f"Resposta não-JSON em {path}: {body[:120]!r}")

    # ── Clientes ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_list(data) -> list[dict]:
        """A API varia o envelope: lista crua, {clientes: []}, {registros: []}, {data: []}..."""
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in ("clientes", "registros", "data", "titulos", "resultado", "lista"):
                if isinstance(data.get(key), list):
                    return [d for d in data[key] if isinstance(d, dict)]
            # dict de um registro só
            if any(k in data for k in ("nome", "login", "cpf_cnpj", "cpf")):
                return [data]
        return []

    # Rotas variam por versão do MK-AUTH — a que funcionar fica memorizada
    _CLIENT_ROUTES = [
        "/api/cliente/listar/pagina={pagina}?limite=500",
        "/api/cliente/listagem/pagina={pagina}",
        "/api/cliente/listagem",
    ]

    def list_clients(self, pagina: int = 1, limite: int = 500) -> list[dict]:
        routes = [self._client_route] if getattr(self, "_client_route", None) else self._CLIENT_ROUTES
        for route in routes:
            try:
                data = self._request("GET", route.format(pagina=pagina))
                itens = self._extract_list(data)
                if itens:
                    self._client_route = route
                    return itens
            except MkAuthError as e:
                logger.info(f"[mkauth] Rota {route} falhou ({e}); tentando próxima.")
        return []

    def refresh_clients_cache(self, force: bool = False) -> int:
        """Baixa todos os clientes (paginado) e monta índices por telefone e CPF."""
        if not force and self._clients and (time.time() - self._clients_ts) < config.sync_interval_s:
            return len(self._clients)

        all_clients: list[dict] = []
        vistos: set = set()
        pagina = 1
        while pagina <= 100:  # trava de segurança
            batch = self.list_clients(pagina=pagina)
            if not batch:
                break
            # Algumas versões ignoram a paginação e devolvem tudo — detectar repetição
            chave_batch = (batch[0].get("uuid") or batch[0].get("id"), len(batch))
            if chave_batch in vistos:
                break
            vistos.add(chave_batch)
            all_clients.extend(batch)
            if len(batch) < 500:
                break
            pagina += 1

        phone_index: dict[str, dict] = {}
        cpf_index: dict[str, dict] = {}
        for cli in all_clients:
            # Varre qualquer campo que pareça telefone (nomes variam por versão/tema)
            for field, value in cli.items():
                fl = field.lower()
                if any(k in fl for k in ("celular", "telefone", "fone", "whatsapp", "contato")):
                    norm = normalize_phone(str(value or ""))
                    if len(norm) >= 10:
                        phone_index.setdefault(norm, cli)
            cpf = normalize_cpf(str(cli.get("cpf_cnpj") or cli.get("cpf") or cli.get("cnpj") or ""))
            if cpf:
                cpf_index.setdefault(cpf, cli)

        self._clients = all_clients
        self._clients_ts = time.time()
        self._phone_index = phone_index
        self._cpf_index = cpf_index
        logger.info(f"[mkauth] Cache de clientes atualizado: {len(all_clients)} registros, "
                    f"{len(phone_index)} telefones indexados.")
        return len(all_clients)

    def find_client_by_phone(self, phone: str) -> dict | None:
        """Casa o número do WhatsApp com o cadastro do MK-AUTH (cache local)."""
        norm = normalize_phone(phone)
        if len(norm) < 10:
            return None
        try:
            self.refresh_clients_cache()
        except MkAuthError as e:
            logger.error(f"[mkauth] Falha ao atualizar cache de clientes: {e}")
        return self._phone_index.get(norm)

    def find_client_by_cpf(self, cpf: str) -> dict | None:
        norm = normalize_cpf(cpf)
        if len(norm) not in (11, 14):
            return None
        try:
            self.refresh_clients_cache()
        except MkAuthError as e:
            logger.error(f"[mkauth] Falha ao atualizar cache de clientes: {e}")
        return self._cpf_index.get(norm)

    def get_client(self, login_or_uuid: str) -> dict | None:
        data = self._request("GET", f"/api/cliente/show/{urllib.parse.quote(str(login_or_uuid))}")
        items = self._extract_list(data)
        return items[0] if items else None

    # ── Títulos / boletos ───────────────────────────────────────────────────

    def get_titulos_by_cpf(self, cpf: str, login: str = "") -> list[dict]:
        """Títulos do cliente. Tenta rotas por CPF; fallback: listagem completa filtrada."""
        norm = normalize_cpf(cpf)
        if not norm and not login:
            return []
        # 1) Rotas diretas por CPF (existem em algumas versões)
        for path in (f"/api/titulo/titulos/{norm}", f"/api/titulo/show/{norm}"):
            if not norm:
                break
            try:
                titulos = self._extract_list(self._request("GET", path))
                if titulos:
                    return titulos
            except MkAuthError:
                pass
        # 2) Fallback: listagem completa filtrada por CPF ou login
        try:
            data = self._request("GET", "/api/titulo/listagem")
            titulos = self._extract_list(data)
        except MkAuthError:
            try:
                data = self._request("GET", "/api/titulo/listar")
                titulos = self._extract_list(data)
            except MkAuthError as e:
                logger.error(f"[mkauth] Falha ao listar títulos: {e}")
                return []
        res = []
        for t in titulos:
            t_cpf = normalize_cpf(str(t.get("cpf_cnpj") or t.get("cpf") or ""))
            t_login = str(t.get("login") or "").strip().lower()
            if (norm and t_cpf == norm) or (login and t_login == login.strip().lower()):
                res.append(t)
        return res

    @staticmethod
    def filter_titulos_abertos(titulos: list[dict]) -> list[dict]:
        """Mantém apenas títulos não pagos (aberto/vencido), ordenados por vencimento."""
        abertos = []
        for t in titulos:
            status = _strip_accents(str(t.get("status", "")).strip().lower())
            pago = str(t.get("pago", "")).strip().lower()
            if status in ("pago", "cancelado", "baixado") or pago in ("sim", "s", "1", "true"):
                continue
            abertos.append(t)
        return sorted(abertos, key=lambda t: str(t.get("datavenc") or t.get("vencimento") or ""))


# Instância global usada pelo plugin (os testes podem substituí-la)
client = MkAuthClient()


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de intenção de cobrança
# ─────────────────────────────────────────────────────────────────────────────

_BILLING_PATTERNS = re.compile(
    r"\b("
    r"boleto|fatura|segunda\s+via|2\s*[ªa]?\s*via|mensalidade|cobranca|"
    r"pix|linha\s+digitavel|codigo\s+de\s+barras|"
    r"vencimento|venceu|vencido|vencida|atrasad[oa]|"
    r"pagar|pagamento|paguei|quita[rc]|debito|em\s+aberto|"
    r"quanto\s+(?:devo|ta|esta|custa)|valor\s+da?\s+(?:conta|fatura|internet)"
    r")\b",
    re.IGNORECASE,
)


def detect_billing_intent(text: str) -> bool:
    """True se a mensagem do cliente indica assunto de cobrança/boleto/fatura."""
    if not text:
        return False
    return bool(_BILLING_PATTERNS.search(_strip_accents(text)))


_CPF_IN_TEXT = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{11}|\d{14})\b")


def extract_cpf_from_text(text: str) -> str | None:
    """Extrai um CPF/CNPJ digitado pelo cliente na mensagem, se houver."""
    m = _CPF_IN_TEXT.search(text or "")
    return normalize_cpf(m.group(1)) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Formatação e bloco de contexto para o prompt
# ─────────────────────────────────────────────────────────────────────────────


def _fmt_money(value) -> str:
    try:
        v = float(str(value).replace(",", "."))
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(value)


def _fmt_date(value) -> str:
    s = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return s


def format_titulo(titulo: dict) -> str:
    """Resume um título em linhas prontas para o LLM repassar ao cliente."""
    valor = titulo.get("valor") or titulo.get("valor_titulo") or ""
    venc = titulo.get("datavenc") or titulo.get("vencimento") or titulo.get("data_venc") or ""
    status = str(titulo.get("status", "")).strip() or "aberto"
    linha = (titulo.get("linhadig") or titulo.get("linha_digitavel")
             or titulo.get("codigobarras") or titulo.get("codigo_barras") or "")
    pix = (titulo.get("pix") or titulo.get("pix_copia_cola") or titulo.get("qrcode")
           or titulo.get("emv") or "")
    link = titulo.get("link") or titulo.get("url") or ""

    lines = [f"- Valor: {_fmt_money(valor)} | Vencimento: {_fmt_date(venc)} | Status: {status}"]
    if linha:
        lines.append(f"  Linha digitável: {linha}")
    if pix:
        lines.append(f"  PIX copia e cola: {pix}")
    if link:
        lines.append(f"  Link: {link}")
    return "\n".join(lines)


def build_mkauth_context_block(phone_number: str, user_msg: str = "") -> str:
    """Monta o bloco '### DADOS DO CLIENTE (MK-AUTH) ###' para injetar no prompt.

    Retorna string vazia quando o MK-AUTH não está configurado ou não há dados.
    Nunca levanta exceção — falhas viram log + bloco de aviso neutro.
    """
    if not config.enabled:
        return ""

    try:
        cli = client.find_client_by_phone(phone_number)
        if not cli:
            cpf_msg = extract_cpf_from_text(user_msg)
            if cpf_msg:
                cli = client.find_client_by_cpf(cpf_msg)
        if not cli:
            return (
                "### DADOS DO CLIENTE (MK-AUTH) ###\n"
                "Este número de WhatsApp NÃO foi localizado no cadastro do provedor.\n"
                "Peça educadamente o CPF do titular para localizar o cadastro e a fatura. "
                "NUNCA invente valores, vencimentos ou códigos de pagamento.\n\n"
            )

        nome = cli.get("nome") or cli.get("name") or ""
        login = cli.get("login") or ""
        plano = cli.get("plano") or cli.get("velocidade") or ""
        cli_status = cli.get("cli_ativado") or cli.get("status") or ""
        cpf = normalize_cpf(str(cli.get("cpf_cnpj") or cli.get("cpf") or ""))

        lines = ["### DADOS DO CLIENTE (MK-AUTH) ###"]
        if nome:
            lines.append(f"Nome no cadastro: {nome}")
        if login:
            lines.append(f"Login: {login}")
        if plano:
            lines.append(f"Plano: {plano}")
        if cli_status:
            ativo = "ativo" if str(cli_status).lower() in ("s", "sim", "1", "true", "ativo") else str(cli_status)
            lines.append(f"Situação do cadastro: {ativo}")

        if cpf or login:
            titulos = client.filter_titulos_abertos(client.get_titulos_by_cpf(cpf, login=login))
            if titulos:
                lines.append(f"\nFaturas em aberto ({len(titulos)}):")
                for t in titulos[:5]:
                    lines.append(format_titulo(t))
                if len(titulos) > 5:
                    lines.append(f"(+{len(titulos) - 5} faturas mais antigas em aberto)")
            else:
                lines.append("\nNenhuma fatura em aberto — cliente em dia. Parabenize se fizer sentido.")

        lines.append(
            "\nREGRAS: use SOMENTE os dados acima — nunca invente valores, datas ou códigos. "
            "Ao enviar linha digitável ou PIX, envie o código completo em linha própria para facilitar a cópia. "
            "Estes dados pertencem ao titular deste número — não repasse dados de outros clientes."
        )
        return "\n".join(lines) + "\n\n"
    except Exception as e:  # nunca derrubar o fluxo de atendimento
        logger.error(f"[mkauth] Erro ao montar contexto: {e}")
        return ""
