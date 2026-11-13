# Contas ChatGPT nos agentes (M36)

A integração usa o Codex CLI oficial e seu app-server por stdio. Não usa cookies do navegador,
endpoints privados do ChatGPT nem chave de API. A CLI é instalada na imagem com versão fixa
0.167.0; não depende de uma instalação no computador de quem abre o site.

## Conectar e escolher

1. Com função TI, abra **Conexões de IA**. A rota antiga de Conectar Claude continua válida.
2. No agente desejado (atendimento ou manutenção), encontre **Conta ChatGPT** e clique em **Conectar ChatGPT**.
3. Abra o link oficial `https://auth.openai.com/codex/device` e digite o código mostrado na tela
   **na página da OpenAI**, não no chat. Complete o login na sua conta. A confirmação é automática.
4. Use **Atualizar modelos ChatGPT** para carregar o catálogo da conta. Escolha o modelo e o esforço
   compatível e clique em **Salvar ChatGPT**. Também é possível manter o padrão da conta/CLI.
5. Clique em **Usar ChatGPT neste agente** para escolher explicitamente o provedor. Para voltar,
   clique em **Usar Claude neste agente**. Trocar não desconecta a outra conta.

O login por código precisa estar habilitado nas configurações de segurança da conta ou nas
permissões do workspace. A conta/plano precisa ter acesso ao Codex e ao modelo escolhido.
Não se promete custo zero, ausência de cotas ou os mesmos modelos do seletor do ChatGPT web.

## Separação

| Agente | Conta ChatGPT | Preferência do provedor |
|---|---|---|
| Atendimento | `var/chatgpt/usuario-ID` | `var/conexoes/atendimento-ID.json` |
| Manutenção | `var/chatgpt/manutencao` (conta do sistema gerenciada pela TI) | `var/conexoes/manutencao.json` |

Cada conta tem suas preferências de modelo/esforço e catálogo. Pastas Claude existentes são
preservadas. Sem seleção explícita, uma conexão Claude já ativa mantém a precedência para não
trocar o comportamento existente. Não há fallback silencioso entre provedores, nem para API paga.

O código de login vive só na memória do processo, pertence ao usuário/sessão/perfil, expira e
desaparece ao concluir ou cancelar. Tokens são gerenciados pela CLI em sua pasta exclusiva;
não são mostrados, copiados de outras aplicações, colocados em mensagens ou versionados.
O login é volátil enquanto está em andamento: uma troca/reinício do processo pode exigir iniciá-lo
novamente. Use o único processo web já previsto no projeto.

## Atendimento e manutenção

No atendimento, a conexão mantém a liberação de privacidade existente (`IATIVA_AGENTE_ENVIO_LIBERADO`).
As chamadas usam threads efêmeras, JSON Schema, ambiente sem chaves/senhas herdadas, diretório
temporário, recursos de execução/MCP/apps desabilitados e nenhuma raiz de ambiente selecionada.
Pedidos de ferramentas/aprovações são negados pelo cliente. Resultados do ERP continuam fora do
modelo; a validação e execução das consultas ficam no backend.

O executor da manutenção lê a seleção e preferências do volume persistente no começo do trabalho.
Com ChatGPT selecionado, chama `codex exec` em container isolado, com sandbox externa Docker: usuário sem privilégios, raiz somente leitura, capabilities removidas,
`no-new-privileges` e temporários em tmpfs. Apenas worktree e pasta própria da conta são graváveis;
sem `.env`, credenciais Git ou socket Docker. A CLI usa `--sandbox danger-full-access` **apenas dentro
desse container isolado**, pois o Bubblewrap interno não funciona nos namespaces Docker deste host.
Não se libera `--privileged`, namespaces do host nem se muda a política de segurança do servidor.
A cópia criada pelo executor root recebe escrita para o grupo 10001, sem trocar o dono,
seguir links ou alterar outras pastas. O ponteiro `.git` fica montado somente leitura. Continua sujeito a teste de regressão, suíte,
lista de arquivos, tamanho, publicação e implantação azul/verde existentes. Os arquivos de
autenticação/seleção e adaptadores ficam protegidos contra alteração pela própria manutenção.

Conectar ou trocar o provedor não altera `MANUTENCAO_ATIVA` e `MANUTENCAO_PUBLICAR`.
Se o ChatGPT selecionado não estiver conectado, o executor não troca para Claude automaticamente.

Configurações opcionais: `IATIVA_CODEX_CLI`, `IATIVA_CODEX_CONTAS_DIR`, `IATIVA_CODEX_TIMEOUT_S`.
No host, `MANUTENCAO_CODEX_DIR` permite informar a mesma pasta de manutenção quando o volume
não estiver no caminho padrão. `MANUTENCAO_PROVEDOR`, se configurado pelo administrador,
prevalece sobre a seleção da interface. A seleção padrão da tela usa o volume `iativa_iativa_var`.

## Fontes oficiais e validação

- [Autenticação e device code](https://learn.chatgpt.com/docs/auth#login-on-headless-devices).
- [Protocolo app-server e catálogo model/list](https://learn.chatgpt.com/docs/app-server).
- [Execução não interativa](https://learn.chatgpt.com/docs/non-interactive-mode).

Testes: `python -m pytest -q tests/test_chatgpt.py tests/test_manutencao.py`.
Cobrem isolamento, autorização/CSRF, código por sessão, catálogo e esforço, schema, privacidade,
negação de ferramentas, seleção independente e montagem do comando de manutenção.
O fluxo oficial de device code foi iniciado e cancelado em uma pasta nova, sem usar credenciais
existentes. Login de uma conta real e inferência só podem ser conferidos após a pessoa conectar
suas contas pela página oficial.
