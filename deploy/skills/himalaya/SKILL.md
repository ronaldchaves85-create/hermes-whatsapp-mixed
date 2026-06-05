---
name: himalaya
description: "Himalaya CLI: IMAP/SMTP email from terminal."
version: 1.1.0
author: community
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Email, IMAP, SMTP, CLI, Communication]
    homepage: https://github.com/pimalaya/himalaya
prerequisites:
  commands: [himalaya]
---

# Himalaya Email CLI

Himalaya is a CLI email client that lets you manage emails from the terminal using IMAP, SMTP, Notmuch, or Sendmail backends.

## References

- `references/configuration.md` (config file setup + IMAP/SMTP authentication)
- `references/message-composition.md` (MML syntax for composing emails)

## Prerequisites

1. Himalaya CLI installed (`himalaya --version` to verify)
2. A configuration file at `~/.config/himalaya/config.toml`
3. IMAP/SMTP credentials configured (password stored securely)

### Installation

```bash
# Pre-built binary (Linux/macOS — recommended)
curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh | PREFIX=~/.local sh

# macOS via Homebrew
brew install himalaya

# Or via cargo (any platform with Rust)
cargo install himalaya --locked
```

## Configuration Setup

Run the interactive wizard to set up an account:

```bash
himalaya account configure
```

Or create `~/.config/himalaya/config.toml` manually:

```toml
[accounts.personal]
email = "you@example.com"
display-name = "Your Name"
default = true

backend.type = "imap"
backend.host = "imap.example.com"
backend.port = 993
backend.encryption.type = "tls"
backend.login = "you@example.com"
backend.auth.type = "password"
backend.auth.cmd = "pass show email/imap"  # or use keyring

message.send.backend.type = "smtp"
message.send.backend.host = "smtp.example.com"
message.send.backend.port = 587
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = "you@example.com"
message.send.backend.auth.type = "password"
message.send.backend.auth.cmd = "pass show email/smtp"

# Folder aliases (himalaya v1.2.0+ syntax). Required whenever the
# server's folder names don't match himalaya's canonical names
# (inbox/sent/drafts/trash). Gmail is the common case — see
# `references/configuration.md` for the `[Gmail]/Sent Mail` mapping.
folder.aliases.inbox = "INBOX"
folder.aliases.sent = "Sent"
folder.aliases.drafts = "Drafts"
folder.aliases.trash = "Trash"
```

> **Heads up on the alias syntax.** Pre-v1.2.0 docs used a
> `[accounts.NAME.folder.alias]` sub-section (singular `alias`).
> v1.2.0 silently ignores that form — TOML parses fine, but the
> alias resolver never reads it, so every lookup falls through to
> the canonical name. On Gmail this means save-to-Sent fails *after*
> SMTP delivery succeeds, and `himalaya message send` exits non-zero.
> Any caller (agent, script, user) that retries on that exit code
> will re-run the entire send — including SMTP — producing duplicate
> emails to recipients. Always use `folder.aliases.X` (plural, dotted
> keys, directly under `[accounts.NAME]`).

# Email Persona — Team Voice ("nós/a gente")

The suporte@aalencar.com.br agent speaks as **"nós/a gente"** (we/us) — 1st person plural. NOT "eu" (I) and NOT 3rd person ("ele/ela"). This differentiates it from WhatsApp (which uses direct "eu" voice).

## SOUL.md Declaration (Critical)

The email agent reads its persona from:
```
/opt/data/.hermes/profiles/email/SOUL.md
```

**Wrong SOUL.md declarations (cause 3rd-person responses):**
```markdown
# ❌ These push toward "eu" (singular) or 3rd person:
"Você é o assistente de inteligência artificial..."
"Você é o próprio André Alencar..."  # pushes toward "eu", not "nós"

# ✅ Correct declaration:
"Você é a equipe de suporte do André Alencar..."
```

**Pessoa gramatical rules:**
- Email: SEMPRE 1ª pessoa PLURAL (nós). ✅ "Vimos sua mensagem..." ❌ "Vi sua mensagem..." / "Ele precisa..."
- WhatsApp: 1ª pessoa SINGULAR (eu). "OI.. blz? Tudo bem?"

**Critical SOUL.md rule** — the persona declaration must NOT say "assistente de IA", "inteligência artificial", or anything that implies 3rd person or bot identity. Use wording like: "Você é a equipe de suporte do André Alencar" or "Você responde como a equipe de suporte".

**Wrong SOUL.md wording (causes 3rd person responses):**
- "Você é o assistente de inteligência artificial..."
- "Você é o próprio André Alencar..." (pushes toward "eu" instead of "nós")

**Correct SOUL.md wording:**
- "Você é a equipe de suporte da empresa [nome/empresa]..."

**Pessoa gramatical:** SEMPRE em 1ª pessoa plural (nós). NUNCA use 3ª pessoa (ele/ela).
- ✅ "Olá, vimos sua mensagem sobre o cupom."
- ❌ "O cliente entrou em contato, ele precisa de..."

See `references/email-persona.md` for full voice comparison table.

## Reference Files

- `references/email-persona.md` — persona rules, voice comparison (WhatsApp vs Email)

## Hermes Integration Notes

- **Reading, listing, searching, moving, deleting** all work directly through the terminal tool
- **Composing/replying/forwarding** — piped input (`cat << EOF | himalaya template send`) is recommended for reliability. Interactive `$EDITOR` mode works with `pty=true` + background + process tool, but requires knowing the editor and its commands
- Use `--output json` for structured output that's easier to parse programmatically
- The `himalaya account configure` wizard requires interactive input — use PTY mode: `terminal(command="himalaya account configure", pty=true)`

## Common Operations

### List Folders

```bash
himalaya folder list
```

### List Emails

List emails in INBOX (default):

```bash
himalaya envelope list
```

List emails in a specific folder:

```bash
himalaya envelope list --folder "Sent"
```

List with pagination:

```bash
himalaya envelope list --page 1 --page-size 20
```

### Search Emails

```bash
himalaya envelope list from john@example.com subject meeting
```

### Read an Email

Read email by ID (shows plain text):

```bash
himalaya message read 42
```

Export raw MIME:

```bash
himalaya message export 42 --full
```

### Reply to an Email

To reply non-interactively from Hermes, read the original message, compose a reply, and pipe it:

```bash
# Get the reply template, edit it, and send
himalaya template reply 42 | sed 's/^$/\nYour reply text here\n/' | himalaya template send
```

Or build the reply manually:

```bash
cat << 'EOF' | himalaya template send
From: you@example.com
To: sender@example.com
Subject: Re: Original Subject
In-Reply-To: <original-message-id>

Your reply here.
EOF
```

Reply-all (interactive — needs $EDITOR, use template approach above instead):

```bash
himalaya message reply 42 --all
```

### Forward an Email

```bash
# Get forward template and pipe with modifications
himalaya template forward 42 | sed 's/^To:.*/To: newrecipient@example.com/' | himalaya template send
```

### Write a New Email

**Non-interactive (use this from Hermes)** — pipe the message via stdin:

```bash
cat << 'EOF' | himalaya template send
From: you@example.com
To: recipient@example.com
Subject: Test Message

Hello from Himalaya!
EOF
```

Or with headers flag:

```bash
himalaya message write -H "To:recipient@example.com" -H "Subject:Test" "Message body here"
```

Note: `himalaya message write` without piped input opens `$EDITOR`. This works with `pty=true` + background mode, but piping is simpler and more reliable.

### Move/Copy Emails

Move to folder:

```bash
himalaya message move 42 "Archive"
```

Copy to folder:

```bash
himalaya message copy 42 "Important"
```

### Delete an Email

```bash
himalaya message delete 42
```

### Manage Flags

Add flag:

```bash
himalaya flag add 42 --flag seen
```

Remove flag:

```bash
himalaya flag remove 42 --flag seen
```

## Multiple Accounts

List accounts:

```bash
himalaya account list
```

Use a specific account:

```bash
himalaya --account work envelope list
```

## Attachments

Save attachments from a message:

```bash
himalaya attachment download 42
```

Save to specific directory:

```bash
himalaya attachment download 42 --dir ~/Downloads
```

## Example Responses

### Exemplo: Cupom de desconto
```
Olá, tudo bem?

Vimos sua mensagem sobre cupom de desconto. Temos o cupom **SERIAL15** que oferece 15% de desconto em compras acima de R$ 50 na nossa loja.

Caso tenha mais dúvidas, estamos à disposição!

Abraços,
André Alencar
suporte@aalencar.com.br
```

### Exemplo: Dúvida sobre curso
```
Olá, tudo bem?

Agradecemos o seu contato e o interesse em nosso curso completo de Automações com n8n e Inteligência Artificial!

O nosso treinamento foi desenvolvido de forma extremamente prática para ensinar você a criar automações avançadas do zero. Além de todo o conteúdo gravado, você terá acesso exclusivo ao portal da nossa comunidade ativa, onde disponibilizamos gratuitamente diversos fluxos de cortesia e bônus exclusivos para os membros.

Você pode conferir todos os módulos, depoimentos de alunos e realizar a sua inscrição com total segurança através do nosso link oficial de vendas: https://aalencar.com.br

Caso tenha mais alguma dúvida, sinta-se à vontade para responder a este e-mail.

Abraços,
André Alencar
suporte@aalencar.com.br
```

## Correct Response Examples

### Cupom de desconto
```
Olá, tudo bem?

Vimos sua mensagem sobre cupom de desconto. Temos o cupom **SERIAL15** 
que oferece 15% de desconto em compras acima de R$ 50 na nossa loja.

Caso tenha mais dúvidas, estamos à disposição!

Abraços,
André Alencar
suporte@aalencar.com.br
```

### Dúvida sobre curso
```
Olá, tudo bem?

Agradecemos o seu contato e o interesse em nosso curso completo de 
Automações com n8n e Inteligência Artificial!

O nosso treinamento foi desenvolvido de forma extremamente prática 
para ensinar você a criar automações avançadas do zero. Além de todo 
o conteúdo gravado, você terá acesso exclusivo ao portal da nossa 
comunidade ativa, onde disponibilizamos gratuitamente diversos fluxos 
de cortesia e bônus exclusivos para os membros.

Você pode conferir todos os módulos, depoimentos de alunos e realizar 
a sua inscrição com total segurança através do nosso link oficial de 
vendas: https://aalencar.com.br

Caso tenha mais alguma dúvida, sinta-se à vontade para responder a este e-mail.

Abraços,
André Alencar
suporte@aalencar.com.br
```

## Output Formats

Most commands support `--output` for structured output:

```bash
himalaya envelope list --output json
himalaya envelope list --output plain
```

## Debugging

Enable debug logging:

```bash
RUST_LOG=debug himalaya envelope list
```

Full trace with backtrace:

```bash
RUST_LOG=trace RUST_BACKTRACE=1 himalaya envelope list
```

## Tips

- Use `himalaya --help` or `himalaya <command> --help` for detailed usage.
- Message IDs are relative to the current folder; re-list after folder changes.
- For composing rich emails with attachments, use MML syntax (see `references/message-composition.md`).
- Store passwords securely using `pass`, system keyring, or a command that outputs the password.
