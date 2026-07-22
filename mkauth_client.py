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
    def data_dir(self) -> str:
        return os.environ.get("MKAUTH_DATA_DIR", "/opt/data")

    @property
    def enrich_enabled(self) -> bool:
        """Enriquecimento de telefones via /api/cliente/show em background."""
        return os.environ.get("MKAUTH_ENRICH", "true").strip().lower() in ("1", "true", "yes")

    @property
    def pix_key(self) -> str:
        return os.environ.get("MKAUTH_PIX_KEY", "").strip()

    @property
    def pix_name(self) -> str:
        return os.environ.get("MKAUTH_PIX_NAME", "SPEEDNET ACARA").strip()[:25]

    @property
    def pix_city(self) -> str:
        return os.environ.get("MKAUTH_PIX_CITY", "ACARA").strip()[:15]

    @property
    def titulos_ttl_s(self) -> int:
        try:
            return int(float(os.environ.get("MKAUTH_TITULOS_TTL_MIN", "10")) * 60)
        except ValueError:
            return 600

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
        self._login_index: dict[str, dict] = {}
        # Cache de títulos (evita baixar a listagem completa a cada consulta)
        self._titulos: list[dict] = []
        self._titulos_ts: float = 0.0
        # Enriquecimento de telefones (via /api/cliente/show) e vínculos memorizados
        self._enrich_running = threading.Event()
        self._persist_loaded = False
        self._phone_to_login: dict[str, str] = {}
        self._enriched_logins: set = set()
        self._bindings: dict[str, str] = {}

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

    # ── Persistência local (índice de telefones e vínculos) ────────────────

    def _persist_path(self, name: str) -> str:
        return os.path.join(config.data_dir, name)

    def _load_persisted(self) -> None:
        if self._persist_loaded:
            return
        self._persist_loaded = True
        try:
            with open(self._persist_path("mkauth_phone_index.json"), encoding="utf-8") as f:
                j = json.load(f)
            self._phone_to_login = dict(j.get("phone_to_login", {}))
            self._enriched_logins = set(j.get("enriched_logins", []))
            logger.info(f"[mkauth] Índice de telefones carregado: {len(self._phone_to_login)} números.")
        except (OSError, ValueError):
            pass
        try:
            with open(self._persist_path("mkauth_bindings.json"), encoding="utf-8") as f:
                self._bindings = dict(json.load(f))
        except (OSError, ValueError):
            pass

    def _save_phone_index(self) -> None:
        try:
            with open(self._persist_path("mkauth_phone_index.json"), "w", encoding="utf-8") as f:
                json.dump({"phone_to_login": self._phone_to_login,
                           "enriched_logins": sorted(self._enriched_logins)}, f, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"[mkauth] Falha ao salvar índice de telefones: {e}")

    def save_binding(self, phone: str, login: str) -> None:
        """Memoriza o vínculo número de WhatsApp → login do cliente (após 1ª identificação)."""
        try:
            norm = normalize_phone(phone)
            if not norm or not login:
                return
            self._load_persisted()
            if self._bindings.get(norm) == login:
                return
            self._bindings[norm] = str(login)
            with open(self._persist_path("mkauth_bindings.json"), "w", encoding="utf-8") as f:
                json.dump(self._bindings, f, ensure_ascii=False)
            logger.info(f"[mkauth] Vínculo memorizado: {norm[:4]}**** → {login}")
        except OSError as e:
            logger.warning(f"[mkauth] Falha ao salvar vínculo: {e}")

    def _request(self, method: str, path: str, payload: dict | None = None, _retry: bool = True, timeout: int = 20):
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
            with self._urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                logger.info("[mkauth] 401 — renovando token e repetindo a chamada.")
                self.get_token(force=True)
                return self._request(method, path, payload, _retry=False, timeout=timeout)
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
            for key in ("clientes", "registros", "data", "dados", "titulos", "resultado", "lista"):
                if isinstance(data.get(key), list):
                    return [d for d in data[key] if isinstance(d, dict)]
                if isinstance(data.get(key), dict):
                    return [data[key]]  # registro único embrulhado (ex.: {'dados': {...}})
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

        login_index: dict[str, dict] = {}
        for cli in all_clients:
            lg = str(cli.get("login") or "").strip().lower()
            if lg:
                login_index.setdefault(lg, cli)

        self._clients = all_clients
        self._clients_ts = time.time()
        self._phone_index = phone_index
        self._cpf_index = cpf_index
        self._login_index = login_index
        logger.info(f"[mkauth] Cache de clientes atualizado: {len(all_clients)} registros, "
                    f"{len(phone_index)} telefones indexados.")
        return len(all_clients)

    def find_client_by_phone(self, phone: str) -> dict | None:
        """Casa o número do WhatsApp com o cadastro do MK-AUTH.

        Ordem: campos de telefone da listagem → vínculo memorizado (CPF informado
        antes) → índice enriquecido via /api/cliente/show (background).
        """
        norm = normalize_phone(phone)
        if len(norm) < 10:
            return None
        try:
            self.refresh_clients_cache()
        except MkAuthError as e:
            logger.error(f"[mkauth] Falha ao atualizar cache de clientes: {e}")
        self._load_persisted()

        cli = self._phone_index.get(norm)
        if cli:
            return cli

        login = self._bindings.get(norm) or self._phone_to_login.get(norm)
        if login:
            cli = self._login_index.get(str(login).strip().lower())
            if cli:
                return cli

        # Não achou — dispara o enriquecimento em background p/ próximas consultas
        if config.enrich_enabled:
            self.start_phone_enrichment()
        return None

    def get_client_detail(self, login: str) -> dict | None:
        """Cadastro completo de um cliente via /api/cliente/show (inclui telefones)."""
        data = self._request("GET", f"/api/cliente/show/{urllib.parse.quote(str(login))}")
        itens = self._extract_list(data)
        alvo = itens[0] if itens else (data if isinstance(data, dict) else None)
        while isinstance(alvo, dict) and len(alvo) == 1 and isinstance(next(iter(alvo.values())), dict):
            alvo = next(iter(alvo.values()))
        return alvo if isinstance(alvo, dict) else None

    def start_phone_enrichment(self) -> None:
        """Inicia (se ainda não estiver rodando) a varredura de telefones em background."""
        if self._enrich_running.is_set():
            return
        self._enrich_running.set()
        threading.Thread(target=self._enrich_worker, daemon=True).start()

    def _enrich_worker(self) -> None:
        try:
            self._load_persisted()
            done = {str(l).lower() for l in self._enriched_logins}
            total = len(self._clients)
            novos = 0
            logger.info(f"[mkauth] Enriquecimento de telefones iniciado ({len(done)}/{total} já feitos).")
            for cli in list(self._clients):
                login = str(cli.get("login") or "").strip()
                if not login or login.lower() in done:
                    continue
                try:
                    det = self.get_client_detail(login) or {}
                except MkAuthError:
                    time.sleep(2)
                    continue
                for k, v in det.items():
                    kl = k.lower()
                    if any(t in kl for t in ("fone", "celular", "whats", "contato")) and "op" not in kl:
                        n = normalize_phone(str(v or ""))
                        if len(n) >= 10:
                            self._phone_to_login[n] = login
                self._enriched_logins.add(login)
                done.add(login.lower())
                novos += 1
                if novos % 200 == 0:
                    self._save_phone_index()
                    logger.info(f"[mkauth] Enriquecimento: {len(done)}/{total} clientes, "
                                f"{len(self._phone_to_login)} telefones mapeados.")
                time.sleep(0.05)  # gentileza com o servidor MK-AUTH
            self._save_phone_index()
            logger.info(f"[mkauth] Enriquecimento concluído: {len(self._phone_to_login)} telefones mapeados.")
        except Exception as e:
            logger.error(f"[mkauth] Enriquecimento interrompido: {e}")
        finally:
            self._enrich_running.clear()

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
        # 2) Fallback: listagem completa (em cache com TTL) filtrada por CPF ou login
        titulos = self._get_all_titulos()
        if not titulos:
            return []
        res = []
        for t in titulos:
            t_cpf = normalize_cpf(str(t.get("cpf_cnpj") or t.get("cpf") or ""))
            t_login = str(t.get("login") or "").strip().lower()
            if (norm and t_cpf == norm) or (login and t_login == login.strip().lower()):
                res.append(t)
        return res

    def _get_all_titulos(self) -> list[dict]:
        """Listagem completa de títulos com cache (TTL configurável, padrão 10 min)."""
        if self._titulos and (time.time() - self._titulos_ts) < config.titulos_ttl_s:
            return self._titulos
        for path in ("/api/titulo/listagem", "/api/titulo/listar"):
            try:
                titulos = self._extract_list(self._request("GET", path, timeout=60))
                if titulos:
                    self._titulos = titulos
                    self._titulos_ts = time.time()
                    logger.info(f"[mkauth] Cache de títulos atualizado: {len(titulos)} registros.")
                    return titulos
            except MkAuthError:
                continue
        logger.error("[mkauth] Falha ao listar títulos em todas as rotas.")
        return self._titulos

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


# ─────────────────────────────────────────────────────────────────────────────
# PIX (BR Code EMV — padrão Banco Central)
# ─────────────────────────────────────────────────────────────────────────────


def _crc16_ccitt(payload: str) -> str:
    crc = 0xFFFF
    for ch in payload.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def _emv(field_id: str, value: str) -> str:
    return f"{field_id}{len(value):02d}{value}"


def build_pix_payload(amount: float, txid: str = "***") -> str | None:
    """Monta o 'PIX copia e cola' estático com valor, usando a chave da empresa."""
    if not config.pix_key:
        return None
    txid = re.sub(r"[^A-Za-z0-9]", "", txid)[:25] or "***"
    mai = _emv("00", "br.gov.bcb.pix") + _emv("01", config.pix_key)
    payload = (
        _emv("00", "01")
        + _emv("26", mai)
        + _emv("52", "0000")
        + _emv("53", "986")
        + _emv("54", f"{amount:.2f}")
        + _emv("58", "BR")
        + _emv("59", _strip_accents(config.pix_name))
        + _emv("60", _strip_accents(config.pix_city))
        + _emv("62", _emv("05", txid))
        + "6304"
    )
    return payload + _crc16_ccitt(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Geração do PDF da fatura
# ─────────────────────────────────────────────────────────────────────────────


def generate_boleto_pdf(cli: dict, titulo: dict, out_dir: str | None = None) -> str | None:
    """Gera um PDF de fatura (Speednet) com os dados do título. None se indisponível."""
    try:
        from fpdf import FPDF
    except Exception:
        logger.warning("[mkauth] fpdf2 não instalado — envio de PDF desabilitado (fallback: linha digitável no texto).")
        return None
    try:
        out_dir = out_dir or os.path.join(config.data_dir, "boletos_pdf")
        os.makedirs(out_dir, exist_ok=True)

        nome = str(cli.get("nome") or cli.get("login") or "Cliente")
        login = str(cli.get("login") or "")
        valor = _fmt_money(titulo.get("valor") or titulo.get("valor_titulo") or "")
        venc_raw = str(titulo.get("datavenc") or titulo.get("vencimento") or "")
        venc = _fmt_date(venc_raw)
        status = str(titulo.get("status", "")).strip() or "aberto"
        linha = (titulo.get("linhadig") or titulo.get("linha_digitavel")
                 or titulo.get("codigobarras") or titulo.get("codigo_barras") or "")
        nossonum = str(titulo.get("nossonum") or titulo.get("titulo") or "")

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Cabeçalho verde
        pdf.set_fill_color(19, 128, 66)
        pdf.rect(0, 0, 210, 30, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("helvetica", "B", 20)
        pdf.set_xy(12, 7)
        pdf.cell(0, 10, "SPEEDNET ACARA")
        pdf.set_font("helvetica", "", 10)
        pdf.set_xy(12, 18)
        pdf.cell(0, 6, "Provedor de Internet - Fatura de Servicos")

        pdf.set_text_color(30, 30, 30)
        pdf.set_y(40)
        pdf.set_font("helvetica", "B", 13)
        pdf.cell(0, 8, f"Cliente: {nome}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 11)
        if login:
            pdf.cell(0, 7, f"Login: {login}", new_x="LMARGIN", new_y="NEXT")
        if nossonum:
            pdf.cell(0, 7, f"Documento: {nossonum}", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(4)
        pdf.set_font("helvetica", "B", 14)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(63, 12, f"Valor: {valor}", border=1, fill=True)
        pdf.cell(63, 12, f"Vencimento: {venc}", border=1, fill=True)
        pdf.cell(63, 12, f"Situacao: {status}", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

        if linha:
            pdf.ln(6)
            pdf.set_font("helvetica", "B", 12)
            pdf.cell(0, 8, "Linha digitavel para pagamento:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("courier", "B", 13)
            pdf.set_fill_color(248, 248, 248)
            pdf.multi_cell(0, 9, linha, border=1, fill=True)
            pdf.set_font("helvetica", "", 9)
            pdf.cell(0, 6, "Pague em qualquer banco, lotérica ou pelo app do seu banco (opcao boleto).",
                     new_x="LMARGIN", new_y="NEXT")

        # PIX com QR Code (quando a chave da empresa está configurada)
        try:
            _valor_f = float(str(titulo.get("valor") or "0").replace(",", "."))
        except (ValueError, TypeError):
            _valor_f = 0.0
        pix_payload = build_pix_payload(_valor_f, txid=nossonum) if _valor_f > 0 else None
        if pix_payload:
            try:
                import qrcode
                qr_img = qrcode.make(pix_payload)
                qr_tmp = os.path.join(out_dir, "_qr_tmp.png")
                qr_img.save(qr_tmp)
                pdf.ln(6)
                y0 = pdf.get_y()
                pdf.image(qr_tmp, x=12, y=y0, w=48, h=48)
                pdf.set_xy(66, y0)
                pdf.set_font("helvetica", "B", 12)
                pdf.cell(0, 8, "Pague com PIX (QR Code ao lado)", new_x="LMARGIN", new_y="NEXT")
                pdf.set_xy(66, y0 + 9)
                pdf.set_font("helvetica", "", 9)
                pdf.multi_cell(130, 5,
                               "Abra o app do seu banco, escolha PIX > Ler QR Code e aponte "
                               "a camera. O valor ja vem preenchido. Ou use o copia e cola abaixo.")
                pdf.set_y(y0 + 50)
                pdf.set_font("helvetica", "B", 10)
                pdf.cell(0, 6, "PIX copia e cola:", new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("courier", "", 8)
                pdf.set_fill_color(248, 248, 248)
                pdf.multi_cell(0, 4.5, pix_payload, border=1, fill=True)
                try:
                    os.remove(qr_tmp)
                except OSError:
                    pass
            except Exception as qr_err:
                logger.warning(f"[mkauth] QR PIX indisponivel no PDF: {qr_err}")

        pdf.ln(6)
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(0, 5, "Atendimento: (91) 98599-4245 - seg a sab, 08:00-12:00 / 14:00-18:00",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 5, "Av. Comandante Pedro Vinagre, ao lado do Hotel e Supermercado Fonseca - Acara/PA",
                 new_x="LMARGIN", new_y="NEXT")

        safe_nome = re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_")[:30] or "cliente"
        safe_venc = re.sub(r"[^0-9-]", "", venc_raw)[:10] or "fatura"
        path = os.path.join(out_dir, f"Fatura_Speednet_{safe_nome}_{safe_venc}.pdf")
        pdf.output(path)
        logger.info(f"[mkauth] PDF gerado: {path}")
        return path
    except Exception as e:
        logger.error(f"[mkauth] Falha ao gerar PDF: {e}")
        return None


def _render_boleto_via_browser(url: str, out_path: str) -> bool:
    """Renderiza a página HTML do boleto em PDF usando o Chromium embutido (Playwright)."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_boleto.cjs")
    if not os.path.exists(script):
        logger.warning("[mkauth] render_boleto.cjs não encontrado ao lado do plugin.")
        return False
    import subprocess
    env = dict(os.environ)
    env.setdefault("NODE_PATH", "/opt/hermes/node_modules")
    try:
        r = subprocess.run(["node", script, out_path, url], env=env,
                           capture_output=True, timeout=90)
        ok = (r.returncode == 0 and os.path.exists(out_path)
              and os.path.getsize(out_path) > 5000)
        if not ok:
            logger.warning(f"[mkauth] Render do boleto falhou: {r.stderr.decode(errors='replace')[:150]}")
        return ok
    except Exception as e:
        logger.warning(f"[mkauth] Render do boleto: {e}")
        return False


def fetch_official_boleto_pdf(titulo: dict, out_dir: str | None = None) -> str | None:
    """Baixa o boleto oficial (Sicoob, com QR PIX) do próprio MK-AUTH.

    Usa a rota pública /boleto/boleto.hhvm?titulo=N. Retorna o caminho do PDF
    ou None se a resposta não for um PDF (aí usamos o PDF gerado como fallback).
    """
    tid = str(titulo.get("titulo") or "").strip()
    if not tid or not config.url:
        return None
    out_dir = out_dir or os.path.join(config.data_dir, "boletos_pdf")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        return None
    base = config.url
    urls = [f"{base}/boleto/boleto.hhvm?titulo={tid}"]
    if base.startswith("https://"):
        urls.append(f"http://{base[8:]}/boleto/boleto.hhvm?titulo={tid}")
    path = os.path.join(out_dir, f"Boleto_Speednet_{tid}.pdf")
    # Reaproveita render recente (evita abrir o navegador de novo à toa)
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 3600                 and os.path.getsize(path) > 5000:
            return path
    except OSError:
        pass
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with client._urlopen(req, timeout=30) as resp:
                data = resp.read()
            if data[:5] == b"%PDF-":
                with open(path, "wb") as f:
                    f.write(data)
                logger.info(f"[mkauth] Boleto oficial baixado ({len(data)} bytes): {path}")
                return path
            # Página HTML → renderizar com o navegador embutido (fica idêntico ao impresso)
            logger.info(f"[mkauth] {u} é HTML — renderizando boleto oficial via navegador...")
            if _render_boleto_via_browser(u, path):
                logger.info(f"[mkauth] Boleto oficial renderizado ({os.path.getsize(path)} bytes): {path}")
                return path
        except Exception as e:
            logger.warning(f"[mkauth] Falha ao obter boleto oficial em {u}: {e}")
    return None


# Cache do bundle por (telefone+mensagem) — pre_llm_call roda várias vezes por turno
_bundle_cache: dict = {}


def build_billing_bundle(phone_number: str, user_msg: str = "") -> dict:
    """Monta o contexto de cobrança + PDF da fatura (quando possível).

    Retorna {"block": str, "block_pdf": str, "pdf_path": str|None, "pdf_name": str|None}.
    - block: contexto com linha digitável (fallback quando o PDF não vai)
    - block_pdf: contexto para quando o PDF FOI enviado (sem códigos no texto)
    """
    res = {"block": "", "block_pdf": "", "pdf_path": None, "pdf_name": None}
    if not config.enabled:
        return res

    cache_key = normalize_phone(phone_number) + ":" + (user_msg or "")[:60]
    cached = _bundle_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < 600:
        return cached[1]

    try:
        cli = client.find_client_by_phone(phone_number)
        if not cli:
            cpf_msg = extract_cpf_from_text(user_msg)
            if cpf_msg:
                cli = client.find_client_by_cpf(cpf_msg)
                if cli and cli.get("login"):
                    # Memoriza o vínculo: próximas conversas nem pedem CPF
                    client.save_binding(phone_number, str(cli["login"]))
        if not cli:
            res["block"] = (
                "### DADOS DO CLIENTE (MK-AUTH) ###\n"
                "Este número de WhatsApp NÃO foi localizado no cadastro do provedor.\n"
                "Peça educadamente o CPF do titular para localizar o cadastro e a fatura. "
                "NUNCA invente valores, vencimentos ou códigos de pagamento.\n\n"
            )
            _bundle_cache[cache_key] = (time.time(), res)
            return res

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

        titulos = []
        if cpf or login:
            titulos = client.filter_titulos_abertos(client.get_titulos_by_cpf(cpf, login=login))

        lines_pdf = list(lines)  # variante para quando o PDF é enviado

        if titulos:
            lines.append(f"\nFaturas em aberto ({len(titulos)}):")
            for t in titulos[:5]:
                lines.append(format_titulo(t))
            if len(titulos) > 5:
                lines.append(f"(+{len(titulos) - 5} faturas mais antigas em aberto)")

            t0 = titulos[0]
            venc0 = _fmt_date(t0.get("datavenc") or t0.get("vencimento") or "")
            valor0 = _fmt_money(t0.get("valor") or "")
            lines_pdf.append(f"\nFaturas em aberto: {len(titulos)}. "
                             f"A mais próxima: {valor0}, vencimento {venc0}.")

            # 1º: boleto oficial do banco (com QR PIX de baixa automática);
            # 2º: PDF gerado localmente como fallback
            pdf_path = fetch_official_boleto_pdf(t0) or generate_boleto_pdf(cli, t0)
            if pdf_path:
                res["pdf_path"] = pdf_path
                res["pdf_name"] = os.path.basename(pdf_path)
        else:
            aviso = "\nNenhuma fatura em aberto — cliente em dia. Parabenize se fizer sentido."
            lines.append(aviso)
            lines_pdf.append(aviso)

        lines.append(
            "\nREGRAS: use SOMENTE os dados acima — nunca invente valores, datas ou códigos. "
            "Ao enviar linha digitável ou PIX, envie o código completo em linha própria para facilitar a cópia. "
            "Sempre que o assunto for pagamento/boleto, apresente também o aplicativo da Speednet "
            "(links na base de conhecimento). "
            "Estes dados pertencem ao titular deste número — não repasse dados de outros clientes."
        )
        lines_pdf.append(
            "\nIMPORTANTE: o BOLETO em PDF JÁ FOI ENVIADO como anexo nesta conversa "
            "(com QR Code PIX para pagamento). "
            "NÃO envie linha digitável, código de barras nem PIX no texto — está tudo no PDF. "
            "Apenas avise que a fatura está em anexo, com *Valor:* e *Vencimento:* em linhas "
            "próprias (negrito com um asterisco), e apresente o aplicativo da Speednet com os "
            "links em linhas próprias (modelo na base de conhecimento). "
            "Não repasse dados de outros clientes."
        )

        res["block"] = "\n".join(lines) + "\n\n"
        res["block_pdf"] = "\n".join(lines_pdf) + "\n\n"
        _bundle_cache[cache_key] = (time.time(), res)
        return res
    except Exception as e:  # nunca derrubar o fluxo de atendimento
        logger.error(f"[mkauth] Erro ao montar contexto: {e}")
        return res


def build_mkauth_context_block(phone_number: str, user_msg: str = "") -> str:
    """Compatibilidade: retorna apenas o bloco de texto (sem PDF)."""
    return build_billing_bundle(phone_number, user_msg)["block"]
