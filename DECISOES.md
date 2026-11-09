# DECISÕES — IAtiva

Registro de decisões tomadas e mudanças de escopo. Uma decisão só é marcada **Aprovada** com o nome de quem aprovou e a data. Afirmações feitas em chat não viram decisão sem esse registro.

Formato: `ID | data | decisão | situação | aprovado por | observações`

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

## Pendentes de validação (propostas na Etapa 0)

| ID | Data | Decisão proposta | Situação | Aprovado por |
|---|---|---|---|---|
| D1 | 2026-11-09 | Frontend: templates Django + HTMX (alternativa: React) | Pendente | — |
| D2 | 2026-11-09 | Banco da aplicação: PostgreSQL 16; SQLite só em testes automatizados | Pendente | — |
| D3 | 2026-11-09 | Modelo gera plano de consulta estruturado; backend compila o SQL (sem SQL livre do modelo) | Pendente | — |
| D4 | 2026-11-09 | ERP sintético em esquema estilo Protheus; SQLite somente leitura para testes rápidos + SQL Server Developer (Docker) em máquina da empresa para testes de T-SQL | Pendente | — |
| D5 | 2026-11-09 | Provedor de IA: API sob contrato comercial com termos de retenção verificados e/ou modelo local | Pendente | — |
| D6 | 2026-11-09 | Não enviar linhas de resultado ao modelo na v1 | Pendente | — |
| D7 | 2026-11-09 | Autenticação inicial com usuários locais do Django; AD/LDAP/Entra ID depois | Pendente | — |
| D8 | 2026-11-09 | Commit e push: um commit por etapa após aprovação, na branch `claude/new-session-w616rq`, sem PR salvo pedido | **Aprovada** (2026-11-09) | Kayky Felipe (chat) |
| D9 | 2026-11-09 | Testes com `pytest` + `pytest-django`, só dados sintéticos | Pendente | — |
| D10 | 2026-11-09 | Domínio inicial: Financeiro — Contas a Receber | Pendente | — |
| D11 | 2026-11-09 | Tabela de persistência e retenção (`PLANO.md` §4) | Pendente | — |
| D12 | 2026-11-09 | Política de precedência de memória (`PLANO.md` §3, princípio 6) | Pendente | — |

## Organização do trabalho

| ID | Data | Decisão | Situação | Observações |
|---|---|---|---|---|
| O1 | 2026-11-09 | Contexto compartilhado entre participantes por arquivos versionados: `PLANO.md`, `DECISOES.md`, `STATUS.md` (e `docs/PEDIDO_INICIAL.md`). Cada participante usa sua conta e pede à Claude que leia esses arquivos antes de trabalhar. | Adotada | Primeiro commit/push desta documentação autorizado em 2026-11-09 por Kayky Felipe (chat). |

## Mudanças de escopo

Nenhuma até o momento.
