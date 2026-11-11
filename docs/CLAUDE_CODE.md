# Claude Code pessoal

Integração opcional com o executável oficial, inspirada nas chamadas isoladas de
`server/drivers/claude-background.ts` do OpenMausBot. Não depende de OpenMausBot
rodando, de Node como serviço, de cookies de navegador ou de API não oficial.

## Conectar pela interface (M12)

1. Entre no IAtiva com seu superusuário pessoal.
2. Na lateral, abra **Conectar Claude** (rota `/configuracoes/claude/`).
3. Clique em **Conectar Claude** e aguarde o link oficial.
4. Use **Abrir login do Claude**, entre na sua própria conta e copie o código
   exibido pelo Claude.
5. Cole o código no campo oculto e clique em **Concluir**. Aguarde a confirmação.

O provedor fica selecionado apenas para suas mensagens; outras contas mantêm o
provedor configurado anteriormente. O envio pelo chat continua dependendo de
`IATIVA_AGENTE_ENVIO_LIBERADO=1`. O login sozinho não libera dados para o modelo.
Quando essa liberação estiver ausente, o chat informa o bloqueio em vez de falhar.

**Verificar conexão** confere a autenticação sem enviar uma pergunta. **Cancelar**
encerra um login pendente. **Desconectar** desativa a seleção para seu chat e pede
à CLI que encerre a conta exclusiva do IAtiva.

Cada usuário possui um diretório próprio em `var/claude-pessoal/usuario-ID`,
ignorado pelo Git. O único estado adicional do IAtiva é um marcador vazio de
ativação; o código de login não é salvo em arquivo, banco, sessão ou histórico.
A CLI oficial gerencia suas próprias credenciais nesse diretório. Não copie nem
publique essa pasta. A conexão não usa o diretório padrão do OpenMausBot.

Somente superusuários ativos podem gerenciar ou usar essa conexão pessoal.
`IATIVA_CLAUDE_USUARIO` pode restringir também a um username específico.
`IATIVA_CLAUDE_CONTAS_DIR` troca a raiz por um caminho absoluto persistente.
Estas permissões não concedem acesso ao ERP: continuam valendo as políticas
de consulta, setor e filial já existentes.

A detecção procura a CLI no PATH, nos diretórios nativos usuais e na instalação
do Claude Desktop para Windows. Campo/caminho vazio significa detecção automática;
o executável precisa existir no mesmo ambiente onde o Django roda. A instalação
do aplicativo desktop do host não instala a CLI dentro de um container.

O login é temporário: até 30 segundos para mostrar o link, 15 minutos para entrar
e 60 segundos para confirmar o código. O fluxo pertence ao usuário **e à sessão**
que o iniciou. Reiniciar o processo invalida logins ainda em andamento, não a
conta já conectada. Use um único processo Django para este fluxo local (como o
`runserver --noreload` do projeto); balanceamento entre vários workers exige
um serviço dedicado de autenticação e não é suportado nesta etapa.

O login pela interface não instala a CLI nem implementa OAuth próprio. As páginas
de autenticação e a troca do código continuam sob controle do binário oficial.

## Preparar o ambiente

### Docker do IAtiva

A imagem instala a CLI nativa oficial 2.1.321 como usuário `iativa`, em
`/home/iativa/.local/bin/claude`, disponível no PATH do container. Não é necessário
montar diretórios do OpenMausBot, copiar cookies ou instalar Node/NVM.
O build precisa de acesso HTTPS ao instalador e aos downloads da Anthropic e
confere a versão e a presença de `--safe-mode`; se a instalação falhar, o build falha.
`CLAUDE_CODE_VERSION` é um argumento de build para atualizar a versão explicitamente.

Na pasta do repositório no servidor, atualize o código e reconstrua somente o app:

```bash
git pull --ff-only
docker compose up -d --build --no-deps app
docker compose exec app claude --version
```

Se seu `.env` tiver `IATIVA_CLAUDE_CLI` apontando para o caminho NVM do OpenMausBot,
troque por `IATIVA_CLAUDE_CLI=claude` antes de recriar o container. Um caminho
explícito errado tem prioridade sobre a detecção automática.

Depois, use **Conectar Claude** no site. O login continua exclusivo por usuário e
é mantido em `/app/var/claude-pessoal`, dentro do volume `iativa_var` já definido
no Compose. Não use `docker compose down -v`: isso apagaria os volumes existentes.
Não faça login durante o build: a imagem contém somente a CLI, sem credenciais.

O Docker Engine não estava disponível na máquina de desenvolvimento nesta
alteração; o build real e o login dentro do container precisam ser confirmados
no servidor. A instalação segue https://code.claude.com/docs/en/setup.

### Execução direta no sistema operacional

Instale ou atualize Claude Code seguindo https://code.claude.com/docs/en/setup.
Use a mesma conta do sistema operacional que executa Python/Django. O login de
outro computador, usuário, container ou serviço não é herdado automaticamente.
No Windows use o executável nativo `claude.exe`, sem launcher `.cmd`/PowerShell.

Confira `claude --version` e `claude --help`, depois autentique pelo fluxo oficial:

```text
claude auth login
claude auth status --json
```

Não use `--console` para este adaptador. Ele aceita somente `loggedIn: true` e
`authMethod: claude.ai`; estados desconhecidos, API, helpers e provedores externos
são bloqueados. Não copie tokens entre aplicações. Se usar uma conta configurada
no OpenMausBot com diretório próprio, configure o mesmo diretório de credenciais
apenas quando pertencer à sua própria conta e estiver acessível ao processo Python.

Login por assinatura não comprova ausência de cobrança: confira seu plano, cotas
e se o uso extra pago está desativado nas configurações do Claude. O adaptador não
habilita uso extra nem configura chave ou fallback pago. Ele também não consegue
garantir que a conta não tenha uso extra previamente habilitado.

## Testar sem configurar PostgreSQL ou SQL Server

Na raiz do IAtiva, com o Python da `.venv` (no Windows,
`.\.venv\Scripts\python.exe`), execute:

```text
python manage.py testar_claude --settings=config.settings.test --cli CAMINHO_ABSOLUTO_DA_CLI
```

Isso confere compatibilidade e autenticação, sem enviar prompt de inferência.
O comando não imprime e-mail, tokens, respostas HTTP nem detalhes de credenciais.
Acrescente `--config-dir DIRETORIO_ABSOLUTO` se o seu login usar diretório próprio.

Para uma chamada com mensagem fictícia, sem acessar o ERP:

```text
python manage.py testar_claude --settings=config.settings.test --cli CAMINHO_ABSOLUTO_DA_CLI --enviar
```

O comando valida a resposta contra o mesmo contrato JSON do agente. A liberação
de envio nesse comando vale somente para sua entrada fictícia; não habilita o chat.
Não coloca pergunta, resposta ou credenciais em arquivos temporários.

## Alternativa: configurar um provedor fixo pelo ambiente

Somente depois do teste, configure no `.env` local, preservando as demais opções:

```dotenv
IATIVA_AGENTE_PROVEDOR=claude-code
IATIVA_CLAUDE_CLI=CAMINHO_ABSOLUTO_DA_CLI
IATIVA_CLAUDE_CONFIG_DIR=
IATIVA_CLAUDE_MODELO=sonnet
IATIVA_CLAUDE_TIMEOUT_S=120
```

O chat continua exigindo `IATIVA_AGENTE_ENVIO_LIBERADO=1`, conforme as regras de
privacidade do projeto. Não foi alterado nem habilitado automaticamente.
Reinicie o processo Django após configurar. O provedor padrão continua `teste`.
Para usuários autenticados na interface, o provedor `claude-code` sempre resolve
o diretório pessoal correspondente; não compartilha a conta padrão do processo.

## Isolamento e limites

- Prompt por stdin, `shell=False`, diretório temporário vazio e ambiente restrito
  sem credenciais do ERP, tokens explícitos ou chaves de outros provedores.
- `--safe-mode`, ferramentas vazias, MCP vazio e negado e
  `--no-session-persistence`; nenhuma continuação global ou sessão da CLI.
- Flags obrigatórios ausentes bloqueiam uso; nunca relaxa a proteção para CLI antiga.
- Contexto limitado a 200 KB; stdout e stderr limitados a 1 MB cada; timeout
  configurável entre 1 e 600 segundos, encerrando o processo ao exceder limites.
- O histórico continua no Django, conforme as permissões e conversa já existentes.
- Saída JSON estruturada e contrato de ações/parâmetros validados no backend.
- Erros do provedor são classificados e sanitizados; nenhuma resposta bruta é
  persistida. Timeout, autenticação e limites não provocam repetição pelo agente.

O modelo continua sem receber resultados do ERP. O plano, compilação de T-SQL,
validação AST, autorização, conta somente leitura e limites de execução existentes
continuam responsáveis por cada consulta. Este adaptador não habilita SQL livre.

## Escopo e validação

Autorizado por Diego em 2026-11-11 para seu uso pessoal. Não configura login Claude
para terceiros nem transforma uma assinatura pessoal em backend compartilhado.
Condições: https://code.claude.com/docs/en/legal-and-compliance.
CLI: https://code.claude.com/docs/en/cli-reference.

Testes automatizados usam um executor simulado e subprocessos Python locais para
verificar stdin, ambiente, timeout e limite de saída. Não comprovam qualidade do
modelo nem o estado do login. Teste real exige CLI instalada, login e rede; teste
com ERP exige também a conexão local autorizada e VPN, quando necessária.

Em 2026-11-11 foi detectado Claude Code 2.1.321 neste Windows. O diagnóstico
confirmou o login já existente e uma chamada sintética real retornou uma decisão
JSON válida. Isso não valida o login completo pela nova tela com uma conta real,
não comprova custo zero e não envolve consultas ao SQL Server.
