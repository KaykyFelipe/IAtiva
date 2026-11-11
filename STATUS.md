# STATUS — IAtiva

**Atualizado em:** 2026-11-11
**Etapa atual:** 6 — Memórias setoriais e chat consolidado
**Situação:** **implementada, aguardando validação.** Plano aprovado em 2026-11-11. Etapa 5 aprovada em 2026-11-11 por Diego; conferência dos números com o Financeiro e premissas T29 seguem pendentes.

## Etapa 6 — o que foi entregue

- Memória do setor: notas do agente por setor, com proposta → aprovação; valores e notas em conflito substituem o aprovado só quando aprovados (T31, T32).
- Fila única de aprovação (valores, notas, consultas aprendidas) na tela "Memória e aprovações" e pelo chat ("quais propostas estão pendentes?", "aprovar nota-3") (T33).
- Busca por nome em todas as consultas ("a Alcans lançou conta mês passado?"): filtro opcional pelo nome, sem precisar procurar na lista (T35).
- Chat mestre consolidado: um bloco por setor autorizado, cada um com a sua memória e filiais; soma só com premissas iguais e filiais diferentes (T34).
- 350 testes passando (10 da rotina M4 pulados no Windows).

## Checklist de validação da Etapa 6

- [ ] "Lembre para o setor que ..." vira proposta; só vale depois de aprovada; nota "só minha" vale na hora.
- [ ] Autor (sem autoaprovação) e quem não aprova no nível não conseguem aprovar.
- [ ] Proposta que contradiz algo aprovado aparece lado a lado na fila; aprovar substitui e mantém o histórico.
- [ ] Setor não consegue definir valor contrário a regra obrigatória da empresa.
- [ ] Tela "Memória" mostra só os setores do usuário, sem dados do ERP.
- [ ] Chat mestre: um bloco por setor, cada um com sua memória/filiais; não soma premissas diferentes nem a mesma filial.
- [ ] Setor sem "consultar" não aparece no consolidado.
- [ ] Observação: com a autoaprovação ligada no servidor, as propostas do Diego (único aprovador) já entram aprovadas.

## Mudança M10 — agente conversacional com Gemini (2026-11-11)

- `agent/agente.py` (decisão + ações), `agent/decisao.py` (esquema), `GeminiProvider`, `NotaAgente` (memória), consulta aprendida "removida".
- Chat em texto corrido: pergunta o que falta em frase, opções rápidas como botões, execução automática ao completar, troca de assunto sem clique.
- Pendências: chave do Gemini no `.env` (projeto com faturamento) e `IATIVA_AGENTE_ENVIO_LIBERADO=1` (decisão do DPO); avaliação com perguntas reais.

## Mudança M9 — aprendizado de novas consultas (2026-11-10)

- Administrador pergunta algo desconhecido → busca no dicionário → "Aprender com XXX" → formulário (colunas, período, filtros fixos, setores) → proposta testada no compilador/validador → aprovação por outra pessoa com "aprovar definições" → disponível para os setores escolhidos.
- `ConsultaModelo` (app agent), objetivos dinâmicos em `contratos.py`, plano `montar_modelo`, telas no chat.
- Autoaprovação liberada no servidor em 2026-11-10 (`IATIVA_APRENDIZADO_AUTOAPROVACAO=1`, pedido de Diego, único administrador); desligar quando houver um segundo aprovador.
- Pendências: tabelas de RH exigem posição do DPO antes de uso real.

## Etapa 5 — o que foi entregue

- `iativa/query`: plano estruturado por objetivo (`plano.py`), compilador para T-SQL com nomes do catálogo (`compilador.py`), validação AST com `sqlglot` (`validador.py`), execução somente leitura com limite de linhas e tempo (`executor.py`), serviço com reconferência, estados e checagem de duplicação (`servico.py`).
- Chat: botão "Executar consulta" na ficha pronta; resultado em tabela com total e premissas; "Executar de novo" nas conversas reabertas. Resultado nunca é salvo.
- 305 testes passando (10 da rotina M4 pulados no Windows), inclusive comparação com consulta de referência na base sintética.
- Homologação (2026-11-10, filial 001001): inadimplência e títulos a vencer executaram em ~1 s (consultas com literais validados, T30). Conferência dos números com o Financeiro pendente.

## Checklist de validação da Etapa 5 (aprovada em 2026-11-11; itens do Financeiro pendentes)

- [ ] Inadimplência, títulos a vencer e posição de cliente batem com o que o Financeiro vê no Protheus (conferir na homologação).
- [ ] Premissas T29 (tipos excluídos, saldo atual, regra de atraso) aprovadas ou corrigidas pelo Financeiro.
- [ ] SQL recusado pelo validador nos casos proibidos; só leitura; filial e exclusão lógica sempre aplicadas.
- [ ] Resultado não fica no histórico nem no banco; reabrir pede nova execução.
- [ ] Perda de permissão/filial entre "pronta" e a execução bloqueia.

## Etapa 4 (aprovada) — notas

## Mudança M8 — novo chat com histórico (2026-11-10)

- Tela no layout aprovado (cores e logo da Ativa): lateral com nova conversa, chat mestre, chat do setor, conversas individuais e perfil; conversa com várias mensagens e ficha de critérios.
- Modelos: `ConversationContext` ganhou tipo (individual/setor/mestre) e título; novo `Mensagem` (texto e ficha, nunca resultados).
- Expurgo: conversas paradas há 90 dias e mensagens antigas do setor.
- Pendências: posição do DPO sobre guardar o texto das conversas; o chat mestre ainda consulta como o setor do próprio usuário (visão consolidada real é a Etapa 6); preencher nome e sobrenome dos usuários no admin.

## Etapa 4 — o que foi entregue

- `iativa/agent`: `LLMProvider` com `FakeProvider` e `AnthropicProvider` (saída estruturada); contratos de parâmetros por objetivo; conversão de valores; validação estrita da saída; orquestrador com interpretação → contrato → descoberta de metadados → avaliação de completude → perguntas.
- Chat com perguntas pendentes em formulário (data, período, escolha, filial autorizada, número), critérios com origem, ambiguidade, cancelamento e mudança de assunto.
- Falhas do modelo: uma nova tentativa; depois mensagem ao usuário, sem mudar a solicitação; auditado como `modelo_falhou`.
- 263 testes passando (10 da rotina M4 pulados no Windows).

## Checklist de validação da Etapa 4

- [ ] Pergunta vaga ou fora do escopo não cria solicitação.
- [ ] "Quais clientes estão inadimplentes?" pede só o que falta (data de corte, tolerância, baixa parcial, filial) e reaproveita preferências salvas.
- [ ] Correção ("na verdade 10 dias") troca o valor; mudança de assunto encerra a anterior; cancelamento funciona.
- [ ] Filial fora das autorizadas e CPF/CNPJ são recusados.
- [ ] Sem domínio, definição ausente, tabela restrita ou ERP fora do ar: bloqueia com motivo claro.
- [ ] Mensagem não é gravada nem ecoada; ao modelo não vão linhas do ERP.
- [ ] Avaliação com o modelo real (Claude) quando houver chave e liberação do DPO.

## Pendências da Etapa 4

- [ ] Chave da API da Anthropic (no `.env` do servidor) e posição do DPO/LGPD (`IATIVA_AGENTE_ENVIO_LIBERADO=1`).
- [ ] Avaliação de interpretação com o modelo real (perguntas reais, referências ao contexto, negações).
- [ ] Definições reais (o que é "inadimplente", tipos de título considerados) aprovadas pelo Financeiro (informação 8).

## Histórico

- Etapa 0 — Levantamento e proposta: **aprovada** em 2026-11-09 (D1–D12; ver `DECISOES.md`).
- Etapa 1 — Fundação em localhost: **aprovada** em 2026-11-09 (inclui T1–T7, M1). Commit de segurança antes da aprovação (M2). Pull request KaykyFelipe/IAtiva#1 com merge na `main` (M3).
- Etapa 2 — Autorização e memória de parâmetros: **aprovada** em 2026-11-10 (T8–T13). Migrações aplicadas no PostgreSQL do servidor.

## Etapa 2 — o que foi entregue

- `iativa/accounts`: `Department`, `UserProfile` (setor e função informativa), `AccessPolicy` (permissões separadas por setor, filiais, vigência). Autorização em `authz.py`; sem permissão universal; superusuário e função não concedem acesso a dados.
- `iativa/chat`: `ConversationContext`, `RequestContext` (objetivo como código estruturado, nunca texto) e máquina de estados; checagem determinística de completude (`avaliar_completude`).
- `iativa/memory`: `ParameterDefinition`, `ParameterBinding` (escopos solicitação/usuário/setor/empresa, aprovação, validade, versão), `MemoryRevision`; resolução por precedência (D12) com conflitos; dados pessoais só na solicitação (30 dias); CPF/CNPJ recusados.
- `iativa/audit`: `OperationAudit` imutável, só códigos e contagens; comando `expurgar_retencao`.
- Comando `seed_exemplos` (setores e definições fictícios). Admin com aprovação/rejeição de propostas via serviços.
- 161 testes passando (10 da rotina M4 pulados no Windows), sem rede.

- Etapa 3 — Conhecimento e metadados: **aprovada** em 2026-11-10 (T14–T19), com fonte real de homologação.
- Etapa 4 — Agente e esclarecimentos: **aprovada** em 2026-11-10 (T20–T24, M8).
- Etapa 5 — Consulta de leitura: iniciada e entregue para validação em 2026-11-10 (T25–T29).

## Etapa 3 — o que foi entregue

- `iativa/knowledge`: documentos `knowledge/**/*.md` com cabeçalho (origem, versão, responsável, situação, aprovação); só aprovados e sem CPF/CNPJ/credenciais são recuperáveis; busca local (BM25) com limite de trechos e tamanho; comando `validar_conhecimento`. Quatro documentos sintéticos de exemplo.
- `iativa/metadata`: adaptadores `DicionarioProtheus` (SX2/SX3/SIX/SX9), `FisicoSqlite` e `InformationSchemaSqlServer` (consultas fixas e parametrizadas); `Catalogo` junta dicionário e estrutura física; descoberta progressiva (domínios → entidades → campos → relacionamentos → conhecimento), auditada só com etapa e contagem; `DominioSetor` e `RestricaoMetadado` no admin; comando `descobrir_metadados`.
- Base sintética versão 2: inclui dicionário SX sintético (com campo virtual, campo sem coluna física e linha excluída para teste).
- 201 testes passando (10 da rotina M4 pulados no Windows), sem rede.

## Checklist de validação da Etapa 3

- [ ] Só documentos aprovados são recuperados, com origem, versão e aprovação; conteúdo proibido recusa o documento.
- [ ] Busca devolve poucos trechos relevantes, nunca a base inteira.
- [ ] Descoberta em camadas; detalhe de no máximo 3 tabelas por vez; domínio com lista fechada de tabelas.
- [ ] Sem acesso ao domínio (setor não habilitado ou sem "consultar"), nada é descoberto; superusuário não tem acesso automático.
- [ ] Restrições escondem tabelas/campos, relacionamentos que os usam e documentos que citam tabela oculta.
- [ ] Campo virtual, sem coluna física ou fora do dicionário não é consultável; personalização vem do dicionário, não do prefixo.
- [ ] Conhecimento sintético nunca é usado com fonte real; fonte real recusada até liberação.
- [ ] No servidor: `seed_synthetic --substituir` e `seed_exemplos` executados; `descobrir_metadados` conferido.

## Pendências da Etapa 3

- [ ] Informação 4: forma de acesso ao dicionário real (o adaptador assume tabelas SX no banco).
- [ ] Informações 5–8 e liberação explícita para ler metadados da base de homologação.
- [ ] Documentos de conhecimento reais, aprovados pelo responsável do Financeiro.

## Pendências da Etapa 2 (não bloqueiam a Etapa 3)

- [ ] Lista real de setores e perfis; quem aprova em cada nível; quem tem visão consolidada (informações 1 e 2). Hoje: exemplos fictícios.
- [ ] Agendar `expurgar_retencao` diariamente no servidor.
- [ ] Solicitações abandonadas (não encerradas) não são expurgadas; definir política.
- [ ] Interface de usuário para memória/aprovação: hoje só pelo admin (API e telas nas Etapas 4 e 6).

## Etapa 1 — o que foi entregue

- Projeto Django 5.2 (`config/`, `iativa/core`, `iativa/chat`, `iativa/synthetic_erp`).
- Interface mínima (templates + htmx local): login, chat que confirma recebimento sem armazenar, logout.
- Configuração via `.env` (`.env.example` versionado; `.env` ignorado).
- Banco da aplicação PostgreSQL 16; base sintética do ERP separada e somente leitura (`manage.py seed_synthetic`).
- Restrições: `runserver` só em 127.0.0.1 (exceção controlada em container, T6); verificações do sistema; `DEBUG` fixo em `False`; `no-store`; CSP; filtro de redação de logs.
- Ambiente de desenvolvimento em containers (`Dockerfile` Ubuntu 24.04 + `compose.yaml`), portas só em 127.0.0.1.
- 82 testes automatizados (`pytest`), sem rede (92 com a rotina M4).

## Aprovado fora das etapas

- **M4 — Atualização automática a partir do GitHub** (`scripts/atualizar.sh`, `scripts/instalar_agendamento.sh`): aprovada em 2026-11-09; testada com Docker simulado (10 testes). Falta executar no servidor.
- **M5 — Servidor Ubuntu nativo**: aprovada em 2026-11-09; `compose.yaml` sem alteração; acesso por túnel SSH.

## Pendências da Etapa 1 (não bloqueiam a Etapa 2)

- [ ] Executar no servidor Ubuntu (a partir da `main`): `docker compose up -d --build` e `docker compose run --rm app pytest -p no:cacheprovider` (ainda não executado em Docker real).
- [ ] Servidor alterado para Ubuntu nativo (informado em 2026-11-09). Confirmar versão do Ubuntu e instalação do Docker Engine.
- [ ] Confirmar se o repositório é privado (o servidor precisará de credencial somente leitura para a atualização automática).

## Informações pendentes

Necessárias para a Etapa 2:

1. Lista de setores e perfis iniciais (e quem aprova conhecimento em cada nível).
2. Quem terá a visão consolidada (diretoria/TI) e de quais setores.

Necessárias a partir da Etapa 3 (equipe do ERP):

3. ~~Versão do Protheus e do SQL Server; collation.~~ Recebida em 2026-11-10: Protheus 12.1.2510; SQL Server 2022 (16.0.4212.1); collation `Latin1_General_BIN` (comparação binária: diferencia maiúsculas/minúsculas e acentos; ordenação por código de caractere).
4. ~~Forma suportada de acesso ao dicionário (SX2, SX3, SIX, SX9, SX5, SX6 — banco ou arquivos).~~ Recebida em 2026-11-10: dicionário no banco (compatível com o adaptador `DicionarioProtheus`, T16).
5. Estrutura de empresas/filiais (SM0), sufixo das tabelas e modo de compartilhamento de SA1, SE1, SE5. Parcial (2026-11-10): todas as tabelas compartilhadas entre filiais e com campo de filial; o nome da tabela não indica filial (a conferir com X2_MODO no dicionário). Faltam: empresas/filiais (SM0) e sufixo das tabelas (será lido do próprio banco).

**Conexão com a homologação (2026-11-10):** rede liberada; `verificar_erp` conectou com o login somente leitura (confirmado: sem papéis ou permissões de escrita). Achados (só metadados):
- Dicionário no banco com sufixos 010, 020 (incompleto: só SX2) e 990. Tabela `SYS_COMPANY` existe.
- Empresa 010: SA1, SE1 e SE5 presentes; campos personalizados marcados no SX3; 4 relacionamentos SX9 entre elas.
- Compartilhamento esclarecido em 2026-11-10: filial tem 6 posições (3 da empresa do grupo + 3 da filial). O catálogo lê X2_MODO, X2_MODOUN e X2_MODOEMP: SE1/SE5 exclusivas por filial (código completo), SA1 compartilhada (em branco), SF4 exclusiva só por empresa (`001   `). Documento `knowledge/protheus/filiais-e-compartilhamento.md` **aprovado** por Diego em 2026-11-10 (usado quando a fonte de metadados for a real).
- O índice 1 do SE5 é por data/banco, não pela chave do título; a ligação SE1–SE5 deve vir do SX9.
- Empresa de sistema: **010** (`IATIVA_ERP_SUFIXO`), confirmada em 2026-11-10.
- Base sintética versão 3: rodar `seed_synthetic --substituir` no servidor após atualizar.
- **Rede instável (2026-11-10, 15:45):** a porta do SQL Server voltou a ficar inacessível a partir do servidor (timeout), depois de funcionar às 15:17–15:38. Pedir liberação permanente à equipe de rede.
- **Fonte de metadados do servidor: Protheus real** desde 2026-11-10 (T19). Conhecimento sintético deixa de ser usado lá; só documentos `origem: erp`/negócio aprovados.
6. Documentação das personalizações do domínio.
7. Base de homologação disponível e responsável pelo login somente leitura. Parcial (2026-11-10): login dedicado somente leitura informado (nome registrado só no `.env` do servidor quando liberado; senha nunca no chat ou no repositório). Base: homologação. Endereço e nome do banco recebidos em 2026-11-10 (ficam só no `.env` do servidor, não no repositório). Faltam, confirmação das permissões do login e liberação explícita.
8. Responsável de negócio no Financeiro para aprovar definições (ex.: "inadimplente").

Necessárias antes da Etapa 4:

9. Provedor de IA, condições contratuais e posição do DPO/LGPD (D5, D6).

Necessárias antes da Etapa 7 (piloto):

10. Prazos de retenção de auditoria e revisões (D11).

## Próximo passo

Validar a Etapa 3 pelo checklist acima. Informações 4–8 quando disponíveis; o Protheus real só entra com liberação explícita.
