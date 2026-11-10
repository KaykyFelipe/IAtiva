# DECISÕES — IAtiva

Registro de decisões tomadas e mudanças de escopo. Uma decisão só é marcada **Aprovada** com o nome de quem aprovou e a data. Afirmações feitas em chat não viram decisão sem esse registro.

Formato: `ID | data | decisão | situação | aprovado por | observações`

## Etapas aprovadas

| Etapa | Data | Aprovado por |
|---|---|---|
| 0 — Levantamento e proposta | 2026-11-09 | Kayky Felipe (chat) |
| 1 — Fundação em localhost (inclui T1–T7, M1) | 2026-11-09 | Kayky Felipe (chat: "eu aprovo, pode mandar") — aprovada antes da execução no servidor Windows; a validação do Docker no servidor segue como pendência |
| 2 — Plano de autorização e memória (início autorizado; entrega ainda não validada) | 2026-11-10 | Diego (chat: "Aprovo a etapa 2, pode fazer") — com setores de exemplo enquanto a lista real não chega |

## Definidas pelo pedido inicial (`docs/PEDIDO_INICIAL.md`)

| ID | Decisão | Situação |
|---|---|---|
| R1 | Backend e APIs em Python + Django | Definida |
| R2 | Banco da aplicação separado do banco do ERP | Definida |
| R3 | Não persistir SQL gerado nem resultados (banco, logs, filas, cache, temporários, APM, exceções, analytics, navegador) | Definida |
| R4 | Primeira versão somente em localhost (127.0.0.1), sem portas externas, túneis ou publicação | Definida |
| R5 | Começar com dados sintéticos; ERP real só após aprovação, com acesso somente leitura | Definida |
| R6 | Trabalho por etapas, com validação explícita antes de avançar | Definida |
| R7 | Sem deploy, exposição externa, alteração no ERP ou operação destrutiva sem autorização específica | Definida |
| R8 | Interface substituível para o provedor de IA | Definida |

## Decisões da Etapa 0

Aprovadas em 2026-11-09 por Kayky Felipe (chat), com as opções recomendadas na proposta.

| ID | Data | Decisão | Situação | Aprovado por |
|---|---|---|---|---|
| D1 | 2026-11-09 | Frontend: templates Django + HTMX | **Aprovada** | Kayky Felipe (chat) |
| D2 | 2026-11-09 | Banco da aplicação: PostgreSQL 16; SQLite só em testes automatizados | **Aprovada** | Kayky Felipe (chat) |
| D3 | 2026-11-09 | Modelo gera plano de consulta estruturado; backend compila o SQL (sem SQL livre do modelo) | **Aprovada** | Kayky Felipe (chat) |
| D4 | 2026-11-09 | ERP sintético em esquema estilo Protheus; SQLite somente leitura para testes rápidos + SQL Server Developer (Docker) em máquina da empresa para testes de T-SQL | **Aprovada** | Kayky Felipe (chat) |
| D5 | 2026-11-09 | Provedor de IA: API sob contrato comercial com termos de retenção verificados e/ou modelo local | **Aprovada como diretriz** — escolha do provedor e verificação contratual pendentes (necessárias antes da Etapa 4) | Kayky Felipe (chat) |
| D6 | 2026-11-09 | Não enviar linhas de resultado ao modelo na v1 | **Aprovada** | Kayky Felipe (chat) |
| D7 | 2026-11-09 | Autenticação inicial com usuários locais do Django; AD/LDAP/Entra ID depois | **Aprovada** | Kayky Felipe (chat) |
| D8 | 2026-11-09 | Commit e push: um commit por etapa após aprovação, na branch `claude/new-session-w616rq`, sem PR salvo pedido | **Substituída pela M7** em 2026-11-10 | Kayky Felipe (chat) |
| D9 | 2026-11-09 | Testes com `pytest` + `pytest-django`, só dados sintéticos | **Aprovada** | Kayky Felipe (chat) |
| D10 | 2026-11-09 | Domínio inicial: Financeiro — Contas a Receber | **Aprovada** | Kayky Felipe (chat) |
| D11 | 2026-11-09 | Tabela de persistência e retenção (`PLANO.md` §4) | **Aprovada** — prazos marcados "a definir" (auditoria, revisões) continuam pendentes | Kayky Felipe (chat) |
| D12 | 2026-11-09 | Política de precedência de memória (`PLANO.md` §3, princípio 6) | **Aprovada** | Kayky Felipe (chat) |

## Decisões técnicas de implementação (Etapa 1 — sujeitas à validação da etapa)

| ID | Data | Decisão | Situação |
|---|---|---|---|
| T1 | 2026-11-09 | `DEBUG` fixo em `False` (não lido do ambiente). O `runserver` restrito serve os estáticos localmente (equivalente a `--insecure`). | Aprovada com a Etapa 1 |
| T2 | 2026-11-09 | `runserver` substituído: aceita apenas `127.0.0.1`/`localhost`; recusa `0.0.0.0`, IPs da rede e IPv6. Verificações do sistema `iativa.E001`–`E005` impedem inicialização fora das regras. | Aprovada com a Etapa 1 |
| T3 | 2026-11-09 | htmx 2.0.4 servido localmente (sem CDN), com histórico desativado para não gravar páginas no navegador; integridade conferida por SHA-256 em teste. | Aprovada com a Etapa 1 |
| T4 | 2026-11-09 | CSP restritiva (`script-src 'self'`, sem inline), `Cache-Control: no-store` em todas as respostas, cookies `HttpOnly` + `SameSite=Strict`, cache da aplicação desativado (`DummyCache`). | Aprovada com a Etapa 1 |
| T5 | 2026-11-09 | Base sintética do ERP marcada com tabela `IATIVA_SINTETICO`; a conexão de leitura recusa arquivos sem essa marca. | Aprovada com a Etapa 1 |
| T6 | 2026-11-09 | Exceção de bind em container: `runserver` aceita `0.0.0.0` somente dentro de container Docker (`/.dockerenv`) **e** com `IATIVA_ALLOW_CONTAINER_BIND=1`; a restrição a localhost passa a ser garantida pela publicação da porta em `127.0.0.1` do host (verificada por teste). IPs da rede continuam recusados. | Aprovada com a Etapa 1 |
| T7 | 2026-11-09 | Ambiente de desenvolvimento em containers Linux (Ubuntu 24.04 + PostgreSQL 16) via `compose.yaml`, para o servidor Windows (Docker Engine em WSL2 ou VM Ubuntu). Não é configuração de produção. | Aprovada com a Etapa 1 |

## Decisões técnicas de implementação (Etapa 2 — sujeitas à validação da etapa)

| ID | Data | Decisão | Situação |
|---|---|---|---|
| T8 | 2026-11-10 | Nenhuma permissão universal ou automática: tudo vem de `AccessPolicy` explícita por setor (setor vazio = nível empresa, só memória/aprovação/administração). Função (diretoria/TI) e superusuário do admin não concedem acesso a dados. Visão consolidada = soma de políticas por setor. | Em validação |
| T9 | 2026-11-10 | Valores de parâmetros num único modelo (`ParameterBinding`) com escopo solicitação/usuário/setor/empresa e restrição de coerência no banco; regra obrigatória só em setor/empresa. | Em validação |
| T10 | 2026-11-10 | Memória privada (solicitação, usuário) aprovada na criação; setor e empresa entram como proposta e exigem `pode_aprovar_conhecimento` no nível, sem autoaprovação. Correção compartilhada mantém o valor anterior até a nova versão ser aprovada. | Em validação |
| T11 | 2026-11-10 | Dado pessoal: definição marcada só aceita escopo de solicitação, com `expira_em` de 30 dias. Valores que pareçam CPF/CNPJ são recusados em qualquer escopo. | Em validação |
| T12 | 2026-11-10 | Objetivo da solicitação guardado como código (`SlugField`), nunca o texto da pergunta. Concluída e cancelada são finais; "não validada" só volta para "aguardando parâmetros". | Em validação |
| T13 | 2026-11-10 | Auditoria imutável com campos de código validados (sem texto livre). Expurgo por comando (`expurgar_retencao`); prazos "a definir" ficam em `None` e não removem nada. | Em validação |

## Organização do trabalho

| ID | Data | Decisão | Situação | Observações |
|---|---|---|---|---|
| O1 | 2026-11-09 | Contexto compartilhado entre participantes por arquivos versionados: `PLANO.md`, `DECISOES.md`, `STATUS.md` (e `docs/PEDIDO_INICIAL.md`). Cada participante usa sua conta e pede à Claude que leia esses arquivos antes de trabalhar. | Adotada | Primeiro commit/push desta documentação autorizado em 2026-11-09 por Kayky Felipe (chat). |

## Mudanças de escopo

| ID | Data | Mudança | Situação |
|---|---|---|---|
| M7 | 2026-11-10 | Substitui a D8: commit e push sempre direto na `main`, sem pull request salvo pedido. Como a `main` dispara a atualização automática do servidor (M4), etapas ainda em validação também chegam ao servidor; `pytest` roda antes de cada push e o script roda os testes de novo antes de trocar o container. A Etapa 2 foi levada à `main` nesta data, ainda em validação. | Autorizado por Diego (chat: "eu quero que manda sempre para a main") |
| M6 | 2026-11-10 | Exceção à D8: commit e push da Etapa 2 **antes** da validação da etapa, na branch `claude/new-session-w616rq`. A `main` só recebe a etapa depois de aprovada. | Autorizado por Diego (chat: "faça o commit e o push") |
| M5 | 2026-11-09 | Servidor de desenvolvimento passa de Windows (WSL2/VM) para Ubuntu nativo com Docker Engine. `compose.yaml` mantido sem alteração (portas só em 127.0.0.1); acesso a partir de outros computadores por túnel SSH, sem publicar portas. | **Aprovada** em 2026-11-09 por Kayky Felipe (chat: "aprovo") |
| M4 | 2026-11-09 | Rotina de atualização automática do ambiente de desenvolvimento no servidor a partir da `main` (`scripts/atualizar.sh` + systemd timer): fast-forward, build, testes antes de trocar o container, verificação final e reversão automática em falha. Roda no host; nenhum container recebe acesso ao socket do Docker. | **Aprovada** em 2026-11-09 por Kayky Felipe (chat: "aprovo") |
| M3 | 2026-11-09 | Pull request da branch `claude/new-session-w616rq` para `main` (KaykyFelipe/IAtiva#1) e merge na `main` após a aprovação da Etapa 1. | Autorizado por Kayky Felipe (chat: "faz o merge na main") |
| M2 | 2026-11-09 | Exceção à D8: commit e push da Etapa 1 **antes** da aprovação da etapa, para que o servidor possa clonar o código e testar o ambiente em containers. (A Etapa 1 foi aprovada depois, no mesmo dia.) | Autorizada por Kayky Felipe (chat: "faz o commit") |
| M1 | 2026-11-09 | Etapa 1 cria apenas as apps necessárias agora (`core`, `chat`, `synthetic_erp`). As apps `accounts`, `memory`, `knowledge`, `metadata`, `agent`, `query` e `audit` serão criadas nas etapas correspondentes, em vez de esqueletos vazios. | Aprovada com a Etapa 1 |
