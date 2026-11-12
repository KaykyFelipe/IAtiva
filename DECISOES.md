# DECISÕES — IAtiva

Registro de decisões tomadas e mudanças de escopo. Uma decisão só é marcada **Aprovada** com o nome de quem aprovou e a data. Afirmações feitas em chat não viram decisão sem esse registro.

Formato: `ID | data | decisão | situação | aprovado por | observações`

## Etapas aprovadas

| Etapa | Data | Aprovado por |
|---|---|---|
| 0 — Levantamento e proposta | 2026-11-09 | Kayky Felipe (chat) |
| 1 — Fundação em localhost (inclui T1–T7, M1) | 2026-11-09 | Kayky Felipe (chat: "eu aprovo, pode mandar") — aprovada antes da execução no servidor Windows; a validação do Docker no servidor segue como pendência |
| 2 — Plano de autorização e memória (início autorizado) | 2026-11-10 | Diego (chat: "Aprovo a etapa 2, pode fazer") — com setores de exemplo enquanto a lista real não chega |
| 2 — Autorização e memória de parâmetros (entrega validada, inclui T8–T13) | 2026-11-10 | Diego (chat: "Aprovo a Etapa 2") — setores reais e agendamento do expurgo seguem como pendências |
| 3 — Plano de conhecimento e metadados (início autorizado, só fontes sintéticas) | 2026-11-10 | Diego (chat: "Aprovo a Etapa 3") — Protheus real só após liberação explícita e informações 4–8 |
| 3 — Conhecimento e metadados (entrega validada, inclui T14–T19; fonte real de homologação) | 2026-11-10 | Diego (chat: "Aprovo a Etapa 3") |
| 4 — Agente e esclarecimentos (entrega validada, inclui T20–T24 e M8) | 2026-11-10 | Diego (chat: "aprovo a etapa 4") |
| 6 — Memórias setoriais e chat consolidado (entrega validada, inclui M11–M18) | 2026-11-12 | Diego (chat: "Aprovo a etapa 6") — base do ERP sem movimento após 08/09/2026 confirmada como esperada; T29 segue pendente |
| 6 — Plano de memórias setoriais e chat consolidado (`PLANO.md` §7, implementação autorizada) | 2026-11-11 | Diego (chat: "Aprovo, pode fazer") |
| 5 — Consulta de leitura (entrega validada, inclui T25–T28, T30, M9 e M10) | 2026-11-11 | Diego (chat: "aprovo a etapa 5") — conferência dos números com o Financeiro e premissas T29 seguem como pendência |
| 5 — Consulta de leitura (início autorizado; homologação, somente leitura) | 2026-11-10 | Diego (chat: "Vamos começar a Etapa 5") |
| 4 — Plano de agente e esclarecimentos (início autorizado; provedor falso até a chave da API da Anthropic e a posição do DPO) | 2026-11-10 | Diego (chat: "Aprovo a Etapa 4") |
| 3 — Liberação para conectar na base de **homologação** com o login somente leitura (só metadados) | 2026-11-10 | Diego (chat: "pode tentar conectar") — dados de conexão só no `.env` do servidor |

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
| D5 | 2026-11-09 | Provedor de IA: API sob contrato comercial com termos de retenção verificados e/ou modelo local | **Aprovada como diretriz** — provedor escolhido em 2026-11-10: API da Anthropic (Claude), ainda sem contrato/chave; verificação contratual e posição do DPO pendentes | Kayky Felipe (chat) |
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

## Decisões técnicas de implementação (Etapa 2)

| ID | Data | Decisão | Situação |
|---|---|---|---|
| T8 | 2026-11-10 | Nenhuma permissão universal ou automática: tudo vem de `AccessPolicy` explícita por setor (setor vazio = nível empresa, só memória/aprovação/administração). Função (diretoria/TI) e superusuário do admin não concedem acesso a dados. Visão consolidada = soma de políticas por setor. | Aprovada com a Etapa 2 |
| T9 | 2026-11-10 | Valores de parâmetros num único modelo (`ParameterBinding`) com escopo solicitação/usuário/setor/empresa e restrição de coerência no banco; regra obrigatória só em setor/empresa. | Aprovada com a Etapa 2 |
| T10 | 2026-11-10 | Memória privada (solicitação, usuário) aprovada na criação; setor e empresa entram como proposta e exigem `pode_aprovar_conhecimento` no nível, sem autoaprovação. Correção compartilhada mantém o valor anterior até a nova versão ser aprovada. | Aprovada com a Etapa 2 |
| T11 | 2026-11-10 | Dado pessoal: definição marcada só aceita escopo de solicitação, com `expira_em` de 30 dias. Valores que pareçam CPF/CNPJ são recusados em qualquer escopo. | Aprovada com a Etapa 2 |
| T12 | 2026-11-10 | Objetivo da solicitação guardado como código (`SlugField`), nunca o texto da pergunta. Concluída e cancelada são finais; "não validada" só volta para "aguardando parâmetros". | Aprovada com a Etapa 2 |
| T13 | 2026-11-10 | Auditoria imutável com campos de código validados (sem texto livre). Expurgo por comando (`expurgar_retencao`); prazos "a definir" ficam em `None` e não removem nada. | Aprovada com a Etapa 2 |

## Decisões técnicas de implementação (Etapa 3)

| ID | Data | Decisão | Situação |
|----|------|---------|----------|
| T14 | 2026-11-10 | Conhecimento em arquivos `knowledge/**/*.md` versionados no Git, com cabeçalho chave: valor; aprovação registrada no cabeçalho (aprovado_por, aprovado_em). Documento com padrão de CPF, CNPJ, credencial ou string de conexão é recusado inteiro. | Aprovada com a Etapa 3 |
| T15 | 2026-11-10 | Busca local por pontuação BM25 (sem IA, sem rede, sem dependência nova), por seção, no máximo 5 trechos de até 1.200 caracteres. | Aprovada com a Etapa 3 |
| T16 | 2026-11-10 | Dicionário lido de tabelas SX no banco (SX2, SX3, SIX, SX9 com sufixo da empresa) por consultas fixas e parametrizadas; estrutura física por PRAGMA (sintético) ou INFORMATION_SCHEMA (SQL Server). Forma de acesso real ainda a confirmar (informação 4). | Aprovada com a Etapa 3 |
| T17 | 2026-11-10 | Acesso à descoberta: domínio habilitado por setor (`DominioSetor`) + "consultar" no setor; `RestricaoMetadado` esconde tabela ou campo exceto para setores listados. Domínio tem lista fechada de tabelas. | Aprovada com a Etapa 3 |
| T19 | 2026-11-10 | Fonte de metadados passa a ser o Protheus real (homologação, empresa 010) quando `IATIVA_ERP_*` está configurado; a base sintética fica só para testes automatizados e máquinas sem ERP. Cada abertura do catálogo real confere que o login segue somente leitura e bloqueia se não estiver. Somente metadados; leitura de dados continua na Etapa 5. Pedido por Diego (chat: "Pare de usar fonte sintetica e comece a usar o banco de dados real"). | Aprovada com a Etapa 3 |
| T18 | 2026-11-10 | Conhecimento com `origem: sintetico` só é usado com a fonte sintética, e o real só com a fonte real. Base sintética passa à versão 2 (com dicionário SX); é preciso regerar com `seed_synthetic --substituir`. | Aprovada com a Etapa 3 |

## Decisões técnicas de implementação (Etapa 4)

| ID | Data | Decisão | Situação |
|----|------|---------|----------|
| T20 | 2026-11-10 | Interface `LLMProvider` com `FakeProvider` (regras por palavras-chave, sem rede) e `AnthropicProvider` (SDK `anthropic`, modelo `claude-opus-5-5`, esforço `low`, saída estruturada por JSON Schema, fallback de recusa no servidor `fallbacks: "default"`). Provedor padrão: teste. | Aprovada com a Etapa 4 |
| T21 | 2026-11-10 | O modelo só interpreta: devolve intenção, objetivo (lista fechada), parâmetros como texto e uma pergunta de ambiguidade. Saída validada estritamente (campos, enums, tamanhos); inválida = falha do modelo, com uma nova tentativa. | Aprovada com a Etapa 4 |
| T22 | 2026-11-10 | Contratos de parâmetros por objetivo (`agent/contratos.py`): titulos-em-atraso, titulos-a-vencer, posicao-cliente. Parâmetros obrigatórios vêm do contrato; perguntas geradas pelo backend a partir das definições; filial só entre as autorizadas. | Aprovada com a Etapa 4 |
| T23 | 2026-11-10 | Ao modelo vão só: mensagem, data de hoje, catálogo de objetivos/parâmetros, parâmetros já definidos (dado pessoal como "[informado]") e trechos de conhecimento aprovados. Nunca linhas do ERP. A mensagem não é persistida nem ecoada. | Aprovada com a Etapa 4 |
| T24 | 2026-11-10 | `AnthropicProvider` só funciona com `IATIVA_AGENTE_ENVIO_LIBERADO=1`, que depende da posição do DPO/LGPD (D5/D6). Mudança de assunto cancela a solicitação anterior em preparação. | Aprovada com a Etapa 4 |

## Decisões técnicas de implementação (Etapa 5)

| ID | Data | Decisão | Situação |
|----|------|---------|----------|
| T25 | 2026-11-10 | Plano de consulta montado pelo backend a partir do objetivo e dos parâmetros resolvidos (o modelo não participa). Compilador usa só nomes do catálogo (tabela física do SX2, campos consultáveis), valores sempre como parâmetros, filial calculada pelo compartilhamento (xFilial). | Aprovada com a Etapa 5 |
| T26 | 2026-11-10 | Validação por AST com `sqlglot` (T-SQL): um SELECT, sem INTO/UNION/subconsulta/comandos/OPENROWSET, só tabelas permitidas sem banco/servidor no nome, D_E_L_E_T_ e filial em cada tabela, TOP obrigatório. | Aprovada com a Etapa 5 |
| T27 | 2026-11-10 | Execução na mesma conexão somente leitura do catálogo (login conferido a cada abertura), até 500 linhas e 30 s. Antes de executar, reconfere permissões, filiais, parâmetros e metadados. Chave repetida no resultado → "não validada". | Aprovada com a Etapa 5 |
| T28 | 2026-11-10 | Resultado só em memória na resposta; o histórico guarda "consulta executada, N linhas"; reabrir = "Executar de novo" (nova solicitação com os mesmos valores). Auditoria: duração e número de linhas. | Aprovada com a Etapa 5 |
| T30 | 2026-11-10 | No SQL Server os valores vão como literais (não parâmetros): na homologação a mesma consulta parametrizada estourava o tempo (HYT00) e com literais rodava em 0,0 s. Só valores do plano, restritos a `[0-9A-Za-z %-]` (até 40 caracteres, sem aspas); o SQL final é revalidado pela árvore sintática antes de executar. Na base sintética continuam parâmetros. | Aprovada com a Etapa 5 |
| T29 | 2026-11-10 | **Premissas a confirmar com o Financeiro (informação 8):** excluídos tipos RA, NCC, PR e abatimentos (tipo terminado em "-"); usa o saldo atual (pagamentos após a data de corte já descontados); atraso = vencimento real antes de (corte − tolerância). Mostradas ao usuário em cada resultado. | Pendente — aguarda o Financeiro (não bloqueia a Etapa 6) |

## Decisões técnicas de implementação (Etapa 6 — sujeitas à validação da etapa)

| ID | Data | Decisão | Situação |
|----|------|---------|----------|
| T31 | 2026-11-11 | `NotaAgente` ganha escopo "setor", situação (proposta/aprovada/rejeitada/substituída), aprovador e `substitui`. Nota de usuário vale na hora; de setor/empresa só depois de aprovada por quem tem "aprovar definições" no nível. Notas já existentes ficam aprovadas. Autoaprovação segue a chave da M9 (`IATIVA_APRENDIZADO_AUTOAPROVACAO`), também para valores padrão. | Em validação |
| T32 | 2026-11-11 | Conflitos resolvidos na entrada: proposta de setor que contraria regra obrigatória aprovada da empresa é recusada; mesmo valor/nota já aprovado no nível é recusado; valor ou nota diferente no mesmo nível (para nota: mesmo termo antes de "é/significa/:") vira proposta que substitui a aprovada ao ser aprovada (histórico mantido). A resolução continua tratando divergências legadas como conflito. | Em validação |
| T33 | 2026-11-11 | Fila única (`agent/memoria.py`): valores padrão, notas e consultas aprendidas, com referência `tipo-id`. Tela "Memória e aprovações" no chat (só setores do usuário) e ações do agente `listar_pendencias`, `aprovar_pendencia`, `rejeitar_pendencia` (sempre com confirmação). | Em validação |
| T40 | 2026-11-11 | Filtro por campo nas consultas aprendidas: parâmetro opcional `filtro-campo` (CAMPO=valor; dado pessoal) aceito só nos campos filtráveis da consulta (escolhidos no formulário ou, se nenhum, as colunas de texto), valor `[0-9A-Za-z -]` até 30, comparação exata. Critério informado e recusado impede a execução (antes rodava sem ele). Histórico de troca de função no Protheus fica na SR7 (R7_MAT, R7_DATA, R7_FUNCAO, R7_DESCFUN); a SRA só tem a função atual. | Em validação |
| T39 | 2026-11-11 | Período lido da própria mensagem pelo backend ("desde janeiro", "mês passado", "em março", "de X a Y", "a partir de DD/MM/AAAA") quando o modelo não envia `periodo-analise`; evita herdar o período da consulta anterior em continuações. | Em validação |
| T38 | 2026-11-11 | Provedor `OpenRouterProvider` (pedido do Diego), padrão `google/gemma-4-26b-a4b-it:free`, ativado por `IATIVA_AGENTE_PROVEDOR=openrouter` + `OPENROUTER_API_KEY` no `.env` do servidor. Sem dependência nova (urllib). Pede JSON por esquema; se o modelo recusar `response_format`, repete só com a instrução e extrai o JSON. Raciocínio ligado (`IATIVA_OPENROUTER_RACIOCINIO`), não reenviado entre mensagens. Mesmas esperas/reserva/mensagem de limite do Gemini (T36). Continua exigindo `IATIVA_AGENTE_ENVIO_LIBERADO=1`; modelos gratuitos podem registrar os dados (DPO). | Em validação |
| T37 | 2026-11-11 | Continuação de assunto: nova consulta do mesmo objetivo herda os critérios ditos na anterior da conversa (menos a busca por nome) e "executar" sem consulta aberta prepara e roda uma nova (antes só repetia "processando" do modelo). Chat estilo assistente: a mensagem aparece na hora com animação "Pensando…" e o botão vira "parar"; parar marca o envio (`PedidoInterrompido`, só id e dono) e o servidor descarta tudo se a IA ainda não agiu; se a consulta ao ERP já começou, ela termina e aparece ao recarregar. | Em validação |
| T36 | 2026-11-11 | Gemini sobrecarregado (503) ou no limite do plano gratuito (429): o provedor espera e tenta de novo (2 s e 4 s) e depois usa o modelo de reserva (`IATIVA_GEMINI_RESERVA`, padrão `gemini-3.5-flash`). Se nada responder, o usuário recebe mensagem clara para tentar em um minuto, sem tentativa extra do agente. | Em validação |
| T35 | 2026-11-11 | **Busca por nome** (pedido do Diego: "a IA tem que aprender a filtrar", para todos os setores). Parâmetro opcional `texto-busca` (dado pessoal: só na solicitação, 30 dias), nunca perguntado; o modelo o preenche quando a pessoa cita um nome. Vira `LIKE '%PALAVRA%OUTRA%'` (maiúsculas, sem acento, só letras/números, até 40 caracteres) em `A1_NOME` nas consultas fixas e no campo de busca das aprendidas (escolhido no formulário ou, nas antigas, a primeira coluna de texto que pareça nome: `_NOME`, `_NOMxxx`, `_NREDUZ`...). | Em validação |
| T34 | 2026-11-11 | Chat mestre: depois de executar no setor do usuário, roda uma solicitação por setor com "consultar", com a memória e as filiais do setor; os valores ditos na conversa valem para todos. Soma só com premissas iguais (exceto filial), filiais diferentes e resultado não truncado; senão mostra os blocos lado a lado e explica. Solicitações incompletas de outros setores são canceladas. | Em validação |

## Organização do trabalho

| ID | Data | Decisão | Situação | Observações |
|---|---|---|---|---|
| O1 | 2026-11-09 | Contexto compartilhado entre participantes por arquivos versionados: `PLANO.md`, `DECISOES.md`, `STATUS.md` (e `docs/PEDIDO_INICIAL.md`). Cada participante usa sua conta e pede à Claude que leia esses arquivos antes de trabalhar. | Adotada | Primeiro commit/push desta documentação autorizado em 2026-11-09 por Kayky Felipe (chat). |

## Mudanças de escopo

| M19 | 2026-11-12 | Somas sob pedido: o sistema calcula os totais das colunas numéricas de cada resultado (não exibidos). O navegador envia contagem e totais dos resultados na tela junto com a próxima pergunta, só para aquela chamada ao modelo (nada gravado; R3 mantida). D6 segue: linhas nunca vão ao modelo, só agregados. A resposta da IA que cita um total fica no histórico da conversa (retenção de 90 dias). | Pedido de Diego no chat ("some os valores"; "não quero que toda resposta com números...") |
| M15–M17 | 2026-11-11 | Ajustes pedidos por Diego: layout lembrado na sessão, renomear conversa, modelo/esforço Claude só para função TI, nomes das filiais, opção "todas as filiais", resultados anteriores mantidos só na memória da página (R3 preservada), animações sempre ativas, agente responde perguntas gerais. | Pedidos de Diego no chat |
| M18 | 2026-11-11 | Aprendizado automático sem aprovação humana: quando nenhuma consulta atende, a IA procura a tabela no dicionário, monta a consulta (tabela, colunas, data, busca, filtráveis), o sistema valida (compilação, validador SQL, tabelas restritas, campo filial obrigatório), descarta colunas sensíveis (CPF/CNPJ, RG, salário, contato, banco, nascimento), aprova e executa. Vale para quem consulta algum setor; a consulta fica liberada aos setores que a pessoa consulta. Filiais continuam pelas políticas; somente leitura; sem guardar resultados. Substitui, para consultas aprendidas, a exigência de aprovação. | Autorizado por Diego (chat: "Sim, sem aprovação") |
| M14 | 2026-11-11 | Completa a instalação da integração Claude no ambiente Docker confirmado pelo Diego: CLI nativa oficial 2.1.321 instalada na imagem como usuário iativa e disponível no PATH, sem depender do NVM do OpenMausBot. Mantém conta/logins no volume var existente, sem credenciais na imagem e sem modificar banco, rede ou portas. | Complemento da integração pessoal autorizada em M11/M12; ambiente Docker confirmado pelo Diego no chat |

| M13 | 2026-11-11 | Interface do chat conforme prévia aprovada: navegação por categorias recolhíveis, busca nos títulos autorizados, lateral redimensionável/recolhível, leitura expansível, tela inicial centralizada, mensagens e resultados com nova estética, compositor com enviar/parar e animações com redução de movimento. Saudação acompanha o horário local do navegador e frase inicial alterna a cada nova conversa (apenas índice na sessão Django). Sem mascotes/microfone, sem mudanças nas permissões, consultas ou histórico. | Autorizado por Diego (chat: "Pode fazer", após aprovação visual e observações de saudação/frase) |

| M12 | 2026-11-11 | Login Claude pela interface: conectar, abrir link oficial, concluir com código efêmero, verificar, cancelar e desconectar. CLI detectada automaticamente; conta em diretório exclusivo por usuário, somente superusuários (restrição adicional opcional por username), fluxo vinculado à sessão, sem salvar código. Após autenticação confirmada, seleciona o provedor apenas para o próprio usuário; mantém liberação de privacidade e regras de SQL. | Autorizado por Diego (chat: "Sim", após proposta da tela) |

| M11 | 2026-11-11 | Claude Code oficial para uso pessoal do Diego, por adaptador Python inspirado na chamada isolada do OpenMausBot. Sem cookies, API paga automática, compartilhamento de credenciais, SQL livre ou envio de resultados do ERP. Mantém compilador, validações, privacidade e histórico do Django. Implementação e testes isolados autorizados; ativação depende da CLI e login no ambiente do Python. | Autorizado por Diego (chat: "apenas para uso pessoal. Vamos fazer isso") |

| ID | Data | Mudança | Situação |
|---|---|---|---|
| M10 | 2026-11-11 | **Agente conversacional com Gemini.** (1) Provedor `GeminiProvider` (SDK `google-genai`, modelo padrão `gemini-3.8-flash`, saída JSON por esquema) para testes; continua exigindo `IATIVA_AGENTE_ENVIO_LIBERADO=1` e chave de projeto **com faturamento** (no nível gratuito o Google pode usar os dados). (2) O modelo responde em texto corrido e escolhe UMA ação de lista fechada (conversar, preparar/executar/cancelar consulta, aprender tabela, listar/remover aprendizado, lembrar); o backend autoriza e executa. (3) Troca de assunto encerra a solicitação anterior sozinha; com os parâmetros completos a consulta executa sem clique (somente leitura). (4) Memória do agente (`NotaAgente`): o usuário ensina termos/preferências; administrador grava para a empresa, demais só para si; sem CPF/CNPJ nem resultados; vai ao modelo como referência. (5) Remover consulta aprendida pelo chat (só administrador, com confirmação; vira "removida"). (6) Ao modelo vão também as últimas 12 mensagens da conversa (texto salvo pelo M8, sem resultados). | Autorizado por Diego (chat, 2026-11-11) |
| M9 | 2026-11-10 | **Aprendizado assistido de novas consultas.** Quando um administrador pergunta algo fora das consultas conhecidas, o sistema busca no dicionário (SX2/SX3, só estrutura) as tabelas candidatas e oferece aprender. O administrador define, num formulário, colunas, campo de data do período, filtros fixos, campos sensíveis (não exibidos) e setores que podem usar. Vira uma **consulta-modelo** (estrutura, não SQL) que passa pelo mesmo compilador e validador; só vale depois de aprovada por quem tem "aprovar definições" (sem autoaprovação, salvo `IATIVA_APRENDIZADO_AUTOAPROVACAO=1`). Uma tabela por consulta-modelo, com campo de filial obrigatório. Dados lidos não são guardados. | Autorizado por Diego (chat: "sim", após a proposta) |
| M8 | 2026-11-10 | **Altera R3/D11 para conversas.** Novo front estilo chat de IA (protótipo `docs/prototipo/chat`, cores e logo da Ativa) com conversas individuais, chat do setor e chat mestre, e **histórico de mensagens salvo no Django**. Regras: (1) grava o texto das mensagens e as fichas de critérios, **nunca resultados de consultas nem SQL**; (2) individual: só o dono; setor: quem tem "consultar" ou "ler memória" no setor; mestre: quem tem "consultar" em mais de um setor, vendo só os setores das suas políticas (sem acesso automático); (3) mensagens com padrão de CPF/CNPJ são recusadas; (4) conversas paradas há mais de `conversa_dias` (90) são apagadas pelo expurgo; (5) mensagens não aparecem no admin. Pendente: posição do DPO sobre guardar o texto (pode conter nomes de clientes). | Autorizado por Diego (chat: "Aprovo, pode fazer", após proposta das regras) |
| M7 | 2026-11-10 | Substitui a D8: commit e push sempre direto na `main`, sem pull request salvo pedido. Como a `main` dispara a atualização automática do servidor (M4), etapas ainda em validação também chegam ao servidor; `pytest` roda antes de cada push e o script roda os testes de novo antes de trocar o container. A Etapa 2 foi levada à `main` nesta data, ainda em validação. | Autorizado por Diego (chat: "eu quero que manda sempre para a main") |
| M6 | 2026-11-10 | Exceção à D8: commit e push da Etapa 2 **antes** da validação da etapa, na branch `claude/new-session-w616rq`. A `main` só recebe a etapa depois de aprovada. | Autorizado por Diego (chat: "faça o commit e o push") |
| M5 | 2026-11-09 | Servidor de desenvolvimento passa de Windows (WSL2/VM) para Ubuntu nativo com Docker Engine. `compose.yaml` mantido sem alteração (portas só em 127.0.0.1); acesso a partir de outros computadores por túnel SSH, sem publicar portas. | **Aprovada** em 2026-11-09 por Kayky Felipe (chat: "aprovo") |
| M4 | 2026-11-09 | Rotina de atualização automática do ambiente de desenvolvimento no servidor a partir da `main` (`scripts/atualizar.sh` + systemd timer): fast-forward, build, testes antes de trocar o container, verificação final e reversão automática em falha. Roda no host; nenhum container recebe acesso ao socket do Docker. | **Aprovada** em 2026-11-09 por Kayky Felipe (chat: "aprovo") |
| M3 | 2026-11-09 | Pull request da branch `claude/new-session-w616rq` para `main` (KaykyFelipe/IAtiva#1) e merge na `main` após a aprovação da Etapa 1. | Autorizado por Kayky Felipe (chat: "faz o merge na main") |
| M2 | 2026-11-09 | Exceção à D8: commit e push da Etapa 1 **antes** da aprovação da etapa, para que o servidor possa clonar o código e testar o ambiente em containers. (A Etapa 1 foi aprovada depois, no mesmo dia.) | Autorizada por Kayky Felipe (chat: "faz o commit") |
| M1 | 2026-11-09 | Etapa 1 cria apenas as apps necessárias agora (`core`, `chat`, `synthetic_erp`). As apps `accounts`, `memory`, `knowledge`, `metadata`, `agent`, `query` e `audit` serão criadas nas etapas correspondentes, em vez de esqueletos vazios. | Aprovada com a Etapa 1 |
