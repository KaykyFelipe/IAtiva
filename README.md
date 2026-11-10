# IAtiva

Chat corporativo para consultas de leitura ao TOTVS Protheus em linguagem natural.

> **Fase atual: Etapa 4, agente e esclarecimentos (em validação, provedor de teste).** O chat
> interpreta a pergunta, pede os parâmetros que faltam e deixa a solicitação pronta. Ainda não há
> consulta de dados ao ERP (Etapa 5). A mensagem não é armazenada.
> Acompanhamento: [`STATUS.md`](STATUS.md) · decisões: [`DECISOES.md`](DECISOES.md) ·
> plano: [`PLANO.md`](PLANO.md) · requisitos: [`docs/PEDIDO_INICIAL.md`](docs/PEDIDO_INICIAL.md).

## Regras desta fase

- A aplicação só aceita acesso por **127.0.0.1**. O `runserver` recusa `0.0.0.0`, IPs da rede e IPv6,
  e uma verificação do sistema impede a inicialização se `ALLOWED_HOSTS` sair de localhost.
- O ambiente usa **somente dados sintéticos**. O ERP real não é acessado.
- **Nada de SQL, resultado ou mensagem do chat é persistido:** não vai para banco, log, cache, sessão
  ou armazenamento do navegador. As respostas usam `Cache-Control: no-store`.
- `DEBUG` é fixo em `False`, portanto não existe página técnica de erro.
- O `runserver` é **servidor de desenvolvimento** e não deve ser usado em produção (Etapa 9).

## Requisitos

- Python 3.12 ou superior (testado com 3.13)
- PostgreSQL 16 para o banco da aplicação, instalado na máquina ou via Docker (`compose.yaml`)
- Git

## Instalação

### 1. Obter o código e criar o ambiente virtual

Linux / macOS:

```bash
git clone https://github.com/KaykyFelipe/IAtiva.git
cd IAtiva
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Windows (PowerShell):

```powershell
git clone https://github.com/KaykyFelipe/IAtiva.git
cd IAtiva
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

### 2. Configurar o `.env`

Copie `.env.example` para `.env` (`cp` no Linux/macOS, `Copy-Item` no Windows) e preencha os campos:

- `DJANGO_SECRET_KEY`: gere com `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `IATIVA_DB_PASSWORD`: senha do usuário do banco da aplicação

O arquivo `.env` está no `.gitignore` e **não deve ser versionado**.

### 3. Criar o banco da aplicação (PostgreSQL)

**Opção A, PostgreSQL instalado na máquina.** Como superusuário do PostgreSQL, use a mesma senha do `.env`:

```sql
CREATE ROLE iativa LOGIN PASSWORD 'a-mesma-senha-do-.env';
CREATE DATABASE iativa OWNER iativa;
```

**Opção B, só o banco no Docker.** A porta é publicada somente em 127.0.0.1:

```bash
docker compose up -d db
```

Para rodar **tudo** em containers (banco e aplicação), veja a seção "Execução com Docker" abaixo.

### 4. Preparar a aplicação

```bash
python manage.py check
python manage.py migrate
python manage.py seed_synthetic
python manage.py createsuperuser
```

`seed_synthetic` gera `var/erp_sintetico.sqlite3`, uma base **fictícia** e determinística que imita
tabelas do Protheus (SA1010, SE1010, SE5010). Ela fica separada do banco da aplicação, e a aplicação
só a abre em modo somente leitura. Para gerar de novo: `python manage.py seed_synthetic --substituir`.

### 5. Executar

```bash
python manage.py runserver
```

Acesse <http://127.0.0.1:8000/> e entre com o usuário criado. Para criar outros usuários, use
<http://127.0.0.1:8000/admin/>.

## Setores, permissões e memória (Etapa 2)

Exemplos fictícios de setores e definições de parâmetros (a lista real de setores ainda está pendente):

```bash
python manage.py seed_exemplos
```

No admin (<http://127.0.0.1:8000/admin/>):

1. **Setores:** cadastre os setores.
2. **Perfis dos usuários:** ligue cada usuário a um setor. A função (colaborador, gestor, diretoria,
   TI) é só informativa e **não concede acesso**.
3. **Políticas de acesso:** conceda permissões por setor. As permissões são separadas: consultar,
   ler memória, alteração operacional, aprovar definições e administrar.
   - Consulta e alteração exigem um setor e a lista de filiais.
   - Setor vazio = nível empresa, usado para aprovar definições da empresa.
   - Visão consolidada (diretoria/TI) = uma política por setor autorizado. Não existe
     "todos os setores", e o superusuário do admin não tem acesso automático a dados.
4. **Definições de parâmetros:** o que é cada parâmetro (ex.: data de corte). Marque "contém dado
   pessoal" quando o valor identificar alguém: ele só vale na solicitação e é descartado em 30 dias.
5. **Valores de parâmetros:** somente leitura no admin. Propostas de setor e empresa são aprovadas ou
   rejeitadas pelas ações da listagem, por quem tem "aprovar definições" naquele nível. Quem propôs
   não aprova a própria proposta.

Precedência na resolução de um parâmetro (D12): regra obrigatória aprovada (setor ou empresa) >
valor da solicitação > preferência privada do usuário > setor > empresa. Conflitos não são
resolvidos automaticamente: a solicitação volta para "aguardando parâmetros".

### Retenção

```bash
python manage.py expurgar_retencao --simular
```

```bash
python manage.py expurgar_retencao
```

Remove valores com dado pessoal vencidos (30 dias), solicitações encerradas há mais de 90 dias e
conversas vazias. A auditoria só é removida quando `IATIVA_RETENCAO["auditoria_dias"]` for definido
(prazo ainda "a definir"). Imprime só contagens. Agende uma execução diária no servidor; com Docker:
`docker compose exec app python manage.py expurgar_retencao`.

## Conhecimento e metadados (Etapa 3)

Com `IATIVA_ERP_*` no `.env`, a fonte de metadados é o Protheus real (homologação, empresa
`IATIVA_ERP_SUFIXO = "010"`); sem ele, a base sintética. `IATIVA_METADADOS_FONTE` força uma das duas.
Só metadados: nenhuma linha de dados é lida nesta etapa.

- **Conhecimento:** documentos `.md` em `knowledge/` com cabeçalho (origem, versão, responsável,
  situação, aprovação). Só os aprovados e sem conteúdo proibido (CPF, CNPJ, credenciais) são
  recuperados, e só os trechos relevantes. Formato em [`knowledge/README.md`](knowledge/README.md).
- **Metadados:** dicionário no formato SX2/SX3/SIX/SX9 combinado com a estrutura física. Campo
  virtual, campo sem coluna física e coluna fora do dicionário não são consultáveis.
- **Acesso:** no admin, **Domínios por setor** habilita um domínio para um setor; o usuário precisa
  de "consultar" nesse setor. **Restrições de metadados** escondem tabelas ou campos, exceto para os
  setores permitidos.

Depois de atualizar, gere de novo a base sintética (agora inclui o dicionário) e os exemplos:

```bash
python manage.py seed_synthetic --substituir
```

```bash
python manage.py seed_exemplos
```

Conferência:

```bash
python manage.py validar_conhecimento
```

```bash
python manage.py descobrir_metadados USUARIO --tabelas SE1 SA1 --buscar "baixa parcial"
```

`descobrir_metadados` mostra só metadados e trechos aprovados, com as permissões do usuário.

### Conexão com o ERP real (homologação)

A imagem Docker inclui o Microsoft ODBC Driver 18 e `pyodbc`. Preencha `IATIVA_ERP_*` no `.env` do
servidor (veja `.env.example`) e confira:

```bash
docker compose exec app python manage.py verificar_erp
```

O comando lê só metadados: versão, permissões do próprio login, sufixos do dicionário (SX2) e
contagens de campos de SA1, SE1 e SE5. Para com erro se o login tiver qualquer permissão de escrita.

## Tela de chat (M8)

Layout de chat de IA com as cores e a logo da Ativa (protótipo aprovado em `docs/prototipo/chat`):

- Lateral: nova conversa, **chat mestre** (só para quem tem "consultar" em mais de um setor),
  **chat do setor** (lê quem tem "consultar" ou "ler memória" no setor; escreve quem é do setor),
  conversas individuais agrupadas por data, e nome e setor do usuário no rodapé.
- Conversa com várias mensagens; a ficha da solicitação mostra critérios, origem de cada um e os
  campos que faltam. Só a ficha mais recente da sua solicitação é editável.
- **O histórico fica salvo** (decisão M8): texto e fichas, nunca resultados de consultas. Mensagens
  com CPF/CNPJ são recusadas. Conversas paradas há mais de 90 dias (`conversa_dias`) são apagadas
  pelo `expurgar_retencao`. Mensagens não aparecem no admin.
- O nome exibido vem de "nome" e "sobrenome" do usuário no admin.

## Agente e esclarecimentos (Etapa 4)

O chat interpreta a pergunta, escolhe um objetivo da lista fechada (`iativa/agent/contratos.py`),
confere as tabelas na descoberta de metadados e pergunta só os parâmetros que faltam. Quando tudo
está definido, a solicitação fica **pronta** (a execução no ERP é a Etapa 5).

- `IATIVA_AGENTE_PROVEDOR=teste` (padrão): provedor falso, por palavras-chave. Exemplos que ele
  entende: "quais clientes estão inadimplentes?", "corte 31/12/2025, 5 dias, considerar saldo,
  filial 001001", "na verdade 10 dias", "títulos a vencer de 01/01/2026 a 31/01/2026", "cancela".
- `IATIVA_AGENTE_PROVEDOR=anthropic`: Claude (`claude-opus-5-5`) com saída estruturada. Exige
  `ANTHROPIC_API_KEY` e `IATIVA_AGENTE_ENVIO_LIBERADO=1` (só após a posição do DPO/LGPD).

Ao modelo vão só a mensagem, o catálogo de objetivos e parâmetros, os parâmetros já definidos e
trechos de conhecimento aprovados; nunca linhas do ERP. A mensagem não é armazenada.

Para usar o chat, o usuário precisa de perfil num setor habilitado em **Domínios por setor** e de
política com "consultar" e filiais. Depois de atualizar, rode `python manage.py seed_exemplos`
(cria a definição "periodo-analise").

## Consulta de leitura (Etapa 5)

Com a ficha **pronta**, o botão "Executar consulta" roda a consulta no ERP (homologação, somente
leitura). O backend monta o plano a partir do objetivo, compila o T-SQL com nomes do catálogo,
valida a árvore sintática (`sqlglot`) e executa com até 500 linhas e 30 segundos
(`IATIVA_CONSULTA`). O resultado aparece em tabela com total e premissas e **não é salvo**: ao
reabrir a conversa, use "Executar de novo". As premissas de negócio (tipos excluídos, saldo atual,
regra de atraso) estão na decisão T29 e precisam da aprovação do Financeiro.

## Execução com Docker (banco e aplicação)

Este é o ambiente de **desenvolvimento** em containers Linux (imagem Ubuntu 24.04), pensado para o
servidor Windows. Não é configuração de produção.

**Pré-requisito:** servidor Ubuntu (22.04 ou 24.04) com **Docker Engine** e o plugin
`docker compose`, instalados pelo repositório oficial da Docker
(https://docs.docker.com/engine/install/ubuntu/). Para usar o Docker sem `sudo`:
`sudo usermod -aG docker $USER`. Depois disso, saia da sessão e entre de novo.

**Acesso ao chat:** as portas ficam só em `127.0.0.1` do servidor. Para abrir o chat no navegador do
seu computador sem expor nada na rede, use um túnel SSH a partir do seu computador:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@ip-do-servidor
```

Com o túnel aberto, acesse <http://127.0.0.1:8000/> no seu computador. Liberar o acesso pela rede
interna é a Etapa 9 e exige autorização própria.

Na pasta do projeto, depois de preencher o `.env` (passo 2):

```bash
docker compose up -d --build
```

```bash
docker compose exec app python manage.py createsuperuser
```

Acesse <http://127.0.0.1:8000/> **no próprio servidor**. Outros comandos:

```bash
docker compose run --rm app pytest -p no:cacheprovider
```

```bash
docker compose logs -f app
```

```bash
docker compose down
```

Como o ambiente funciona:

- **Portas:** publicadas **somente em 127.0.0.1** (aplicação 8000, banco 5432). Um teste falha se o
  `compose.yaml` publicar porta fora de localhost.
- **Escuta em 0.0.0.0 dentro do container:** é necessária para a porta publicada funcionar. O
  `runserver` só aceita isso quando o processo está num container **e**
  `IATIVA_ALLOW_CONTAINER_BIND=1` (decisão T6). Fora de container, `0.0.0.0` continua recusado.
- **Primeira subida:** aplica as migrações e gera a base sintética, que fica no volume `iativa_var`.
  O banco da aplicação fica no volume `iativa_pgdata`.
- **Usuário sem privilégios:** a aplicação roda com `iativa` (uid 10001).
- **Atualizar o código:** rode `git pull` e depois `docker compose up -d --build`.

## Atualização automática a partir do GitHub

O script `scripts/atualizar.sh` roda **no servidor**, no terminal Ubuntu, fora dos containers, e
acompanha a branch `main`. Todo o trabalho vai direto para a `main` (M7), inclusive etapas ainda em
validação.

Quando a `main` tem commits novos, ele:

1. avança o código (somente fast-forward);
2. reconstrói a imagem;
3. **roda os testes antes de trocar o container**;
4. recria o container;
5. confere se o chat responde.

Se qualquer passo falhar, o script **volta para a versão anterior**.

Proteções do script:

- não roda se houver alterações locais em arquivos versionados nem se a pasta estiver em outra branch;
- impede duas execuções ao mesmo tempo;
- os logs ficam em `var/atualizacao/` e registram só horários, commits e o resultado de cada passo.

Rodar à mão:

```bash
scripts/atualizar.sh --checar
```

```bash
scripts/atualizar.sh
```

Agendar a cada 5 minutos (systemd timer):

```bash
sudo scripts/instalar_agendamento.sh
```

Para outro intervalo, use `sudo INTERVALO=15min scripts/instalar_agendamento.sh`. Para remover, use
`sudo scripts/instalar_agendamento.sh --remover`.

Requisitos e observações:

- **systemd ativo.** No WSL, adicione em `/etc/wsl.conf` as linhas `[boot]` e `systemd=true` e rode
  `wsl --shutdown` no Windows.
- **Docker sem `sudo`:** o usuário precisa estar no grupo `docker` (`sudo usermod -aG docker $USER`).
- **Repositório privado:** o servidor precisa de uma credencial **somente leitura**, de preferência
  uma *deploy key* do GitHub.
- **WSL ligado:** o WSL precisa estar em execução para os containers e o agendamento funcionarem.
  Confira se ele continua ativo com o servidor sem sessão aberta.
- **Primeira subida:** é manual (`docker compose up -d --build`). O script atualiza um ambiente que
  já existe.

## Testes

```bash
pytest
```

Os testes usam SQLite em memória e dados sintéticos. Não precisam de PostgreSQL, `.env` ou rede:
qualquer tentativa de conexão de rede faz o teste falhar. Os testes cobrem:

| Arquivo | Verifica |
|---|---|
| `tests/test_localhost.py` | `ALLOWED_HOSTS`, `DEBUG`, as verificações do sistema e a recusa de endereços externos pelo `runserver` |
| `tests/test_autenticacao.py` | login obrigatório, login e logout, CSRF, cookies `HttpOnly`/`SameSite=Strict` |
| `tests/test_cabecalhos_e_erros.py` | `no-store`, CSP, páginas 404 e 500 sem detalhes técnicos |
| `tests/test_chat.py` | mensagem transitória, que não vai para banco, log nem sessão |
| `tests/test_armazenamento_navegador.py` | ausência de armazenamento do navegador, htmx sem histórico, htmx local e íntegro |
| `tests/test_logs.py` | redação de SQL, credenciais e query strings nos logs |
| `tests/test_erp_sintetico.py` | determinismo, somente leitura, cenários do Protheus, separação dos bancos |
| `tests/test_segredos.py` | nenhum segredo versionado e chave obrigatória via `.env` |
| `tests/test_acessos.py` | permissões separadas, sem acesso universal, vigência, visão consolidada, filiais |
| `tests/test_memoria.py` | escopos, dados pessoais, CPF/CNPJ recusados, aprovação, correção, precedência, conflitos |
| `tests/test_solicitacoes.py` | máquina de estados, dono, cancelamento, visibilidade, liberação determinística |
| `tests/test_auditoria_retencao.py` | auditoria imutável e sem valores, expurgo por retenção |
| `tests/test_admin_etapa2.py` | páginas do admin e aprovação pelo admin respeitando políticas |
| `tests/test_conhecimento.py` | cabeçalho, aprovação, conteúdo proibido e busca de documentos |
| `tests/test_agente.py` | interpretação, contratos, esclarecimentos, falhas do modelo, provedor Claude simulado |
| `tests/test_metadados.py` | adaptadores, catálogo, descoberta progressiva e restrições de acesso |

## Estrutura

```text
config/                 configurações (base, local, test), URLs, WSGI/ASGI
iativa/core/            verificações do sistema, middleware de segurança, filtro de logs, runserver restrito
iativa/accounts/        setores, perfis, políticas de acesso e autorização (authz.py)
iativa/chat/            chat mínimo (mensagem transitória), contextos e estados da solicitação
iativa/memory/          definições e valores de parâmetros, revisões, resolução por precedência
iativa/audit/           auditoria sanitizada e expurgo por retenção
iativa/knowledge/       carga, validação e busca de documentos aprovados
iativa/metadata/        adaptadores (dicionário SX, físico SQLite, INFORMATION_SCHEMA), catálogo, descoberta
iativa/agent/           provedores de IA, contratos de parâmetros, esquema de saída e orquestrador
iativa/synthetic_erp/   esquema, dicionário, gerador e conexão somente leitura da base sintética
knowledge/              documentos de conhecimento (hoje só sintéticos)
templates/, static/     interface (templates Django + htmx 2.0.4 servido localmente)
tests/                  testes automatizados
docs/                   pedido inicial (requisitos)
```

A app de consulta (execução no ERP) será criada na Etapa 5.
