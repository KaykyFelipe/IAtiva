# Claude Code pessoal

Integração opcional com o executável oficial, inspirada nas chamadas isoladas de
`server/drivers/claude-background.ts` do OpenMausBot. Não depende de OpenMausBot
rodando, de Node como serviço, de cookies de navegador ou de API não oficial.

## Preparar o ambiente

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

## Habilitar no chat pessoal

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
