---
name: deploy-hermes-whatsapp
description: "Executa testes de regressão, comita e envia as alterações para o GitHub, e fornece instruções para o pull em produção."
category: deploy
---

# Skill: Deploy do Hermes WhatsApp Mixed

## Quando usar esta skill

Use esta skill sempre que o usuário pedir para fazer deploy, publicar ou atualizar o bot de WhatsApp em produção.

Exemplos de triggers:
- "faz o deploy do bot"
- "sobe a versão pro github e atualiza"
- "faz o deploy do whatsapp"
- "manda pro servidor de produção"
- "deploy"

## Repositório alvo

- **Diretório do projeto:** `/Users/andrealencar/GoogleAntigravity/hermes-whatsapp-mixed`
- **Remote:** `https://github.com/empreendedorserial/hermes-whatsapp-mixed.git`
- **Branch de deploy:** `main`

---

## Instruções de execução

### Passo 1 — Executar os testes de regressão

Antes de realizar o deploy, você DEVE rodar a suite de testes automatizados para garantir que nenhuma regra de negócio crítica foi quebrada:

```bash
npm test
```

* Se algum teste falhar, **PARE** o deploy imediatamente e informe os erros ao usuário.

### Passo 2 — Verificar alterações no git

```bash
git status
```

* Identifique quais arquivos foram modificados ou criados. Certifique-se de que novos arquivos essenciais (como utilitários ou dependências) estejam incluídos.

### Passo 3 — Criar o commit de deploy

Adicione todos os arquivos modificados e crie um commit com uma mensagem clara sobre o que foi feito:

```bash
git add .
git commit -m "feat: <MENSAGEM_AQUI>"
```

### Passo 4 — Enviar para o repositório remoto

Faça o push para a branch `main`:

```bash
git push origin main
```

### Passo 5 — Orientar o usuário na atualização do servidor de produção

Após o push com sucesso, informe o usuário com as seguintes orientações em português para atualizar a instância de produção (seja no Portainer ou Easypanel):

```text
✅ Deploy enviado para o GitHub com sucesso!

Para aplicar as alterações no seu servidor de produção:

1. Acesse o seu painel do Portainer ou Easypanel.
2. Vá até a sua stack/serviço `hermes-agent` e clique em **Restart** (ou **Update/Redeploy** marcando a opção para puxar a imagem/código mais recente).
   - O contêiner executa automaticamente o script `setup.sh` durante a inicialização, garantindo que todas as personas, regras e atualizações do GitHub sejam sincronizadas e aplicadas de forma limpa, sem necessidade de console manual.
```

---

## Regras importantes

- **Sempre** execute `npm test` antes de realizar o deploy.
- **Sempre** certifique-se de manter as três cópias de `bridge.js` e `package.json` sincronizadas caso faça alterações em alguma delas.
- **Nunca** force um push (`--force`) na branch `main`.
- Se o push falhar por conflitos de histórico, peça ajuda ao usuário e não tente forçar.
