# Manutenção automática e atualização sem interrupção (M31/M32)

## Visão geral

M36: também é possível usar conta ChatGPT pelo Codex oficial, separada da conta de atendimento.
Em **Conectar Claude / ChatGPT**, conecte e selecione o provedor no agente de manutenção.
Veja [CHATGPT.md](CHATGPT.md). Conexões Claude existentes e interruptores são preservados.

| Componente | Faz | Não tem |
|---|---|---|
| Agente de atendimento (chat + worker) | resolve pedidos; quando algo quebra, registra um **incidente sanitizado** | Git, Docker, escrita no código, ferramentas do Claude (`--tools ""`) |
| Executor de manutenção (`scripts/manutencao.py`, no host) | reproduz, corrige, testa, publica e confere | acesso ao `.env`, ao ERP, ao `var/` do app ou a dados de usuários |

## Incidentes

- Onde: tela de administração do Django → **Incidentes**, ou no servidor
  `docker compose -p iativa exec app-<cor> python manage.py manutencao listar`.
- O que guardam: categoria, código, componente/operação, versão, correlação, rastreamento
  `arquivo:linha:função` só do código do IAtiva, e condições técnicas (etapa, tipo). **Nunca** a
  mensagem do erro, texto do usuário, nomes, documentos, resultados, SQL ou credenciais.
- Mesma causa = mesmo incidente (contador de ocorrências).

### Categorias e o que acontece

| Categoria | Ação |
|---|---|
| Erro técnico reproduzível (no código do IAtiva) | **elegível a correção automática** |
| Falha transitória (tempo, conexão, bloqueio) | tratada na hora (repetir, lotes menores); não mexe no código |
| Informação/regra de negócio desconhecida | a IA pergunta à pessoa; não mexe no código |
| Falta de permissão | nunca amplia acesso; nada a corrigir |
| Limitação da fonte de dados | registrada; não mexe no código |
| Capacidade técnica ausente | com a trilha do pedido (M38): elegível à **triagem** — só é corrigida se a manutenção reproduzir com dados sintéticos; sem trilha, fica para decisão administrativa |

## Correções automáticas

São automáticas só as correções **pequenas e delimitadas** de erro técnico:
- até 6 arquivos e 250 linhas, só em `iativa/`, `tests/`, `templates/`, `static/`;
- com um **teste de regressão novo** que falha na versão afetada e passa com a correção;
- com a suíte inteira passando, na versão integrada à `main` atual.

Ficam **bloqueadas** (precisam de pessoa): permissões (`iativa/accounts`), auditoria, privacidade,
manutenção e saúde, validador de SQL, conexão com o ERP, settings, scripts, Docker, dependências,
**qualquer migração**, remoção ou alteração de testes existentes, e qualquer incidente que volte a
acontecer depois de corrigido (reabre uma vez; na segunda, bloqueia).

Limites: 3 correções por dia, 30 min por correção, uma por vez (fila com `flock`), 2 tentativas por
incidente. O executor não altera a própria configuração nem os critérios de aprovação.

## Publicação e implantação

1. Commit só com os arquivos da correção, mensagem `fix(manutenção): incidente #N ...`.
2. Integra a `main` atual por **rebase** (nunca force push); conflito = não publica; testa de novo.
3. Push na `main` (política M7) e `scripts/atualizar.sh`: constrói `iativa-app:<commit>`, testa essa
   imagem e implanta **a mesma imagem** com `scripts/implantar.sh`.
4. Confere a versão no ar (`X-IAtiva-Versao`) = commit; marca o incidente como publicado e retoma
   **uma vez** as tarefas que falharam por ele (permissões reconferidas pelo worker).

Enquanto isso, as consultas continuam no ambiente ativo: o código em uso nunca é editado.

## Atualização sem interrupção (azul/verde)

- Duas cores (`app-azul`/`app-verde` + workers) com imagem por commit; `proxy` (nginx) na porta
  `127.0.0.1:8000` aponta para a cor ativa.
- Antes de trocar: migrações à parte (só **aditivas** passam: `manage.py verificar_migracoes`), cor
  nova sobe e só recebe tráfego quando `/saude/pronto/` responde com a versão certa.
- Troca: `nginx -s reload` (as conexões em andamento continuam na cor antiga).
- Drenagem: o worker antigo para de pegar tarefas e termina a que tem; o app antigo só para quando
  `/saude/ativas/` = 0. Se não esvaziar em 15 min, continua rodando e a drenagem fica **pendente**.
- Servidor de aplicação: gunicorn, 1 processo com 16 threads (o login do Claude e o "Pensando…"
  vivem na memória do processo).

## Operação

| Quero | Comando (no servidor, em /opt/IAtiva) |
|---|---|
| Ver a versão/commit no ar | `scripts/implantar.sh --estado` (ou o cabeçalho `X-IAtiva-Versao`) |
| Reverter a última atualização | `scripts/implantar.sh --reverter` |
| Terminar uma drenagem pendente | `scripts/implantar.sh --drenar` |
| **Suspender novas publicações sem parar consultas** | em `/etc/iativa/manutencao.env`: `MANUTENCAO_PUBLICAR=0` (ou `MANUTENCAO_ATIVA=0`) |
| Ver o executor | `python3 scripts/manutencao.py --estado`; log em `/opt/iativa-manutencao/manutencao.log` |
| Ver incidentes | `docker compose -p iativa exec app-<cor> python manage.py manutencao listar` |
| Instalar o agendamento | `sudo scripts/instalar_manutencao.sh` |
| Teste real de continuidade/reversão | `scripts/testar_implantacao.sh` (projeto separado, porta 18000) |

Reversão do **banco**: "voltar o Git" não desfaz migrações. Por isso só migrações aditivas passam
no automático: a versão anterior continua funcionando com o esquema novo e a reversão é só de tráfego.

## Para ligar a publicação automática (pendente)

1. Uma chave SSH com **escrita** no repositório, só para o executor (deploy key), em
   `MANUTENCAO_GIT_SSH_KEY`. Diego informou em 2026-11-12 que o push simulado foi aceito
   e o executor reconhece a credencial. Isso não equivale a uma publicação real.
2. O login do Claude **só da manutenção**: na tela **Conexões Claude** do IAtiva (TI), cartão
   "Agente de manutenção" → Conectar Claude, e escolher o modelo (padrão Opus). Fica em
   `var/claude-manutencao` do volume do app; o executor encontra sozinho (ou use `MANUTENCAO_CLAUDE_DIR`).
3. `MANUTENCAO_ATIVA=1` e, depois de acompanhar algumas correções preparadas, `MANUTENCAO_PUBLICAR=1`.

### Escolher o modelo da manutenção

Em **Conectar Claude / Conexões Claude**, use o cartão **Agente de manutenção**.
Conecte pelo link oficial, conclua com o código e use **Verificar conexão**.
Escolha Opus, Sonnet ou Haiku e o esforço e clique em **Salvar**. O executor lê essas
preferências no próximo uso; o modelo do atendimento continua independente.

O padrão é Opus com esforço padrão da CLI, sem herdar o esforço do atendimento.
As escolhas ficam em `iativa-preferencias.json` da pasta de manutenção, com gravação
atômica. Preferências inválidas não derrubam o executor: ele usa o padrão. A indicação
**Conexão salva** não comprova que a sessão ainda é válida; **Verificar conexão** consulta a CLI.

Conectar ou salvar o modelo não altera os interruptores em `/etc/iativa/manutencao.env`.
Primeiro habilite somente preparação/validação (`MANUTENCAO_ATIVA=1`, `MANUTENCAO_PUBLICAR=0`);
habilite publicação posteriormente.
