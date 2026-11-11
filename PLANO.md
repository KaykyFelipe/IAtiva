# PLANO — IAtiva

> **Situação: APROVADO em 2026-11-09** (Etapa 0, Kayky Felipe, via chat — ver `DECISOES.md`).
> Pendências que não bloqueiam a Etapa 1: escolha do provedor de IA (D5) e prazos de retenção marcados "a definir" (D11).
> Requisitos completos e regras obrigatórias: `docs/PEDIDO_INICIAL.md` (o pedido original prevalece em caso de divergência).

## 1. Entendimento

Chat corporativo em que funcionários autenticados fazem perguntas em linguagem natural sobre dados do TOTVS Protheus. O sistema interpreta, descobre metadados sob demanda, identifica parâmetros ausentes/ambíguos e pergunta antes de executar, gera SQL temporário validado pelo backend, executa **somente leitura** no SQL Server e apresenta o resultado com critérios, filtros e escopo — descartando SQL e resultado em seguida.

O "aprendizado" é memória estruturada de **definições e parâmetros** (solicitação, usuário, setor, empresa), com aprovação, validade e histórico. Nunca dados do ERP. O backend é a autoridade: a IA propõe; o backend valida, autoriza e executa.

## 2. Situação levantada

- Repositório `KaykyFelipe/IAtiva`: apenas commit inicial com `README.md`. Nada a reaproveitar.
- A sessão de desenvolvimento da Claude roda em contêiner na nuvem, efêmero, sem acesso à rede interna. Desenvolvimento e testes com dados sintéticos; conexão ao SQL Server real somente em máquina da empresa (que será o "localhost").

## 3. Arquitetura

```text
Navegador (127.0.0.1) — HTML renderizado no servidor, sem storage do navegador para dados
        ↓
Django (bind 127.0.0.1)
├── accounts    usuários, Department, AccessPolicy (identidade/filiais resolvidas só no backend)
├── chat        ConversationContext, RequestContext + máquina de estados
├── memory      ParameterDefinition, ParameterBinding, MemoryRevision, KnowledgeApproval
├── knowledge   carga e recuperação de trechos aprovados de knowledge/*.md
├── metadata    adaptadores: sintético | INFORMATION_SCHEMA | dicionário Protheus (fonte confirmada)
├── agent       interface LLMProvider + orquestrador (saídas JSON validadas por schema)
├── query       plano estruturado → compilador SQL → validador AST → executor somente leitura
└── audit       OperationAudit sanitizado
        ↓
Banco da aplicação (separado)        SQL Server Protheus (login somente leitura)
```

### Princípios técnicos

1. **Modelo não escreve SQL livre.** Produz um plano de consulta estruturado; o backend compila T-SQL a partir de entidades/campos/relacionamentos aprovados (identificadores de listas permitidas, valores como parâmetros vinculados). Segunda barreira: validação por AST com `sqlglot` (um único `SELECT`; sem DML/DDL, `EXEC`, `OPENROWSET`, servidores vinculados, `INTO`; só objetos permitidos; filtros obrigatórios `D_E_L_E_T_ = ' '`, filial no escopo, `TOP`). Terceira: login SQL Server com `SELECT` mínimo, timeout e limite de linhas.
2. **Acesso ao ERP** via `pyodbc` + ODBC Driver 18 em serviço isolado, fora do ORM do Django.
3. **Estados da solicitação:** `rascunho → aguardando_parametros → validando → pronta → executando → concluida | nao_validada | falhou | bloqueada | cancelada`. Liberação por checagem determinística do backend, não pela confiança do modelo.
4. **Resultados temporários:** apenas em memória durante a requisição; `Cache-Control: no-store`; sem cache de resultados; reagrupar/paginar/reabrir = nova execução, informada ao usuário; filtro de redação nos logs; página técnica de erro desativada; sem APM/captura externa de exceções na v1.
5. **Provedor de IA** atrás da interface `LLMProvider`: `FakeProvider` (testes), API sob contrato comercial e/ou modelo local. Assinatura de chat/CLI não é usada como backend. Por padrão só metadados, definições e parâmetros vão ao modelo — não linhas de resultado.
6. **Precedência de memória:** regras obrigatórias aprovadas (empresa/setor) não são sobrepostas; para preferências: valor explícito da solicitação > privada do usuário válida > setor aprovado > empresa aprovada; conflito no mesmo nível ou contra regra obrigatória → perguntar. O resultado mostra a origem de cada critério.

### Domínio inicial proposto: Financeiro — Contas a Receber

Hipóteses a confirmar com a equipe do ERP: SE1 (títulos), SA1 (clientes, chave `A1_COD + A1_LOJA`), SE5 (baixas/movimentação); `E1_FILIAL` e compartilhamento de SA1; `E1_SALDO`/`E1_BAIXA`/`E1_VENCREA` para baixas parciais; tipos RA/NCC/abatimentos; cancelamentos/estornos na SE5; `D_E_L_E_T_`.

## 4. Persistência e retenção (proposta)

| Item | Persistir? | Acesso | Retenção |
|---|---|---|---|
| Objetivo estruturado da solicitação | Sim | Dono; setor se compartilhado | 90 dias após encerrar |
| Definição de parâmetro | Sim, sem dados pessoais | Conforme escopo | Até revogação; revisão anual |
| Valor sem dado pessoal (data, período, filial) | Sim, ligado à solicitação | Dono | Igual à solicitação |
| Valor com dado pessoal/cadastral | Só no escopo da solicitação; nunca vira padrão | Dono | 30 dias; CPF/CNPJ não persistidos |
| Esclarecimentos (curtos, estruturados) | Sim, sem trechos de resultado | Conforme escopo | Igual ao item relacionado |
| Revisões/aprovações/correções | Sim | Aprovadores, auditoria | A definir |
| Auditoria (quem, quando, entidade, estado, duração, nº de linhas) | Sim, sem SQL e sem valores | TI/auditoria | A definir |
| SQL, linhas, transcrições completas, credenciais | **Não** | — | — |

## 5. Etapas

Conforme `docs/PEDIDO_INICIAL.md` §17: 0 Levantamento → 1 Fundação localhost → 2 Autorização e memória → 3 Conhecimento e metadados → 4 Agente e esclarecimentos → 5 Consulta de leitura → 6 Memórias setoriais e chat consolidado → 7 Piloto local → 8 Operações de alteração → 9 Preparação para acesso externo. Cada etapa exige validação explícita antes da próxima.

## 6. Etapa 1 — escopo e critérios de aceite

**Escopo:** projeto Django 5.2 LTS com a estrutura de apps (sem lógica de agente); configuração por variáveis de ambiente (`.env.example`; `.env` fora do Git); login/logout com usuários locais; página de chat mínima (registra a mensagem como "recebida", sem interpretação); banco da aplicação conforme D2; base sintética do ERP separada, gerada por comando `seed_synthetic` determinístico; README de instalação, execução e testes.

**Critérios de aceite:**

- [ ] `runserver` só em `127.0.0.1`; check do sistema falha se `ALLOWED_HOSTS` ou bind fugirem de localhost.
- [ ] Todas as páginas exceto login exigem autenticação; CSRF; cookies `HttpOnly`/`SameSite`.
- [ ] Banco da aplicação e ERP sintético são distintos; conexão ao ERP sintético é somente leitura, com teste de recusa de escrita.
- [ ] Dados sintéticos fictícios e reprodutíveis, com exclusão lógica, várias filiais, chave composta e campo personalizado fictício.
- [ ] `Cache-Control: no-store`; nenhum uso de storage do navegador nos templates (com teste).
- [ ] Filtro de redação de logs configurado; página técnica de erro desativada.
- [ ] Nenhum segredo no repositório.
- [ ] `pytest` passa sem dependência de rede.
- [ ] README reproduz tudo do zero numa máquina limpa.

## 7. Etapa 6 — memórias setoriais e chat consolidado (proposta, aguardando aprovação)

**Já existe:** valores de parâmetro por setor/empresa como proposta com aprovação (T9/T10), precedência (D12), memória do agente (`NotaAgente`, usuário/empresa, M10), consultas aprendidas por setor (M9) e o chat mestre (M8), que hoje consulta só como o setor do próprio usuário.

**Escopo:**

1. **Memória do setor.** A memória do agente ganha o escopo "setor". Notas de setor e empresa passam a entrar como **proposta**; só valem depois de aprovadas por quem tem "aprovar definições" naquele nível. A autoaprovação segue a mesma chave da M9. Notas de usuário continuam valendo na hora.
2. **Tela "Memória"** no chat, por setor, só para quem é do setor. Lista o que vale (valores padrão, regras obrigatórias, notas e consultas aprendidas), com origem, quem aprovou e versão.
3. **Fila de aprovação única** para valores, notas e consultas aprendidas. Pelo chat, o agente aceita "aprove a nota X" e "rejeite a proposta Y", sempre com confirmação. Autor não aprova a própria proposta (exceto com a autoaprovação ligada).
4. **Resolução de conflitos:**
   - Proposta que contradiz algo já aprovado no mesmo nível (mesmo parâmetro, outro valor) aparece lado a lado ao aprovador. Aprovar substitui com nova versão; o histórico fica.
   - Proposta de setor que contradiz regra obrigatória da empresa é recusada na hora (D12).
   - Notas que se contradizem (mesmo termo, definições diferentes) são marcadas para revisão. Até a decisão, vale a já aprovada.
5. **Visão consolidada (chat mestre):**
   - A consulta roda uma vez por setor autorizado, cada uma com a memória e as filiais do próprio setor.
   - O resultado mostra um bloco por setor. Os números só são somados quando as premissas forem iguais; quando diferirem, o sistema mostra a diferença em vez de somar.
   - Só setores em que o usuário tem "consultar" participam.

**Fora do escopo:** operações de alteração (Etapa 8), acesso externo (Etapa 9) e importação de usuários do AD.

**Critérios de aceite:**

- [ ] Nota de setor/empresa não vale antes de aprovada; a de usuário vale na hora.
- [ ] Quem não tem "aprovar definições" no nível não aprova; o autor não aprova a própria proposta (sem autoaprovação).
- [ ] Conflito com valor aprovado aparece ao aprovador; aprovar cria versão nova e mantém o histórico.
- [ ] Conflito com regra obrigatória da empresa é recusado.
- [ ] A tela "Memória" mostra só setores do usuário, sem resultados do ERP.
- [ ] No chat mestre, cada setor usa a própria memória e filiais; não soma números com premissas diferentes.
- [ ] Usuário sem "consultar" num setor não vê o bloco desse setor.
- [ ] Todas as aprovações, rejeições e substituições ficam na auditoria (só códigos).
- [ ] `pytest` passa.
