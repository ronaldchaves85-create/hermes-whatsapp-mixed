# 📋 Guia Passo a Passo - Configuração do Hermes Agent (Modo Misto)

Este guia prático foi criado para guiar você, aluno da **Comunidade Empreendedor Serial**, na configuração completa do seu robô do zero até ele estar atendendo seus clientes com o Gemini 3.5 Flash e atuando como seu assistente pessoal!

Usaremos um método super moderno: **você editará seus dados visualmente no seu GitHub** e o seu servidor importará tudo de forma automática. Siga os **5 passos simples** abaixo:

---

## 🎨 PASSO 1: Fazer um Fork e Customizar no seu GitHub

Todo o treinamento do robô e alteração de regras é feito de forma visual diretamente na interface do seu GitHub!

1. Na página do repositório oficial (`github.com/empreendedorserial/hermes-whatsapp-mixed`), clique no botão **Fork** (canto superior direito) para criar uma cópia dele na sua própria conta do GitHub.
2. No seu repositório pessoal recém-criado, edite os arquivos diretamente pelo seu navegador (clicando no ícone de lápis ✏️):
   * 📄 **`support_rules.md`**: Coloque as informações da sua empresa, preços, links de checkout (Kiwify, Hotmart, etc.) e formas de suporte. Clique em **Commit changes** para salvar.
   * 📄 **`SOUL.md`**: Personalize o tom e o comportamento do robô se desejar. Clique em **Commit changes** para salvar.

---

## 🛠️ PASSO 2: Subir a Stack e Chaves no Portainer

Todo o gerenciamento de chaves e domínios é feito de forma visual na interface do Portainer!

1. Acesse o painel do seu **Portainer**.
2. Vá no menu lateral em **Stacks** e clique em **Add stack**.
3. Escolha um nome para a stack (ex: `hermes-agent`).
4. Cole o conteúdo do arquivo `docker-compose.yml` (disponível no repositório) no editor de texto.
5. Em **Environment variables** (Variáveis de ambiente) do Portainer, preencha seus domínios e chaves:
   * **`HERMES_DASH_HOST`** = O domínio do seu painel visual (ex: `hermes.seu-dominio.com`).
   * **`HERMES_API_HOST`** = O subdomínio da API (ex: `hermes-api.seu-dominio.com`).
   * **`GOOGLE_API_KEY`** = Cole aqui sua chave do Gemini.
   * **`API_SERVER_KEY`** = Crie uma senha forte para proteger sua API.
   * *Opcional:* Preencha `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` se for usar suporte por e-mail (Gmail).
6. Clique no botão azul **Deploy the stack** na parte inferior da tela e aguarde o status mudar para `running`.

> 🔒 **SSL Automático:** O Traefik integrado na Stack configurará os certificados SSL seguros (HTTPS) para você de forma totalmente transparente e imediata!

---

## ⚡ PASSO 3: Rodar a Sincronização Automatizada (1 Clique)

Agora faremos seu servidor puxar as regras que você salvou no seu GitHub no Passo 1!

1. No painel do Portainer, clique em **Containers** e encontre o container `hermes-agent`.
2. Clique no ícone de **Console** (`>_`) correspondente a ele.
3. Clique em **Connect** para abrir o terminal integrado.
4. **Substitua `SEU_USUARIO_GITHUB` pelo seu usuário real do GitHub** no comando abaixo, cole-o no terminal e aprete **Enter**:

```bash
curl -sSL https://raw.githubusercontent.com/SEU_USUARIO_GITHUB/hermes-whatsapp-mixed/main/setup.sh | bash -s SEU_USUARIO_GITHUB
```

> **Sincronização Ativa:** O seu servidor baixou a persona (`SOUL.md`) e regras de vendas (`support_rules.md`) direto do seu GitHub pessoal, configurou o `config.yaml` e corrigiu a ponte do WhatsApp! 
> 
> 💡 **Dica de Ouro:** Sempre que quiser mudar os preços ou as regras do seu negócio, basta editá-las no seu GitHub e rodar este mesmo comando novamente no console. Seu servidor atualizará tudo em segundos!

---

## 📲 PASSO 4: Conectar o WhatsApp e Ativar

1. Volte ao **Console** do container `hermes-agent` no Portainer (ou abra no Terminal Web do seu Dashboard).
2. Digite o comando abaixo para iniciar o Hermes e gerar o QR Code de pareamento:
   ```bash
   hermes
   ```
3. Um **QR Code** será exibido na tela do terminal. 
4. Abra o WhatsApp no seu celular, vá em **Aparelhos conectados** -> **Conectar um aparelho** e escaneie o código da tela.
5. Assim que a conexão for concluída, você poderá fechar o terminal interativo digitando `/exit`.
6. Para que as novas diretrizes da persona sincronizadas no Passo 3 entrem em vigor na ponte do WhatsApp de forma 100% limpa e estável:
   * Vá ao painel do seu **Portainer**, entre na sua Stack do `hermes-agent` e clique em **Restart** (Reiniciar) no container para recarregar o robô de forma limpa e segura!

---

## 🧪 PASSO 5: O Diagnóstico de Sucesso!

Abra a conversa do seu WhatsApp com **você mesmo (Self-Chat)** e envie a seguinte mensagem para testar a integridade do robô:

> *"Hermes, faça um teste de diagnóstico do meu sistema. Verifique se a minha persona SOUL.md e as regras do support_rules.md foram carregadas, me diga qual modelo de IA você está usando e liste os nossos caminhos persistentes."*

### ✅ Resposta Esperada de Sucesso:
* Ele deve se apresentar como o *Hermes Agent - Edição Especial Empreendedor Serial*.
* Confirmar que está rodando com o **Gemini 3.5 Flash**.
* Confirmar que encontrou e leu seu arquivo `/opt/data/support_rules.md`.
* Listar seus caminhos sob `/opt/data/`.

---

## 🕹️ Comandos de Controle (WhatsApp)

Você agora tem superpoderes! Diretamente do seu chat privado, envie os seguintes comandos para o robô para controlar o atendimento comercial de clientes:

* 🚫 **`stop_bot`** - Pausa imediatamente o chatbot comercial para clientes (enquanto você continua podendo usar o assistente pessoal normalmente).
* 🟢 **`start_bot`** - Reativa o chatbot comercial para responder seus clientes no piloto automático.

---
*Bons negócios e automações!*  
*Comunidade Empreendedor Serial (André Alencar).*
