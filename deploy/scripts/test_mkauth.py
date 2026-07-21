"""Diagnóstico da integração MK-AUTH (v3).

Uso:
    python test_mkauth.py                 # testa rotas e mostra formato dos títulos
    python test_mkauth.py CPF_DO_CLIENTE  # simula a consulta completa de boleto
"""

import sys

sys.path.insert(0, "/opt/data/.hermes/plugins/whatsapp-manager")

import mkauth_client as m

print("habilitado:", m.config.enabled)
token = m.client.get_token(force=True)
print("token OK")

n = m.client.refresh_clients_cache(force=True)
print("clientes no cache:", n)
print("telefones indexados:", len(m.client._phone_index))
print("cpfs indexados:", len(m.client._cpf_index))

data = m.client._request("GET", "/api/titulo/listagem")
titulos = m.client._extract_list(data)
print("titulos na listagem:", len(titulos))
if titulos:
    print("campos de um titulo:", sorted(titulos[0].keys()))

if len(sys.argv) > 1:
    cpf = m.normalize_cpf(sys.argv[1])
    print("---")
    print("buscando CPF:", cpf[:3] + "*****" + cpf[-2:])
    cli = m.client.find_client_by_cpf(cpf)
    if not cli:
        print("cliente NAO encontrado por este CPF.")
    else:
        print("cliente encontrado:", cli.get("nome") or cli.get("login"))
        login = str(cli.get("login") or "")
        ts = m.client.get_titulos_by_cpf(cpf, login=login)
        abertos = m.client.filter_titulos_abertos(ts)
        print("titulos do cliente:", len(ts), "| em aberto:", len(abertos))
        if abertos:
            print("primeiro em aberto:")
            print(m.format_titulo(abertos[0]))
    print("---")
    print("BLOCO QUE O BOT RECEBERIA:")
    print(m.build_mkauth_context_block("5599999999999", "meu cpf é " + cpf))
