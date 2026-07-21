"""Diagnóstico da integração MK-AUTH — sonda os endpoints da API.

Uso:
    python test_mkauth.py                # descobre quais rotas funcionam
    python test_mkauth.py 5591999998888  # (após rotas ok) busca número e boletos
"""

import sys

sys.path.insert(0, "/opt/data/.hermes/plugins/whatsapp-manager")

try:
    import mkauth_client as m
except Exception as e:
    print("ERRO ao importar mkauth_client:", type(e).__name__, e)
    sys.exit(1)

print("habilitado:", m.config.enabled)
print("url:", m.config.url)

if not m.config.enabled:
    print("-> Variáveis MKAUTH_* ausentes no ambiente.")
    sys.exit(1)

try:
    token = m.client.get_token(force=True)
    print("token OK:", token[:25] + "...")
except Exception as e:
    print("ERRO ao gerar token:", type(e).__name__, e)
    sys.exit(1)

print()
print("=== SONDANDO ROTAS DE CLIENTES ===")
candidatos = [
    "/api/cliente/listar/pagina=1?limite=500",
    "/api/cliente/listar/pagina=1",
    "/api/cliente/listar/1",
    "/api/cliente/listar",
    "/api/cliente/listagem/pagina=1",
    "/api/cliente/listagem",
]
rota_ok = None
for path in candidatos:
    try:
        data = m.client._request("GET", path)
        itens = m.client._extract_list(data)
        resumo = str(data)[:100].replace("\n", " ")
        print(f"[OK {len(itens):4d} itens] {path} -> {resumo}")
        if itens and rota_ok is None:
            rota_ok = path
    except Exception as e:
        print(f"[FALHOU] {path} -> {type(e).__name__}: {str(e)[:90]}")

print()
print("=== SONDANDO ROTAS DE TÍTULOS ===")
cand_tit = [
    "/api/titulo/listar/pagina=1",
    "/api/titulo/listar",
    "/api/titulo/listagem",
]
for path in cand_tit:
    try:
        data = m.client._request("GET", path)
        itens = m.client._extract_list(data)
        resumo = str(data)[:100].replace("\n", " ")
        print(f"[OK {len(itens):4d} itens] {path} -> {resumo}")
    except Exception as e:
        print(f"[FALHOU] {path} -> {type(e).__name__}: {str(e)[:90]}")

if rota_ok:
    print()
    print(">>> Melhor rota de clientes:", rota_ok)
    if len(sys.argv) > 1:
        alvo = sys.argv[1]
        print("buscando numero:", alvo)
        # busca manual usando a rota que funcionou
        data = m.client._request("GET", rota_ok)
        itens = m.client._extract_list(data)
        norm = m.normalize_phone(alvo)
        achado = None
        for cli in itens:
            for campo in ("celular", "celular2", "telefone", "fone", "whatsapp"):
                if m.normalize_phone(str(cli.get(campo, ""))) == norm:
                    achado = cli
                    break
            if achado:
                break
        if achado:
            print("cliente encontrado:", achado.get("nome") or achado.get("login"))
            print("campos do cadastro:", sorted(achado.keys()))
        else:
            print("numero NAO encontrado nesta pagina de clientes.")
            if itens:
                print("exemplo de campos de um cliente:", sorted(itens[0].keys()))
