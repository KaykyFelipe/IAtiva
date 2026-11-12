# STATUS — IAtiva

**Atualizado em:** 2026-11-12
**Etapa atual:** 7 — Piloto local
**Situação:** **plano proposto em `PLANO.md` §8, aguardando aprovação.** Etapa 6 aprovada em 2026-11-12 por Diego. Pendente: conferência dos números com o Financeiro (T29). Base do ERP sem movimento após 08/09/2026 (confirmado, esperado). Filiais 002001 e 002002 nomeadas.

## Mudança M36 — ChatGPT nos dois agentes (2026-11-12)

- Conta ChatGPT via Codex oficial, por device code na página da OpenAI, na mesma tela das conexões Claude. Conta, modelo/esforço e provedor independentes por agente.
- Catálogo de modelos obtido da conta conectada, sem inventar IDs. Seleção explícita de Claude/ChatGPT; contas Claude e interruptores do servidor preservados.
- Corrigida a escrita por grupo na cópia isolada criada pelo root, preservando dono e ignorando links; ponteiro `.git` montado somente leitura para ambos os agentes de manutenção.
- Atendimento efêmero, sem ambiente/ferramentas, credenciais herdadas ou linhas do ERP; manutenção com Codex em sandbox externa Docker (raiz imutável, sem capabilities/privilégios) e gates existentes. CLI 0.167.0 fixada na imagem.
- Fluxo oficial de device code iniciado/cancelado em pasta nova sem credenciais existentes, também dentro da imagem no servidor autorizado. Sandbox externa conferida com dados sintéticos: escrita negada na raiz/diagnóstico e permitida só no worktree, conta e temporários; capabilities zeradas e NoNewPrivs ativo. Validação: 633 testes aprovados, 10 pulados no Windows (bash/git/flock); Django check sem problemas e nenhuma migração pendente. Implantação pela atualização azul/verde, com drenagem antes de desligar a versão antiga. Conta real será conectada pela pessoa na interface; inferência autenticada depende desse login.

## Mudança M35 — histórico de cargos com consulta encadeada (2026-11-12)

- Corrigido o encerramento após buscar matrículas: nome → vínculos autorizados por filial → SR7 filtrada por matrícula/filial → descrições SRJ/SQ3 → histórico e função cadastrada no vínculo mais recente.
- Continuação como "qual cargo mudou?" reaproveita o nome informado no contexto; não exige que a pessoa copie as linhas nem informe matrículas já localizadas.
- Resultados ficam só na resposta, sem envio ao modelo ou persistência. Restrições de tabela/campo, filiais, cancelamento e prazo reconferidos. Homônimos não são unificados; troca de matrícula não comprova mudança de cargo/efetivação.
- Período completo por padrão; período explícito filtra a SR7 sem perder matrículas antigas. Data de admissão identificada como tal, sem atribuir a ela uma alteração não comprovada.
- Dicionário e consulta encadeada conferidos no servidor autorizado (SRA/SR7/SRJ/SQ3), com permissões do usuário da conversa de origem e transação desfeita ao final. Sem resultados ou dados pessoais registrados nesta documentação. A data R7_DATA é permitida como data de evento mesmo com descrição "Data Alteracao Salarial"; restrições explícitas continuam prevalecendo e valores de salário não são consultados.
- Validação: 609 testes aprovados, 10 pulados no Windows (dependem de bash/git/flock); check Django sem problemas e nenhuma migração pendente de geração. Consulta encadeada validada no Protheus e, após implantação da M35, pedido e continuação aprovados com a Claude real, sob transação desfeita ao final.
- Operação: manutenção e publicação foram ativadas por pedido de Diego no servidor-bots (192.168.80.237), com login confirmado e timer ativo. Não acessar outro servidor.

## Mudanças M33/M34 — conexões separadas e revisão (2026-11-12)

- M33 já implementada: cartões de atendimento e manutenção com login, modelo e esforço próprios. Manutenção com conta do sistema (padrão Opus), separada do atendimento.
- M34: perfil desconhecido não altera o atendimento; verificação da CLI atualiza só o marcador do cartão escolhido; conexão salva não é apresentada como login recém-verificado.
- Preferências independentes, gravação atômica e teste de integração com o executor. JSON inválido não derruba o executor; login da manutenção protegido contra desconexão por outra pessoa de TI.
- Documentação das conexões atualizada, inclusive função TI e implantação azul/verde.
- Diego informou que a deploy key foi reconhecida e o push simulado aceito; não foi feito novo teste no servidor nesta revisão.
- Pendente: concluir login real no cartão de manutenção e habilitar os interruptores no servidor em etapas. Conectar ou salvar modelo não os habilita.
- Validação: 599 testes aprovados e 10 pulados no Windows (exigem bash/git/flock); 70 testes direcionados aprovados. Django check sem problemas e nenhuma migração pendente de geração. Login real e implantação no servidor não executados nesta revisão.

## Mudanças M30–M32 — correções, atualização sem interrupção, manutenção automática (2026-11-12)

- M30: verificação só termina com evidência; conclusão perdida refeita pelo worker uma vez; datas futuras/inválidas; pergunta pendente retoma a mesma tarefa.
- M31: azul/verde com drenagem e reversão. Teste real no servidor (`scripts/testar_implantacao.sh`): consulta longa iniciada na versão A terminou em A; novas foram para B; 31/31 requisições 200 durante a troca; tarefa do worker concluída uma vez pela versão que a pegou; prontidão ruim e migração incompatível bloquearam a troca; reversão voltou para A.
- M32: incidentes sanitizados e executor de manutenção. **Publicação automática desligada**: login Claude e ativação em etapas pendentes; credencial de push reconhecida segundo relato do Diego (docs/MANUTENCAO.md).
- Testes: 586 aprovados e 10 pulados no Windows; no servidor, 576 na imagem + 37 com git (atualização e manutenção).
- Implantado no servidor (2026-11-12): saída única do layout antigo com 1 amostra sem resposta (~1 s, o servidor antigo não drenava); a partir daí, trocas azul/verde com drenagem.
- **Pendente (Diego):** confirmar o agendamento da manutenção, concluir login Claude da manutenção e ligar em etapas em `/etc/iativa/manutencao.env`.

## Mudança M29 — conclusão da verificação fica salva (2026-11-12)

- Ao recarregar, a conclusão aparece na hora, sem consultar o ERP de novo. **Muda a R3 para tarefas** (decisão do Diego): nomes e matrículas da conclusão ficam gravados com a conversa, sem prazo; CPF nunca.
- Testes: 549 aprovados e 10 pulados no Windows.

## Mudança M27 — a verificação se corrige sozinha (2026-11-12)

- O erro "OperationalError" era a busca em lote estourando o tempo no Protheus. Agora o lote é dividido, o registro problemático é repetido e, se ainda falhar, fica inconclusivo com o motivo; o resto da lista é verificado.
- Toda etapa tenta de novo e mostra no cartão o que corrigiu ("↻ ..."); só desiste depois de 3 tentativas e de pedir outro caminho ao modelo.
- Barra do compositor some ao terminar; botão de enviar vira "parar" enquanto a verificação roda.
- Conferido no Protheus (sem gravar nada): a lista de 19 desligados foi verificada inteira em 41 s, com 33 consultas; 16 verificados e 3 inconclusivos por nome igual. A busca se ajustou sozinha (lotes de 9) depois da primeira lentidão.
- Testes: 547 aprovados e 10 pulados no Windows.

## Mudança M26 — continuar até a verificação; "Pensando" ao vivo (2026-11-12)

- "Verifique se os demitidos tiveram matrícula nova": refaz a lista e já agenda a verificação de todos, sem nova mensagem.
- O "Pensando…" troca de texto conforme a IA trabalha; uma barra fixa acima do campo de mensagem mostra a verificação em segundo plano.
- Comparação de vínculos mantida por nome (decisão do Diego, 2026-11-12).
- Testes: 544 aprovados e 10 pulados no Windows. Não validado com o modelo real nem no navegador.

## Mudança M25 — tarefas longas com conclusão verificável (2026-11-12)

- "Todos foram demitidos mesmo?" sobre uma lista: a IA verifica todas as pessoas em segundo plano (worker), não só o exemplo, e mostra a conclusão de cada uma.
- Cartão de andamento com etapas, cobertura e cancelamento; fechar a página não interrompe.
- Comparação por CPF só com `IATIVA_VINCULO_POR_DOCUMENTO=1` (**decisão pendente de privacidade**); sem isso, nome igual fica inconclusivo.
- Testes: 537 aprovados e 10 pulados no Windows. **Não validado:** modelo real (roteiro `avaliar_agente`, cenário novo) e campos reais da SRA no Protheus.

## Mudança M24 — correções do ciclo (2026-11-12)

- "Some X" sem consulta aprendida: cria a consulta já com o cálculo, sem cair na escolha manual de tabela.
- Chat mestre não soma setores com regras da memória diferentes.
- Prazo total da mensagem vale antes de cada chamada ao modelo e de cada consulta.
- "Quantos foram demitidos?" seguido de "quais os nomes?": a segunda volta a listar (o cálculo não é herdado). Se a consulta não tiver o nome, a IA é instruída a criar outra com ele.
- Testes: 522 aprovados e 10 pulados no Windows. Não validado com o modelo real.

## Mudança M23 — junção pelo campo certo (2026-11-12)

- Com duas ligações para a mesma tabela, a IA usa a da chave da tabela base (cliente da nota, fornecedor do título).
- M22 conferida no Protheus real (contas a pagar e faturamento): total, quantidade, agrupado e média entre filiais batem entre si. Falta a conferência do Financeiro (T29) e a avaliação com o modelo real.
- Testes: 516 aprovados e 10 pulados no Windows.

## Mudança M22 — a IA executa, confere e corrige (2026-11-12)

- Ciclo fechado: se a primeira tentativa falha por motivo corrigível (cálculo inválido, campo que não existe, consulta sem o campo), a IA recebe o motivo e tenta outro caminho na mesma mensagem. Limites: 3 passos, 1 retentativa de erro transitório, 120 s.
- A resposta com o número sai junto com a consulta ("Soma de …: …, N registros") e não é salva no histórico.
- Total sempre calculado no banco sobre todos os registros, mesmo com a lista cortada em 500 linhas. Subtotal, ranking e total geral aparecem separados.
- O navegador não envia mais resumos de resultados; o modelo não vê nenhum número.
- Memória: notas por relevância, com tipo e validade; regra de negócio só vale com campo e valor conferidos e confirmados; "o que você lembra?" e "esqueça nota-N".
- Testes: 515 aprovados e 10 pulados no Windows.
- **Não validado:** interpretação com o modelo real (roteiro em `docs/AVALIACAO_MODELO.md`, ainda não executado) e comportamento no Protheus real (login do IAtiva sem acesso ao banco no momento).
- Migração nova: `iativa_agent.0004_nota_tipo_regra`.

## Mudança M21 — juntar tabelas (2026-11-12)

- A IA traz nomes/descrições de outra tabela (ex.: nome do cliente no faturamento) pela chave do dicionário.
- Testes: 491 aprovados e 10 pulados no Windows.

## Mudança M20 — cálculos no banco (2026-11-12)

- "Total", "média", "top 10 clientes", "por mês", "por filial": a IA pede o cálculo e o banco devolve pronto.
- Testes: 488 aprovados e 10 pulados no Windows.

## Mudança M19 — somas sob pedido (2026-11-12)

- Totais das colunas numéricas calculados e não exibidos; vão à IA só na pergunta seguinte ("some os valores").
- Instruções: entender a intenção, tentar outro caminho sozinha antes de dizer que não dá.
- Testes: 477 aprovados e 10 pulados no Windows.

## Mudança M18 — IA aprende consultas sozinha (2026-11-11)

- Sem consulta que atenda: a IA acha a tabela, monta a consulta, o sistema valida, aprova e executa.
- Colunas sensíveis descartadas automaticamente; vale para quem consulta algum setor.
- Testes: 474 aprovados e 10 pulados no Windows.

## Mudança M17 — chat mais esperto e todas as filiais (2026-11-11)

- Filial "TODAS": uma consulta por filial autorizada, mesmo validador, coluna Filial no resultado.
- Resultados anteriores ficam na tela ao mandar nova mensagem (só na memória da página; R3 mantida).
- Animações rodam mesmo com "reduzir movimento" do sistema.
- Instruções do agente: responde perguntas gerais, nunca só "não consigo", corrige sozinho.
- Pendente: faturamento (SF2) voltou 0 linhas em out/2026; diagnóstico exige leitura agregada no ERP.

## Mudança M16 — nomes das filiais (2026-11-11)

- `iativa/accounts/filiais.py`: código e cidade das 8 filiais (001001–001008).
- Filial escrita por nome ("filial Cuiabá") vira o código; botões e critérios mostram
  "código · cidade". Autorização por filial inalterada (continua pelas políticas).
- Testes: 469 aprovados e 10 pulados no Windows.

## Mudança M15 — ajustes do chat e da conexão Claude (2026-11-11)

- Layout (largura da lateral, leitura expandida, lateral recolhida) lembrado na sessão
  do servidor ao trocar de conversa; nada no navegador.
- Títulos das conversas maiores; lápis ao passar o mouse renomeia a conversa (só o dono).
- Conectar Claude: só usuários com função TI no perfil (superusuário sozinho não basta);
  escolha de modelo (Opus/Sonnet/Haiku) e esforço (`--effort`) salva por usuário.
- Testes: 456 aprovados e 10 pulados no Windows.

## Mudança M14 — CLI Claude no Docker (2026-11-11)

- Corrigida a dependência ausente na imagem: instalação nativa oficial 2.1.321,
  executada como `iativa`, em `/home/iativa/.local/bin`, sem Node/NVM do host.
- Build verifica versão e flag de isolamento. Conta pessoal permanece no volume
  `var` já existente; sem credenciais na imagem. Compose, portas e bancos preservados.
- Instruções de rebuild e correção de caminho explícito em `docs/CLAUDE_CODE.md`.
- Docker Engine indisponível nesta máquina: build e login no container pendentes
  de confirmação no servidor. Testes Python: 451 aprovados e 10 pulados no Windows.

## Mudança M13 — entrega da interface

- Layout aprovado aplicado ao chat real: lateral com categorias recolhíveis e
  busca, divisória arrastável/teclado, recolher menu e expandir área de leitura.
- Início com pergunta centralizada, sugestões e saudação pelo horário local,
  atualizada a cada minuto/retorno à aba. Frase inicial alterna ao abrir nova
  conversa; só o índice de apresentação é mantido na sessão Django.
- Mensagens, tabelas, memória, formulários e compositor reorganizados; enviar/parar
  alinhados, animações discretas com suporte a redução de movimento. Sem mascotes
  ou microfone. Atualizações HTMX preservam a largura e categorias na página.
- Parar interrompe a espera visual imediatamente e registra a interrupção em
  paralelo; consulta que já começou continua sujeita à regra T37.
- CSP preservada; HTMX não copia atributos de estilo entre respostas.
- 451 testes aprovados, 10 pulados no Windows. Navegador com Django isolado e
  dados sintéticos: saudação 6h/14h/21h, alternância de frases, drag, recolher,
  expansão, enviar/parar com campo vazio, OOB, resultados, celular e memória.
  Sem erros de JavaScript/CSP. Nenhum acesso ao ERP real ou mudança de provedor.

## Mudança M12 — login Claude pela interface (2026-11-11)

- Página "Conectar Claude" na lateral do superusuário: iniciar, link oficial,
  código oculto, concluir, verificar, cancelar e desconectar.
- CLI detectada fora do PATH, inclusive instalação do Claude Desktop no Windows.
- Conta exclusiva por usuário em `var/claude-pessoal`; seleção do provedor somente
  para o próprio dono. Login não altera a liberação de privacidade do chat.
- Fluxo efêmero vinculado a usuário/sessão, sem persistir código; somente links
  HTTPS oficiais; limites de saída e tempo; POST com CSRF e respostas sem cache.
- 27 testes de login passaram; suíte completa: 449 aprovados, 10 pulados no Windows.
  Tela renderizada e conferida visualmente em navegador headless.
- CLI 2.1.321 e login existente confirmados neste Windows; chamada real com mensagem
  fictícia retornou JSON válido, sem ERP. Sem confirmação de ausência de cobrança.
- Pendente: concluir login real pela nova tela no ambiente do Django. O fluxo local
  exige processo único; CLI deve estar instalada dentro do ambiente de execução.

## Mudança M11 — entrega do adaptador

- Adaptador `claude-code` com stdin, JSON estruturado, validação dos contratos,
  ferramentas/MCP desativados e sem persistência de sessão na CLI.
- Login oficial por assinatura conferido antes do envio; API e autenticação
  desconhecida bloqueadas, ambiente sem credenciais ERP e sem fallback pago.
- `testar_claude` diagnostica a CLI e faz envio sintético opcional sem banco.
  Configuração e limites em `docs/CLAUDE_CODE.md`; provedor não ativado automaticamente.
- 27 testes do adaptador passaram, incluindo geração/validação de SQL sem execução.
  Suíte completa: 422 aprovados, 10 pulados no Windows (dependem de bash/git/flock).
- Pendente: confirmar caminho da CLI e login pessoal no ambiente do Python,
  chamada real com mensagem fictícia e consulta de leitura autorizada no ERP.

## Etapa 6 — entregas anteriores

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
