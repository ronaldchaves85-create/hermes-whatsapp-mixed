#!/usr/bin/env python3
"""Testa a extração de campos de atualização de contato via LLM.

Uso:
    python3 test_nl_update.py
    python3 test_nl_update.py "coloque uma observação no Juan escrito ele prefere WhatsApp"
    python3 test_nl_update.py "coloque a Mayra como namorada"
"""

import json
import os
import sys
import base64
import urllib.request
from pathlib import Path

HERMES_HOME = os.getenv("HERMES_HOME", "/opt/data/.hermes")
AUTH_JSON = Path(HERMES_HOME) / "auth.json"

CASOS = [
    ("Juan", "coloque uma observação no Juan escrito ele prefere WhatsApp"),
    ("Juan", "coloque como cliente VIP"),
    ("Mayra", "coloque a Mayra como namorada"),
    ("Pedro", "coloque o Pedro como filho"),
    ("Mayra", "o apelido da Mayra é May"),
    ("Juan", "atualize o nome para Juan Carlos"),
    ("Pedro", "coloque uma nota: mora em São Paulo e trabalha com TI"),
]


def get_google_key():
    key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not key and AUTH_JSON.exists():
        try:
            auth = json.loads(AUTH_JSON.read_text())
            key = (auth.get("credential_pool", {}).get("gemini", "") or "").strip()
        except Exception:
            pass
    return key


def get_classify_model():
    return os.getenv("WHATSAPP_CONTACT_CLASSIFIER_MODEL", "gemini-2.0-flash-lite")


def extract_json_from_text(text: str) -> str:
    import re
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_update_fields(contact_name: str, message: str, google_key: str, model: str) -> dict:
    prompt = (
        f"O usuário pediu para atualizar o contato '{contact_name}' com a seguinte instrução:\n"
        f"\"{message}\"\n\n"
        "Extraia SOMENTE os campos explicitamente mencionados e retorne um JSON.\n"
        "Campos permitidos: relationship, manual_relationship, nickname, pet_name, notes, product, name.\n"
        "- notes = observação/anotação sobre o contato (texto livre). Use quando o usuário disser 'coloque uma observação', 'anote', 'registre que', etc.\n"
        "- nickname = apelido (ex: Bebel, Zé). Use quando o usuário disser 'o apelido é X'.\n"
        "- relationship enum: Amigo, AmigoProximo, Parente, Filho, Cliente, Vendedor\n"
        "- manual_relationship: valor livre (ex: Namorada, Filho, Esposa, Cliente VIP)\n"
        "  'como namorada' → relationship=AmigoProximo, manual_relationship=Namorada\n"
        "  'como filho' → relationship=Filho, manual_relationship=Filho\n"
        "  'como cliente' → relationship=Cliente, manual_relationship=Cliente\n"
        "NÃO invente campos. NÃO inclua tone, guidelines, summary, intent, frequency.\n"
        "Retorne APENAS JSON. Exemplos:\n"
        "  'coloque como namorada' → {\"relationship\": \"AmigoProximo\", \"manual_relationship\": \"Namorada\"}\n"
        "  'coloque uma observação: ele prefere WhatsApp' → {\"notes\": \"ele prefere WhatsApp\"}\n"
        "  'apelido é Zé, coloque como cliente' → {\"nickname\": \"Zé\", \"relationship\": \"Cliente\", \"manual_relationship\": \"Cliente\"}\n"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 256},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={google_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
        text_content = result["candidates"][0]["content"]["parts"][0]["text"].strip()

    raw = extract_json_from_text(text_content)
    return json.loads(raw)


def run_case(contact_name: str, message: str, google_key: str, model: str):
    print(f"\n  Contato : {contact_name}")
    print(f"  Comando : {message}")
    try:
        fields = extract_update_fields(contact_name, message, google_key, model)
        print(f"  Campos  : {json.dumps(fields, ensure_ascii=False)}")

        # Validações básicas
        warnings = []
        bad_fields = {"tone", "guidelines", "summary", "intent", "frequency", "classification"}
        found_bad = bad_fields & set(fields.keys())
        if found_bad:
            warnings.append(f"AVISO: campos não permitidos retornados: {found_bad}")
        if not fields:
            warnings.append("AVISO: nenhum campo extraído")

        for w in warnings:
            print(f"  *** {w}")

        return True, fields
    except Exception as e:
        print(f"  ERRO: {e}")
        return False, {}


def main():
    print("=" * 60)
    print("Teste de Extração de Campos NL — whatsapp-manager")
    print("=" * 60)

    google_key = get_google_key()
    if not google_key:
        print("[ERRO] GOOGLE_API_KEY não encontrada (env nem auth.json)")
        sys.exit(1)

    model = get_classify_model()
    print(f"\nModelo : {model}")
    print(f"API Key: {'✓ configurada' if google_key else '✗ ausente'}")

    # Modo ad-hoc: argumento da linha de comando
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        contact_name = "Contato"
        # Tenta extrair nome do contato da mensagem para deixar mais legível
        print(f"\n--- Teste ad-hoc ---")
        run_case(contact_name, message, google_key, model)
        return

    # Suite de casos padrão
    print(f"\n--- {len(CASOS)} casos de teste ---")
    ok = 0
    for contact_name, message in CASOS:
        success, _ = run_case(contact_name, message, google_key, model)
        if success:
            ok += 1

    print(f"\n{'=' * 60}")
    print(f"Resultado: {ok}/{len(CASOS)} casos bem-sucedidos")


if __name__ == "__main__":
    main()
