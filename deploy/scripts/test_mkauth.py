"""Diagnóstico da integração MK-AUTH — roda dentro do container hermes.

Uso:
    python test_mkauth.py                # testa conexão e cache de clientes
    python test_mkauth.py 5591999998888  # também busca este número e seus boletos
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
    print("-> Variáveis MKAUTH_URL / MKAUTH_CLIENT_ID / MKAUTH_CLIENT_SECRET ausentes no ambiente.")
    sys.exit(1)

try:
    token = m.client.get_token(force=True)
    print("token OK:", token[:25] + "...")
except Exception as e:
    print("ERRO ao gerar token:", type(e).__name__, e)
    sys.exit(1)

try:
    n = m.client.refresh_clients_cache(force=True)
    print("clientes no cache:", n)
    print("telefones indexados:", len(m.client._phone_index))
    exemplos = list(m.client._phone_index)[:3]
    for tel in exemplos:
        print("  exemplo de telefone indexado:", tel[:4] + "****" + tel[-2:])
except Exception as e:
    print("ERRO ao listar clientes:", type(e).__name__, e)
    sys.exit(1)

if len(sys.argv) > 1:
    alvo = sys.argv[1]
    print("---")
    print("buscando numero:", alvo, "(normalizado:", m.normalize_phone(alvo) + ")")
    cli = m.client.find_client_by_phone(alvo)
    if not cli:
        print("cliente NAO encontrado pelo telefone.")
        print("-> confira se este numero esta no campo celular/telefone do cadastro no MK-AUTH")
    else:
        print("cliente encontrado:", cli.get("nome") or cli.get("login"))
        cpf = m.normalize_cpf(str(cli.get("cpf_cnpj") or cli.get("cpf") or ""))
        print("cpf no cadastro:", (cpf[:3] + "*****" + cpf[-2:]) if cpf else "(vazio)")
        if cpf:
            titulos = m.client.get_titulos_by_cpf(cpf)
            abertos = m.client.filter_titulos_abertos(titulos)
            print("titulos retornados:", len(titulos), "| em aberto:", len(abertos))
            if abertos:
                print("exemplo de titulo em aberto:")
                print(m.format_titulo(abertos[0]))
    print("---")
    print("bloco que seria injetado no prompt:")
    print(m.build_mkauth_context_block(alvo, "quero meu boleto"))
