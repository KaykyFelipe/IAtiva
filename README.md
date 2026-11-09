# IAtiva

Chat corporativo para consultas de leitura ao TOTVS Protheus em linguagem natural.

> **Fase atual: Etapa 1, fundação em localhost.** Ainda não existe interpretação de perguntas
> nem consulta ao ERP. O chat apenas confirma o recebimento da mensagem, sem armazená-la.
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
git checkout claude/new-session-w616rq
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Windows (PowerShell):

```powershell
git clone https://github.com/KaykyFelipe/IAtiva.git
cd IAtiva
git checkout claude/new-session-w616rq
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
<http://127.0.0.1:8000/admin/>. Usuários, setores e permissões completos chegam na Etapa 2.

## Execução com Docker (banco e aplicação)

Este é o ambiente de **desenvolvimento** em containers Linux (imagem Ubuntu 24.04), pensado para o
servidor Windows. Não é configuração de produção.

**Pré-requisito:** um Docker Engine que rode containers Linux no servidor. O Docker Desktop não tem
suporte oficial no Windows Server, então há dois caminhos:

- **Docker Engine dentro de uma distribuição Ubuntu no WSL2** (Windows Server 2022 ou mais recente).
  O WSL encaminha o `127.0.0.1` do Ubuntu para o `127.0.0.1` do Windows, e o chat abre no navegador
  do próprio servidor.
- **Máquina virtual Ubuntu (Hyper-V) com Docker Engine.** Aqui o `127.0.0.1` é o da VM. Para abrir o
  chat no Windows, use um redirecionamento SSH
  (`ssh -L 8000:127.0.0.1:8000 usuario@vm`), sem publicar a porta na rede.

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

## Estrutura

```text
config/                 configurações (base, local, test), URLs, WSGI/ASGI
iativa/core/            verificações do sistema, middleware de segurança, filtro de logs, runserver restrito
iativa/chat/            chat mínimo (mensagem transitória)
iativa/synthetic_erp/   esquema, gerador e conexão somente leitura da base sintética
templates/, static/     interface (templates Django + htmx 2.0.4 servido localmente)
tests/                  testes automatizados
docs/                   pedido inicial (requisitos)
```

As demais apps da arquitetura (memória, conhecimento, metadados, agente, consulta e auditoria) serão
criadas nas etapas correspondentes.
