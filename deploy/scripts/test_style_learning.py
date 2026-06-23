#!/usr/bin/env python3
"""
Testa o style learning localmente sem enviar mensagem ao bot.

Uso:
  python3 test_style_learning.py              # mostra diálogos coletados
  python3 test_style_learning.py --preview    # mostra como ficaria o SOUL_WHATSAPP.md
  python3 test_style_learning.py --db         # mostra mensagens brutas do banco
"""

import sys
import sqlite3
import json
from pathlib import Path

DB_PATH = Path("/opt/data/.hermes/whatsapp_messages.db")
CONTACTS_PATH = Path("/opt/data/personal_contacts.json")
SOUL_PATH = Path("/opt/data/.hermes/SOUL_WHATSAPP.md")

PREVIEW = "--preview" in sys.argv
SHOW_DB = "--db" in sys.argv


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("55") and len(digits) == 13:
        # Remove 9 duplicado: 5586 9 XXXX → 5586 XXXX (formato antigo)
        pass
    return digits


def normalize_text(s: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def sanitize(text: str) -> str | None:
    import re
    if not text:
        return None
    patterns = [
        r"\b\d{4,6}\b.*senha|senha.*\b\d{4,6}\b",
        r"senha|password|pin\b",
        r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
        r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",
        r"ag[eê]ncia\s*:?\s*\d{3,6}",
        r"conta\s*:?\s*\d{4,}",
        r"cart[aã]o\s*:?\s*[\d\s]{13,19}",
        r"\b\d{13,19}\b",
        r"cvv|cvc\s*:?\s*\d{3}",
        r"saldo.*R\$\s*[\d.,]+",
        r"R\$\s*[\d.,]{4,}",
        r"chave\s+pix.*@|@.*chave\s+pix",
        r"token|código de verificação|código de acesso",
    ]
    tl = text.lower()
    for p in patterns:
        if re.search(p, tl, re.IGNORECASE):
            return None
    return text


def main():
    if not DB_PATH.exists():
        print(f"❌ Banco não encontrado: {DB_PATH}")
        sys.exit(1)

    personal_contacts = {}
    if CONTACTS_PATH.exists():
        with open(CONTACTS_PATH) as f:
            personal_contacts = json.load(f)

    # Construir lookups
    phone_to_rel = {}
    phone_to_name = {}
    raw_to_rel = {}
    raw_to_name = {}

    for key, data in personal_contacts.items():
        rel = data.get("manual_relationship") or data.get("relationship") or "Cliente"
        name = data.get("nickname") or data.get("name") or ""
        if normalize_text(name) in ("andre alencar", "andre", "andré alencar", "andré"):
            name = ""
        raw_prefix = key.split("@")[0]
        phone_norm = normalize_phone(raw_prefix)
        phone_to_rel[phone_norm] = rel
        raw_to_rel[raw_prefix] = rel
        if name:
            phone_to_name[phone_norm] = name
            raw_to_name[raw_prefix] = name

    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()

        if SHOW_DB:
            print("=== MENSAGENS BRUTAS (from_me=1, últimas 20) ===\n")
            cur.execute("""
                SELECT chat_id, sender_name, body, timestamp, from_me
                FROM messages
                WHERE from_me=1 AND (sender_name IS NULL OR sender_name != 'Bot')
                AND chat_id NOT LIKE '%@g.us%'
                ORDER BY timestamp DESC LIMIT 20
            """)
            for row in cur.fetchall():
                print(f"chat: {row[0]}")
                print(f"  sender: {row[1]} | body: {row[2][:80]}")
                print()
            return

        # Owner phone
        cur.execute("SELECT chat_id FROM messages WHERE from_me=1 LIMIT 1")
        owner_phone = ""

        cur.execute("""
            SELECT chat_id, MAX(timestamp) FROM messages
            WHERE from_me=1 AND (sender_name IS NULL OR sender_name != 'Bot')
            AND chat_id NOT LIKE '%@g.us%'
            GROUP BY chat_id ORDER BY 2 DESC
        """)
        chat_rows = cur.fetchall()

        print(f"=== STYLE LEARNING — {len(chat_rows)} chat(s) encontrado(s) ===\n")

        import time
        cutoff = int(time.time()) - 90 * 24 * 3600
        total = 0

        for chat_id, _ in chat_rows:
            phone = chat_id.split("@")[0].split(":")[0]
            phone_norm = normalize_phone("".join(c for c in phone if c.isdigit()))
            rel = raw_to_rel.get(phone, phone_to_rel.get(phone_norm, "Geral"))
            contact_name = raw_to_name.get(phone, phone_to_name.get(phone_norm, rel))

            cur.execute("""
                SELECT m.body, m.timestamp,
                       (SELECT body FROM messages
                        WHERE chat_id=? AND from_me=0 AND timestamp < m.timestamp
                        AND body IS NOT NULL AND length(trim(body)) > 1
                        AND body NOT LIKE '<Media omitted>%'
                        AND length(body) <= 300
                        ORDER BY timestamp DESC LIMIT 1) as contact_msg
                FROM messages m
                WHERE m.from_me=1 AND (m.sender_name IS NULL OR m.sender_name != 'Bot')
                AND m.chat_id=? AND m.timestamp >= ?
                AND m.body IS NOT NULL AND length(trim(m.body)) > 1
                AND m.body NOT LIKE '[%'
                ORDER BY m.timestamp DESC LIMIT 5
            """, (chat_id, chat_id, cutoff))

            rows = cur.fetchall()
            if not rows:
                continue

            print(f"📱 {chat_id}")
            print(f"   Relacionamento: {rel} | Label: {contact_name}")
            for body, ts, contact_msg in rows:
                body_clean = sanitize(body)
                if not body_clean:
                    continue
                if contact_msg:
                    print(f'   {contact_name}: "{contact_msg[:60]}"')
                print(f'   André → {contact_name}: "{body_clean[:80]}"')
                print()
            total += len(rows)

        print(f"Total: {total} mensagem(ns) coletada(s)\n")

        if PREVIEW:
            print("=== PREVIEW SOUL_WHATSAPP.md ===\n")
            from datetime import datetime
            print(f"## EXEMPLOS REAIS DE ESCRITA")
            print(f"> Gerado em {datetime.now().strftime('%d/%m/%Y')}\n")
            # Re-run grouped by rel
            groups = {}
            for chat_id, _ in chat_rows:
                phone = chat_id.split("@")[0].split(":")[0]
                phone_norm = normalize_phone("".join(c for c in phone if c.isdigit()))
                rel = raw_to_rel.get(phone, phone_to_rel.get(phone_norm, "Geral"))
                contact_name = raw_to_name.get(phone, phone_to_name.get(phone_norm, rel))
                cur.execute("""
                    SELECT m.body,
                           (SELECT body FROM messages
                            WHERE chat_id=? AND from_me=0 AND timestamp < m.timestamp
                            AND body IS NOT NULL AND length(trim(body)) > 1
                            AND body NOT LIKE '<Media omitted>%'
                            AND length(body) <= 300
                            ORDER BY timestamp DESC LIMIT 1) as contact_msg
                    FROM messages m
                    WHERE m.from_me=1 AND (m.sender_name IS NULL OR m.sender_name != 'Bot')
                    AND m.chat_id=? AND m.timestamp >= ?
                    AND m.body IS NOT NULL AND length(trim(m.body)) > 1
                    AND m.body NOT LIKE '[%'
                    ORDER BY m.timestamp DESC LIMIT 5
                """, (chat_id, chat_id, cutoff))
                for body, contact_msg in cur.fetchall():
                    if sanitize(body):
                        groups.setdefault(rel, []).append((contact_name, body, contact_msg))

            for rel, items in groups.items():
                print(f"### {rel}")
                for contact_name, andre_text, contact_text in items:
                    if contact_text:
                        print(f'- **{contact_name}:** "{contact_text[:60]}"')
                        print(f'  **André → {contact_name}:** "{andre_text[:80]}"')
                    else:
                        print(f'- **André → {contact_name}:** "{andre_text[:80]}"')
                print()


if __name__ == "__main__":
    main()
