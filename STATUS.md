# STATUS — IAtiva

**Atualizado em:** 2026-11-25
**Etapa atual:** 7 — Piloto local
**Situação:** **Etapa 7 aprovada em 2026-11-16 por Diego; em andamento.** Feito: roteiro do piloto (`docs/piloto/ROTEIRO.md`) e auditoria de não armazenamento (`manage.py auditar_armazenamento`). Falta: usuários do Financeiro, conferência dos números com relatórios do Protheus, avaliação da interpretação e latência. Etapa 6 aprovada em 2026-11-12 por Diego. Pendente: conferência dos números com o Financeiro (T29). Base do ERP sem movimento após 08/09/2026 (confirmado, esperado). Filiais 002001 e 002002 nomeadas.

## Mudança M157 — Gemini CLI nas Conexões de IA

- Gemini CLI oficial com versão fixa na imagem da aplicação. O cartão Gemini do atendimento conecta com chave individual do Google AI Studio, verifica o modelo e permite selecionar o provedor.
- Execução headless com ambiente temporário, política de ferramentas bloqueadas, extensões desativadas, prazo do pedido e contrato de decisão validado pelo backend.
- A conexão exige credencial fornecida pela TI no cartão. O agente de manutenção continua com os provedores já integrados ao executor.

## Mudança M156 — revisão de categorias e contexto na triagem (2026-11-25)

- O levantamento agrupado só acompanha a pergunta sobre classificação após aprovação da base pelo revisor. Com revisão indisponível, um agrupamento ainda sem classificação não é aprovado por falta de prazo.
- Falha temporária do provedor na triagem identifica o serviço indisponível. O registro no servidor da tentativa nº 5 confirmou limite ou capacidade do Claude antes da consulta.
- A triagem e a segunda leitura passam a receber notas relevantes aprovadas nos escopos pessoal, setorial e corporativo. Para outros chats, recupera apenas resumos relevantes do próprio usuário e revalida o acesso.
- Validação local direcionada aos módulos alterados. A atualização mantém uma execução integral e isolada no servidor antes da troca de tráfego.
- Context7 instalado no Codex para documentação técnica. O histórico e a memória da IAtiva seguem no backend com os controles de acesso existentes.

## Mudança M155 — filtro por palavra no SQL Server, entrega do que foi comprovado e problema concreto (2026-11-25)
- Caso: “quantos módulos tem hoje na ativa, disponíveis e deslocados” respondeu só a lista do que faltava (registro M149 nº 4).
- Reconstrução: a 1ª consulta contou todos os bens e a revisão disse o problema concreto (a entidade não foi restringida). O modelo tentou restringir pela família de bens, que era o caminho certo. As duas execuções seguintes foram recusadas pelo executor (“Valor fora do formato permitido”), e a repetição encerrou o pedido.
- Primeira decisão que travou: o termo do “contém” tolerante a acento (M154) usa `_`, que o literal seguro do SQL Server não aceitava. Nos testes com sqlite não há literal embutido, por isso não apareceu. A mesma recusa desligava a sonda de termos no servidor.
- Correções gerais:
  - `_` aceito no literal seguro (curinga de um caractere; nunca aspas, ponto e vírgula ou comentário);
  - “contém” do `filtro-campo` e sonda de termos passam a casar por início de palavra (`LIKE_PALAVRA`), com ou sem acento: um termo não acha outra palavra que só o contém (ex.: “disponível” em “indisponível”);
  - revisão reprovada entrega junto o que já foi aprovado (ex.: o total) e, se faltar definição, a pergunta vem com essa parte;
  - o encerramento diz o problema concreto da revisão e separa falha técnica (validador/ERP) de falta de dado ou definição, em vez de repetir as entregas pedidas;
  - contagens de uma mesma pergunta saem numa tabela “Situação | Quantidade” com a data de referência; cada valor é calculado no banco, sem somar situações para formar o total;
  - consulta com a mesma tabela, colunas e filtros e só outro nome não conta como tentativa nova.
- Ensaio real transacional (e450af4): a pergunta da imagem terminou numa pergunta de definição, mas sem mostrar o levantamento e com o diagnóstico interno junto. Agora, quando a lacuna é só a definição, o levantamento feito (ex.: contagem por situação, com o nome de cada uma) vai com a pergunta, marcado como ainda sem a classificação pedida, e só a frase da pergunta aparece.
- Ensaio real seguinte (970ca9b): o 2º plano já contou só os bens da família de módulos, por status com o nome, em todas as filiais. Mas o revisor aprovou a tabela por status para um pedido de categorias que ninguém definiu. Nova checagem determinística na revisão (`classificacao_sem_definicao`): várias entregas de categoria respondidas por uma tabela agrupada pelos códigos de um cadastro viram levantamento + pergunta. Se a definição já veio no esclarecimento, a correção pede uma contagem filtrada por categoria.
- Testes em `tests/test_m154_descoberta_restrita.py`:
  - o 1º plano conta todos os bens, a revisão aponta o problema e o 2º conta só os módulos pela família;
  - um módulo com duas movimentações é contado uma vez;
  - o literal tolerante é aceito no SQL Server;
  - a pergunta de definição vem com o total comprovado e a resposta curta retoma o pedido.
- Limite conhecido: a situação “hoje” derivada do último evento vigente de cada unidade (movimentação com retorno/cancelamento) ainda não é calculada; vale a situação registrada no cadastro.
## Mudança M154 — pergunta inédita: descoberta restrita, revisão com evidência do backend e definição perguntada na hora (2026-11-25)
- Caso: “quantos módulos tem hoje na ativa, disponíveis e deslocados” (registro M149 nº 3). A mesma consulta aprendida foi decidida três vezes, “na Ativa” virou categoria de status e o pedido terminou sem resposta.
- Causas encontradas no fluxo real:
  - com as tabelas da descoberta em mãos o esquema inteiro valia: o modelo voltava à consulta reprovada (`preparar_consulta`) ou respondia sem consultar;
  - `calculo` e `formato` preenchidos com valores vazios em ações que não os usam derrubavam a decisão inteira;
  - a busca no dicionário usava o nome da empresa (“ativa”) como termo e listava centenas de tabelas com “ativo”; um IN com dezenas de tabelas na leitura do dicionário parava no túnel VPN (mesmo caso da M143);
  - o revisor via só títulos, colunas e contagem: não sabia a entidade contada, o significado dos filtros nem o alcance;
  - quando a descoberta criava e executava a consulta, a receita ficava registrada como `aprender_tabela` e não era gravada;
  - reprovar a mesma consulta com outro parâmetro não contava como método esgotado;
  - a pergunta de definição só aparecia no fim, depois de gastar as tentativas.
- Correções gerais (qualquer setor e provedor):
  - descoberta com ações restritas (`EntradaAgente.acoes_permitidas`, também no esquema JSON dos adaptadores Claude e ChatGPT): montar a consulta, pedir outra fonte com motivo (só tabela nova entra) ou fazer uma pergunta de negócio terminada em “?”; fora disso, a próxima tentativa recebe a restrição específica;
  - `calculo` só é lido em `calcular_resultado`; formato desconhecido vira resposta completa; a recusa de campo devolve a correção daquele campo;
  - busca sem o nome da empresa (`IATIVA_EMPRESA_TERMOS`), leitura de tabelas em lotes de 10 e natureza de cada tabela (cadastro, histórico/movimentação, itens/eventos, cálculo/parâmetro);
  - campo de código cujo significado está em cadastro ligado pelo SX9 (ex.: status do bem) aparece ao modelo como `dominio_por_cadastro`;
  - o resultado leva `evidencia` (entidade e natureza, chave, medidas, agrupamento, filtros com o significado do domínio, alcance das filiais) e o revisor confere a classificação por ela, sem linhas do ERP;
  - a decisão que de fato executou substitui a ação externa para receita, revisão e repetição; filtro exato por código entra na receita (busca por texto não);
  - consulta reprovada duas vezes na mesma mensagem não volta com outro parâmetro: muda a fonte ou pergunta;
  - revisão que conclui faltar uma definição de negócio pergunta na hora (sem pedir tabela ou campo). A resposta curta retoma o pedido original (`pedido_esclarecido`); pergunta nova segue como pedido próprio. O esclarecimento fica só na conversa e não vira regra da empresa.
- Na instalação, o dicionário não tem cadastro com “módulo” no nome. Z54/Z55 são tabelas de cálculo. O status dos bens (ST9) é codificado pelo cadastro “Status do bem”, e a situação do ciclo de vida é outro campo.
- Ensaio real transacional (provedor do atendimento, ERP real só leitura, tudo desfeito): a pergunta dos módulos executou em 25 s sobre o cadastro de bens, agrupada pelo status com domínio no cadastro de status, com todas as filiais autorizadas respondendo. O nome de cada status não apareceu porque o campo de descrição (“Desc Status”) não seguia o padrão de nome. Agora o nome também é achado pelo título do campo, e a evidência só cita os nomes de fato exibidos (`nomes_ao_lado`).
- Contar todos os registros de um cadastro de bens não responde por uma família específica (ex.: módulos), e uma categoria pedida pode não existir no cadastro de status.
- Correção geral: para campo com domínio em cadastro ligado pelo SX9 (família, status, tipo), a descoberta informa ao modelo quantos registros do cadastro têm cada termo do pedido no nome (`termos_do_pedido_no_cadastro`). São só contagens, nunca nomes ou códigos, e cadastros grandes não são sondados. O “contém” do `filtro-campo` casa com e sem acento. O modelo restringe a entidade pela família, e a categoria sem registro vira pergunta de negócio.
- Pendente: validação com o modelo real da versão b0f0b8c (sonda de termos nos cadastros). O ensaio parou porque a assinatura do Claude atingiu o limite; não foi falha do ERP nem falta de definição. Falta também a definição da Ativa: quais situações do bem a operação chama de “deslocado” e quais das situações de “disponível” contam como disponíveis.
- Testes: `tests/test_m154_descoberta_restrita.py`, com o caso dos módulos de ponta a ponta (1º plano errado, descoberta, pergunta de definição, retomada, receita reaproveitada sem o modelo com dados atualizados), RH, Comercial e Cobrança, permissão e uma pergunta reservada.
## Mudança M153 — descoberta após revisão de consulta inédita

- Registro M149 inspecionado no servidor autorizado: a revisão rejeitou duas consultas ao mesmo objetivo e a terceira decisão repetiu a consulta. O registro histórico não identifica o modelo da chamada.
- Planos de consulta reprovados que o agente tentaria executar novamente acionam a descoberta existente dentro das permissões e do orçamento. A revisão devolve ao modelo colunas e lacuna, e pode preservar blocos independentes identificados sem ambiguidade. O catálogo é atualizado antes de continuar uma entrega pendente.
- Ensaio transacional com o provedor real confirmou descoberta e revisão, mas o agrupamento da operação não foi comprovado. A descoberta agora aceita esclarecimentos com opções e o encerramento usa a lacuna específica da revisão.
- Os adaptadores Claude e ChatGPT agora identificam o campo inválido de uma decisão estruturada para que o ciclo possa pedir a correção desse campo.
- O ensaio posterior encontrou uma falha de conexão do ERP na triagem; duas falhas de conexão com o dicionário agora geram aviso de indisponibilidade, sem pedir que o usuário reformule uma pergunta válida.
- Testes de regressão em `tests/test_m153_replanejamento.py`, além do fluxo de recuperação em `tests/test_m109_revisao.py`.

## Mudança M152 — pergunta nova não reaproveita resultado antigo; cadastro dividido pela filial base (2026-11-25)

- Caso: “quantos veículos tem a empresa?” trouxe primeiro uma “Soma” sem sentido e, abaixo, a tabela por filial com números acima do real na Matriz.
- Causa 1: o modelo escolheu `calcular_resultado` sobre a resposta anterior da conversa (a tabela errada de antes da M150) e somou a coluna; depois consultou de novo e juntou as duas coisas.
- Correção geral: cálculo sobre o resultado anterior só vale quando a mensagem se refere a ele (“isso”, “desse”, “e a média?”, “acima”…). Pergunta de dados completa (5+ palavras, com “quantos/qual/liste…”, sem “apenas/só” de ajuste) e sem referência, ou repetida na conversa, recebe a observação para consultar de novo (`agente.pergunta_nova`). O resultado antigo nunca volta como resposta nova.
- Causa 2: a M150 dividia pela filial atual (`DA3_FILATU`), que muda com a viagem. A filial dona do registro é a base.
- Correção: `campos_filial_do_registro` põe a filial base primeiro, depois a atual e por último viagem/previsão. Registros sem filial base aparecem como “Sem filial informada”, e a premissa cita os outros campos.
- Testes: `tests/test_m150_cadastro_compartilhado.py`.

## Mudança M151 — destaque não quebra com a coluna Filial da própria consulta (2026-11-25)

- Caso: depois da M150, “quantos veículos tem a empresa?” lia o ERP e devolvia a pergunta para a caixa de texto, sem resposta. O registro de erros (M149) mostrou `IndexError` em `servico._destaque`.
- Causa: a checagem da coluna “Filial” comparava os rótulos do compilado (tupla) com uma lista, e o resultado era sempre “diferente”. Com a filial vinda da consulta (`DA3_FILATU`, M150), a coluna era deslocada de novo e o índice passava do fim da linha.
- Correção: comparação por tupla, que vale para qualquer consulta com a coluna Filial vinda do banco. Teste em `tests/test_m150_cadastro_compartilhado.py`.

## Mudança M150 — cadastro compartilhado dividido pela filial do registro e resposta só com número apenas quando pedida (2026-11-25)

- Caso: “quantos veículos tem a empresa?” mostrou o mesmo total em todas as filiais e, depois, só um número solto (a soma das 8 repetições). A DA3 (Veículos) é compartilhada: o `DA3_FILIAL` fica em branco, então cada consulta “por filial” lia o cadastro inteiro.
- Correções gerais (qualquer tabela e setor):
  - Execução por partição física real (xFilial): tabela compartilhada ou por empresa não é consultada nem somada de novo para cada filial com a mesma partição.
  - Cadastro compartilhado consultado em todas as filiais é dividido pelo campo de filial do próprio registro, achado pelo dicionário (mesmo prefixo, título de filial, tamanho do campo filial; “atual” antes de “base”, ex.: `DA3_FILATU`, depois `DA3_FILBAS`). Fica restrito às filiais autorizadas, mais a linha “Sem filial informada”, e a premissa diz o campo usado e os outros disponíveis. Nada muda quando o pedido já agrupa ou filtra por esses campos, quando há categorias ou top N, ou quando o setor não vê o campo.
  - O formato “direto” (só o número) vale apenas quando a pessoa pede (“apenas o valor”, “só o número”, “sem tabela”) ou tem preferência guardada nesse sentido; senão a resposta vem completa, com tabela e premissas.
  - Campo oculto da própria tabela base também amplia a consulta aprendida, além do campo de tabela ligada (M142/M143).
- Testes: `tests/test_m150_cadastro_compartilhado.py`.

## Mudança M149 — registro de todo erro da IA para corrigir (2026-11-24)

- Pedido de Diego: sempre que a IA der algum tipo de erro, guardar a pergunta do usuário, a resposta da IA e o erro que precisa corrigir.
- Nova tabela `ErroResposta` (admin “Erros da IA”), preenchida num único ponto: ao gravar qualquer resposta da IA (chat, continuação, retomada). É erro: recusa/bloqueio, falha de execução ou do modelo, fora de escopo, impedimento de dados, “Não terminei esta resposta”. Não é erro: resposta com dados, pergunta ao usuário e recusa de dado pessoal protegido.
- “O que corrigir” traz o tipo, a trilha e o motivo do pedido acompanhado, as entregas pedidas e o que o modelo decidiu em cada passo (ação, consulta, texto curto da resposta dele e as observações do sistema). O modelo nunca vê linhas do ERP: nada do ERP entra no registro.
- Também viram erro: falha inesperada do atendimento (só o tipo da exceção e o local no código, nunca a mensagem) e resposta marcada como incorreta pela pessoa (com o comentário).
- Consulta: `manage.py erros_ia` (abertos), `--todos`, `--corrigido ID...`; no admin, a TI marca “corrigido”. Os registros saem junto com a conversa no expurgo.
- Testes: `tests/test_m149_registro_de_erros.py`.

## Mudança M148 — recusa antiga no histórico não impede nova consulta (2026-11-24)

- Causa de “quantos veículos tem a empresa?” terminar em “Não terminei esta resposta. Tentei: responder sem executar”, já com o acesso corrigido (M147): a conversa tinha as duas recusas anteriores (“A fonte não é autorizada…” e “Sem permissão de consulta…”) e o pedido de veículos continuava em estado “Bloqueada”. A IA lia isso como falta real de permissão e respondia sem executar (5 passos de “conversar”, trilha do objetivo 175).
- Correção geral (qualquer pergunta e setor):
  - no histórico enviado à IA, recusas e falhas do próprio sistema viram um aviso neutro (“não concluída; o acesso é conferido a cada execução; execute de novo”). Respostas com dados, perguntas e recusa de dado pessoal protegido ficam como estão;
  - pedido bloqueado ou com falha aparece para a IA como “Aguardando nova tentativa”;
  - regra no prompt: nunca responder “sem permissão”/“não consigo” por causa de recusa anterior sem antes executar.
- O acesso continua conferido no backend a cada execução. Testes: `tests/test_m148_historico_sem_recusa_antiga.py`.

## Mudança M147 — T.I com acesso total não é barrado pelo setor da pergunta (2026-11-24)

- Causa de “quantos veículos tem a empresa?” voltar “Sem permissão de consulta ou sem filiais autorizadas neste setor.”: a pergunta foi classificada como do setor “operacoes” e `limitar_consulta` restringiu o pedido a esse setor. O pedido da consulta de veículos (já aberto antes no setor TI) foi reaproveitado e, fora do escopo, a permissão da TI deixou de valer. Nada mudou no dicionário: a fonte DA3 passou a ser classificada como “operacoes” quando a consulta nova foi criada.
- Regra de Diego: quem tem o cargo T.I não deve ser barrado em consulta. Correção geral:
  - `authz.acesso_total` (perfil T.I/Diretoria com consulta e as mesmas filiais em todos os setores ativos);
  - com acesso total, `limitar_consulta` não restringe o pedido ao setor classificado;
  - a execução de consulta aprendida não exige que o setor do pedido conste na classificação da fonte.
- Os demais usuários continuam limitados como antes. Testes: `tests/test_m147_ti_sem_barreira.py`.

## Mudança M146 — leitura transversal não barra por reclassificação da fonte (2026-11-24)

- Causa de “quantos veículos tem a empresa?” responder “A fonte não é autorizada para o setor desta solicitação.”: a consulta aprendida da DA3 foi criada como leitura transversal (classificação inconclusiva; quem pediu lê todos os setores) e liberada para todos os setores. O pedido abriu na TI. Logo depois a fonte foi reclassificada só para “operacoes” (a assinatura da classificação muda quando surge uma receita nova que usa a tabela), e a execução exigia que o setor do pedido estivesse na classificação da fonte.
- Correção: em receita de leitura transversal, já reconferida a cada execução (dono, conversa individual, mesmo acesso em todos os setores), o setor do pedido não precisa constar na classificação da fonte. Receitas comuns continuam com a regra antiga. Teste: `tests/test_m146_transversal_reclassificada.py`.

## Mudança M145 — investigação aceita junções em 2 saltos (2026-11-24)

- Causa: `agent/diagnostico.py` (Auditor.ler) conferia os campos de ligação de toda junção na tabela base (`e1`). Com junção encadeada da M143 (ex.: SD2 → SF2 → SA3), o campo F2_VEND1 era procurado na SD2 e a investigação recusava com “Um campo da investigação não está autorizado”, sem motivo real.
- Correção: a conferência usa a tabela de origem da junção (`origem_alias`), como o compilador e o serviço. Teste: `tests/test_m145_diagnostico_dois_saltos.py`.

## Mudança M144 — listas nominais com colunas juntadas de título genérico e pedidos mistos contagem + lista (2026-11-24)

- Causa de "me informe agora com os nomes das pessoas" e "Quantos funcionários temos ativos hoje, em cada filial, me diga os nomes das pessoas e a função" terminarem em "Não terminei esta resposta":
  1. No dicionário do Protheus (`SX3`), campos descritivos de tabelas auxiliares juntadas usam título genérico (`SRJ.RJ_DESC` e `SQ3.Q3_DESCSUM` têm título `"Descricao"`). `planejamento.conferir` passava somente os rótulos exibidos (`res.colunas`) para `entregas.faltam`, sem os nomes técnicos dos campos do dicionário, então `"Descricao"` não era reconhecida como a coluna de função pedida no contrato (`campos_pedidos = ["nome", "funcao", "filial"]`), mesmo com a consulta já trazendo os nomes e as funções de todas as filiais.
  2. Em pedidos compostos que pedem contagem e lista nominal na mesma mensagem, quando o modelo omitia `atende` (ou apontava índice trocado), `registrar_entregas` não marcava a etapa concluída nem orientava o passo seguinte entre lista (sem `agregacao`) e contagem (`agregacao = contagem:*`).
- Correções gerais (qualquer tabela e setor):
  - **Metadados técnicos das colunas no resultado (`colunas_meta`):** `query/servico.py` registra `{campo, rotulo}` para todas as colunas (base, junções, filial e nomes resolvidos de cadastros), e `planejamento.conferir` + `entregas.py` validam `campos_pedidos` também pelo campo técnico do dicionário (`CAMPOS_POSITIVOS`), sem aceitar códigos puros (`RA_CODFUNC`) no lugar da descrição.
  - **Rótulos claros em tabelas juntadas:** títulos genéricos (`"Descricao"`, ou `"Nome"` repetido entre tabelas) recebem o nome da tabela juntada do dicionário (ex.: `"Descricao (Funções)"`), evitando ambiguidade na tela e no revisor.
  - **Ampliação automática de colunas pedidas (`garantir_campos`):** se uma consulta aprendida ainda não traz uma coluna nominal/descritiva pedida no contrato (ex.: função ou cliente), o sistema acrescenta sozinho o campo da própria tabela ou da tabela ligada pelo `SX9` (1 ou 2 saltos) antes da execução.
  - **Coordenação de pedidos mistos (contagem + lista):** `registrar_entregas` reconhece automaticamente a entrega quando há uma única pendente compatível (sem nunca marcar duas perguntas distintas pela mesma tabela), ajusta `partes_faltam` quando o contrato já cobriu todas as partes e orienta explicitamente o próximo passo entre cálculo e lista nominal.

## Mudança M143 — motor geral: junções em 2 saltos, agrupamento multi-dimensão, lotes VPN e cache de catálogo (2026-11-24)

- Causa de "Quanto cada vendedor vendeu em outubro, por filial?" terminar em "O ERP ou a IA não responderam a tempo":
  1. O modelo enviou `agregacao = "soma:D2_TOTAL por F2_VEND1"` com `filial-analise = TODAS` sem `"por filial"`, seguindo uma instrução ambígua do prompt; o backend somou todas as filiais juntas e tirou a coluna `Filial`.
  2. `query/nomes.py` (`_LOTE = 200`) enviou 96 códigos de vendedores num único `IN (...)`, ultrapassando o MTU efetivo (~1400 bytes em UTF-16LE TDS) do túnel VPN `tun0` e travando 30 s (`HYT00`) em cada tentativa, sem trazer os nomes da `SA3`.
  3. O revisor reprovou a 1ª tentativa (faltavam filial e nome); na 2ª tentativa, `juncoes_possiveis` só enxergava 1 salto (`SD2 → SF2`), sem alcançar 2 saltos (`SD2 → SF2 → SA3`), e o prazo de 180 s esgotou dentro da 2ª revisão, descartando o resultado.
- Correções gerais (qualquer tabela e setor, sem regras específicas por pergunta):
  - **Junções SX9 em até 2 saltos:** `aprendizado.py`, `plano.py`, `compilador.py` e `servico.py` suportam junções encadeadas (`origem_alias` / `via`, ex.: item → cabeçalho → cadastro) com fechamento transitivo e validação AST intacta.
  - **Agrupamento multi-dimensão e preservação de "por filial":** `ler_grupos_agregacao` aceita até 3 dimensões (`por CAMPO1, CAMPO2`, `por mes`, `por filial`, separados por `por`, `,` ou `e`), instruções do agente alinhadas e `_calculo_implicito` preserva `por filial` quando pedido junto de outra dimensão.
  - **Filtros dinâmicos em tabelas ligadas e desigualdade:** `filtro-campo` aceita `!=`/`<>` além de `=`, `~` e `@`, e amplia automaticamente campos de tabelas ligadas pelo `SX9` (1 e 2 saltos).
  - **Desempenho e rede VPN:** `_LOTE = 30` em `query/nomes.py` (evita descarte de pacotes grandes no túnel VPN) e cache por sessão em `metadata/catalogo.py` (elimina releituras repetidas de `SX2/SX3/SIX` e `INFORMATION_SCHEMA` entre filiais).
  - **Resiliência no fim do prazo:** quando restam menos de 10 s (ou esgota na revisão com continuações desligadas), `revisao.py` entrega o resultado já validado pelo contrato em vez de descartá-lo.

## Mudança M142 — agrupar por campo de tabela ligada; sem a lista "Ainda falta" (2026-11-24)

- "Quanto cada vendedor vendeu em outubro, por filial?" terminou em "Seu pedido tem 1 partes. Ainda falta…".
  - A IA montou a consulta nos itens da nota (SD2), que não têm vendedor. O vendedor fica no cabeçalho (SF2.F2_VEND1).
  - A junção SD2→SF2 era recusada porque a chave do SF2 também tem FORMUL e TIPO.
  - A IA listou as notas em vez de somar por vendedor, e o tempo da mensagem acabou.
- Correções gerais:
  - "por CAMPO" aceita campo de tabela ligada pelo dicionário (SX9). A consulta ganha sozinha a junção e a coluna (ex.: "soma:D2_TOTAL por F2_VEND1"). Vale para qualquer dimensão: vendedor, grupo do produto, natureza, segmento do cliente.
  - Ligação pelo início da chave do outro lado e pela maior parte dela passa a ser aceita (nota → cabeçalho). A execução continua conferindo no banco se a ligação repete registros e recusa se repetir.
  - Quando o campo não existe, a recusa devolvida ao modelo lista os campos das tabelas ligadas que ele pode usar.
  - Instrução ao modelo: nunca entregar lista quando a pessoa pediu "quanto por alguém"; montar a agregação.
  - Busca dos nomes (M140) limitada a 10 s e ao tempo restante da mensagem.
- Removida a lista "Seu pedido tem N partes / Ainda falta" (era o aviso da manutenção em segundo plano, desligada).
- Revisão dos objetivos sem resposta dos últimos 3 dias: os demais casos eram a pergunta dos fornecedores antes da M138/M139.

## Mudança M141 — colunas curtas no resumo de contagens lado a lado (2026-11-24)

- "Quantos funcionários foram desligados e admitidos em setembro, por filial" mostrava colunas com o título inteiro de cada contagem ("Contagem de funcionários admitidos em setembro/2026, por filial").
- Agora cada coluna mostra só o que a diferencia das outras. O que é igual em todas (contagem, funcionários, o mês, por filial) já está na pergunta e sai: "Admitidos" e "Desligados".
- Mês ou ano diferente entre as colunas fica depois do evento: "Admitidos - Setembro", "Desligados - 2024".
- Se não houver diferença clara, os títulos ficam como estavam.

## Mudança M140 — código sempre acompanhado do nome do cadastro (2026-11-24)

- Pedido de Diego: o ranking de fornecedores mostrava só o código (C7_FORNECE). Toda resposta com um código que tem nome deve trazer também o nome, ao lado.
- Regra geral (qualquer tabela, qualquer setor): cada coluna de código ligada a um cadastro pelo dicionário (SX9) ganha a coluna "Nome (<rótulo>)" ao lado. Exemplos: C7_FORNECE→SA2 (A2_NOME), E1_CLIENTE→SA1 (A1_NOME), produto→SB1 (descrição).
- O nome é buscado depois da consulta principal, só para os códigos exibidos, numa leitura do cadastro pelo mesmo compilador e validador. A consulta principal não muda, então contagens, somas e o ranking continuam iguais.
- O nome segue a visibilidade do setor. O destaque da resposta diz "código - nome". Se a busca falhar, a resposta sai só com o código.
- Não repete: consulta que já traz um campo de nome (ex.: RA_NOME) ou já junta o cadastro fica como está.

## Mudança M139 — ranking geral de todas as filiais sem o teto de exibição (2026-11-24)

- Depois da M138 a IA criou a consulta de "10 fornecedores com mais pedidos de compra em 2026", mas o ranking foi recusado com "o número de grupos ultrapassou o limite de leitura".
- Causa: no ranking geral cada filial devolve todos os seus grupos (fornecedores) para somar entre filiais antes do top N. Essa leitura usava o teto de exibição de tabela (500 linhas); com mais de 500 fornecedores numa filial, o ranking era recusado.
- Agora a leitura agregada dos grupos usa um teto próprio (IATIVA_CONSULTA["limite_grupos"] = 20000). São só totais por grupo, não registros.
- Sem top N, a tela mostra os primeiros 500 grupos do ranking e avisa. Acima de 20000 grupos por filial a recusa continua.
- Vale para qualquer ranking "por X" de todas as filiais (clientes, produtos, fornecedores, vendedores).

## Mudança M138 — a IA monta a consulta nova em vez de travar (2026-11-24)

- Causa (log da M137) de "10 fornecedores com mais pedidos de compra em 2026" sem resposta:
  1. A IA achou as tabelas certas (SC7, SCY, SC1) e mandou os campos ao modelo. O modelo pediu "aprender_tabela" de novo em vez de montar a consulta, e o ciclo repetia até o limite.
  2. Uma resposta do modelo fora do formato era repetida 3 vezes com a mesma entrada, sem dizer o que estava errado, e o pedido parava como "a IA não respondeu a tempo".
- Correções gerais (qualquer pergunta, qualquer setor):
  - com as tabelas em mãos, se o modelo não montar a consulta, recebe uma segunda chance com a instrução explícita de criar a consulta com uma delas;
  - resposta recusada volta ao modelo com o campo recusado e as regras do formato, e o log registra o campo;
  - validação tolerante no que não muda o sentido: texto livre longo e listas longas (colunas, filtráveis, colunas da junção, opções, parâmetros) são cortados, e operador "=" e "<>" vale como igual/diferente;
  - continua recusado o que mudaria o sentido ou a segurança: campo extra (ex.: SQL), operador de filtro desconhecido, código de objetivo ou parâmetro fora da lista.

## Mudança M137 — motivo de "aprender_tabela" sem consulta vai para o log (2026-11-24)

- "Quais são os 10 fornecedores com mais pedidos de compra em 2026?" parou depois de 4 tentativas de criar consulta, sem dizer onde. O motivo de cada tentativa só ia para o modelo.
- Agora cada tentativa sem consulta grava no log do app: o alvo e o motivo (tabelas candidatas, "não serviram" com a ação que o modelo devolveu, fonte inconclusiva, recusa do validador). Só nomes de tabelas e mensagens do sistema; nenhum dado do ERP.
- Próximo passo: repetir a pergunta e corrigir a causa de forma geral.

## Mudança M136 — respostas incorretas chegam ao T.I em "Memória e aprovações" (2026-11-24)

- Nova seção "Respostas para verificar" em "Memória e aprovações", visível só para o T.I (quem administra), com contador no menu.
- Cada cartão mostra:
  - quem enviou, o setor e quando;
  - a pergunta feita e o motivo informado;
  - a consulta usada, a tabela, os critérios (filial, período, cálculo) e as colunas da resposta;
  - o link para abrir a conversa, quando o T.I pode vê-la.
- Botão "Marcar como verificada"; as verificadas ficam abaixo, em verde.
- Menu da avaliação: "Sim" e "Não" ficam ao lado do ✓ e do ✕. Os textos agora dizem "o T.I".

## Mudança M135 — avaliação da resposta no estilo do botão de copiar (2026-11-24)

- "Esta informação está correta?" virou um botão-ícone ao lado do copiar, como o de copiar.
- Ao clicar abre um menu com "Sim" e "Não".
  - "Sim" registra a confirmação.
  - "Não" abre uma janela para a pessoa explicar o que não está correto, com o botão "Enviar para a T.I".
- Depois de avaliar, o ícone fica verde (correta) ou âmbar (enviada à T.I). Clicar de novo permite trocar a avaliação.

## Mudança M134 — "Esta informação está correta?" ao lado do copiar (2026-11-24)

- Nova opção em toda resposta com tabela: "Esta informação está correta?".
  - "Sim, está correta" registra a confirmação.
  - "Não, enviar para a TI verificar" registra a resposta, com um comentário opcional (ex.: filtro geral, faltou critério).
- A TI vê as avaliações no admin ("Avaliações de respostas"), com a pergunta, o comentário e o filtro por correta/verificada, e marca como verificada depois de achar o motivo.
- Guarda só o voto e o comentário; nenhum dado do ERP.
- Conferência de "pedidos de compra em aberto, por filial": a consulta aprendida (SC7) exclui pedidos encerrados e com resíduo eliminado e conta pedidos distintos por filial. A contagem direta no banco bateu com cada filial exibida. Exigir quantidade entregue menor que a pedida não muda o resultado nesta base. Ficaram de fora as filiais do grupo 002, fora das filiais da pessoa.

## Mudança M133 — manutenção desligada; a IA entrega na hora; animação corrigida (2026-11-24)

- Pedido de Diego: nada vai para a manutenção nem fica trabalhando em segundo plano. A IA acha a tabela do Protheus e entrega.
- Servidor:
  - manutenção automática desligada (MANUTENCAO_ATIVA, MANUTENCAO_PUBLICAR e IATIVA_MANUTENCAO_AUTOMATICA = 0);
  - disparo e varredura da manutenção desativados no systemd;
  - continuação em segundo plano desligada (IATIVA_AGENTE_CONTINUACOES=0);
  - o ciclo em primeiro plano ganhou mais passos e tempo (8 passos, 180 s). Backup do .env anterior no servidor.
- Código: com a manutenção desligada não se abre registro de correção nem se pede para avisar a TI. A IA entrega ou diz o que tentou.
- Causa de "pedidos de compra em aberto, por filial" não ser entregue: a contagem distinta por filial rodava no banco, mas a conferência recusava porque só olhava a medida da contagem simples. Agora vale a função que o banco calculou (contagem, contagem distinta, soma, média).
- Animação: a sincronização trocava a lista de mensagens no meio da digitação, e o Windows com efeitos desligados desativava a animação. Agora toda resposta nova digita, e a que estava digitando retoma.

## Mudança M132 — aprendeu a consulta e já entrega; resposta aparece digitando (2026-11-24)

- "Quantos pedidos de compra estão em aberto, por filial?": a IA aprendia a consulta (SC7) mas parava. A consulta aprendida tem data e a pessoa não citou período; a pergunta do sistema ficava escondida pelo texto do modelo ("vou montar…").
- Agora:
  - consulta aprendida sem período na mensagem roda com todo o histórico até hoje (mesma regra de entregas) e a resposta chega na hora;
  - quando falta mesmo um dado da pessoa, aparece a pergunta do sistema, nunca só o texto do modelo.
- Interface: a resposta da IA aparece aos poucos, com cursor, no estilo ChatGPT. Tabelas e botões surgem quando o texto termina. Sem animação para quem pede movimento reduzido.

## Mudança M131 — "quantos" e "quais são" na mesma pergunta viram duas tabelas (2026-11-23)

- A leitura do pedido (analise.tabela) diz também se a pessoa pediu quantidade (`contagem`) e se pediu quem são (`lista.pedida`), com as colunas pedidas (`lista.campos`).
- Como fica:
  - "Quantos … e quais são" = duas tabelas: primeiro a lista ("Quem são", com Evento e Período quando há mais de um) e por último a quantidade, que é a tabela menor;
  - "Quais são … Me informe apenas os nomes" = só a lista, só com os nomes, sem repetir quem aparece em mais de um evento ou período.
- As duas tabelas usam as mesmas consultas aprovadas e as permissões da pessoa. Lista maior que o limite de exibição mostra os primeiros registros e avisa.
- Sem a leitura do modelo, as regras do texto ("quais/quem/liste/nomes", "apenas os nomes") servem de reserva.

## Mudança M130 — a IA entende o jeito da pessoa pedir a tabela (2026-11-23)

- A tabela personalizada (M129) dependia de frases exatas ("por filial", "admitidos e desligados", nomes de mês). Agora a 1ª leitura do pedido (o modelo) devolve `analise.tabela`:
  - medidas: o evento contado e o rótulo da coluna;
  - períodos: início, fim e rótulo; mês sem ano é do ano corrente;
  - agrupamento: filial, dito de qualquer jeito ("separado por filial", "em cada unidade", "nas filiais");
  - se é interseção.
- O backend só valida (datas, limites) e executa com as consultas aprovadas que a pessoa pode usar. Exemplo: "Quem entrou e quem saiu de julho a setembro, separado por filial?" sai como Filial | Entraram - 07/2026 … | Saíram - 09/2026.
- Um evento em 3 ou mais meses também vira tabela; 3 ou mais eventos num período, também.
- Sem a leitura do modelo, as regras do texto servem de reserva, só para a grade evento x período (status listados não viram eventos).
- Se a tabela não puder ser montada (evento sem consulta, célula com erro), o contrato volta às entregas originais e o ciclo normal responde. A entrega "tabela" não manda mais à manutenção o que a IA já sabe consultar.
- A tabela já planejada não é substituída pelo replanejamento (comparação de 2 meses, detalhes de contagem).

## Mudança M129 — tabela personalizada com consultas que a IA já sabe fazer (2026-11-23)

- "Quantos funcionários foram admitidos e desligados em setembro e outubro, por filial?" ia para a manutenção (registro nº 32), embora cada evento já tenha consulta aprovada. Faltava montar a tabela.
- Novo `iativa/agent/tabela.py`:
  1. lê os eventos citados juntos ("admitidos e desligados", "admitidos, transferidos e desligados"), os períodos (2 ou mais meses/anos; sem ano = ano corrente) e o agrupamento (por filial);
  2. acha a consulta aprovada de cada evento pelo campo de data, entre as que a pessoa pode usar (prefere a sem filtro fixo; "desligados" = "demissão");
  3. conta no banco cada evento em cada período, pelo caminho autorizado;
  4. monta uma tabela: Filial | Admitidos - 09/2026 | Admitidos - 10/2026 | Desligados - 09/2026 | Desligados - 10/2026, com o total de cada coluna no rodapé.
- Não chama o modelo nem a manutenção. O contrato vira uma entrega só, e só essa tabela completa a cumpre.
- Se algum evento não tiver consulta, o ciclo normal decide.
- Um período só continua com a composição de contagens separadas (resumo + detalhes dos vínculos). Interseção ("no mesmo ano") continua uma contagem única.

## Mudança M128 — contagem de dois meses na mesma tabela (2026-11-23)

- "Quantos funcionários foram desligados em setembro e outubro, por filial?" voltava um total único dos dois meses juntos, sem dizer de qual mês era cada valor. A tabela de dois meses (só faturamento/total) não reconhecia perguntas que começam com "quantos/quantas".
- Agora a contagem usa a mesma consulta nos dois meses e mostra uma tabela com a filial (pelo nome, não pelo código) e uma coluna por mês, com o total de cada mês no rodapé.

## Mudança M127 — contagem feita no banco é resposta, por qualquer caminho (2026-11-23)

- "Quantos funcionários ativos temos hoje por filial?" ficou em "Ainda falta…".
  - A consulta certa rodou (funcionários ativos, contagem por filial), mas veio por aprender_tabela, sem o parâmetro agregacao na decisão, e a evidência foi lida como consulta, não como cálculo.
  - Na continuação, uma falha passageira do modelo na triagem encerrou o pedido com "Não consegui confirmar o assunto".
- Agora:
  - o resultado diz se calculou no banco (`agregado`), e isso basta como evidência de cálculo, em qualquer caminho (consulta, aprender tabela, receita);
  - na continuação, falha do modelo na triagem é passageira: o pedido segue depois, limitado pelo teto de continuações.

## Mudança M126 — andamento diz o que está fazendo; mês sem ano é do ano corrente (2026-11-23)

- Andamento: a tela mostrava sempre uma de 9 frases fixas por fase. Agora mostra o passo real do sistema, por exemplo "Consultando o ERP — Matriz" ou "Calculando o total no banco".
  - Continua sem texto livre do modelo e sem nome interno de consulta ou código de tabela.
- Período: mês citado sem ano ("faturamento de setembro e outubro") é do ano corrente, inclusive mês que ainda não chegou ("dezembro" = dezembro de 2026). "Desde dezembro" em março continua sendo o ano passado, para o intervalo não ficar invertido. A regra também está nas instruções do modelo.

## Composição de pedidos com consultas já conhecidas

- Tabelas de valores agregados por filial mostram no rodapé os totais aditivos conferidos no banco. Em comparações, cada período tem seu total e a variação geral é calculada a partir desses totais.
- Pedidos de um indicador em dois meses nomeados, como o faturamento de setembro e outubro, usam os meses da mensagem e mostram os valores lado a lado na mesma tabela. A definição da consulta e os filtros valem igualmente para os dois meses.
- Um pedido com várias entregas tenta reaproveitar a receita completa. Sem ela, combina componentes verificados cuja descrição, métrica e critérios correspondem à entrega. Cada consulta é reexecutada com as permissões atuais.
- As demais entregas seguem pelo planejamento normal. Resultados de tentativas que ainda não comprovam nenhuma entrega ficam fora da resposta.

## Reexecução de consultas conhecidas após falha temporária

- O incidente 31 começou com tempo esgotado na consulta de admissões e foi resolvido na versão ativa em novo ensaio. Agora o atendimento retoma a receita no worker quando um passo conhecido encontra falha temporária, antes de seguir para outras ações.
- Variações da pergunta anual de RH com “demitidas”, “desligadas” e “pessoas” compõem as duas contagens pelas datas de cada evento com as consultas já aprovadas.

## Leitura dos resultados no chat

- A primeira mensagem registrada já coloca a nova conversa na barra lateral. O progresso mostra frases curtas sobre o pedido, sem abrir etapas internas.
- Colar uma tabela no campo de mensagem preserva o texto inteiro. Se a mensagem ultrapassar o limite de envio, o chat mostra o aviso antes de enviar.
- Recarregar a conversa abre na última mensagem. Quando uma verificação carrega a tabela depois, o chat mantém quem está no fim junto da última mensagem e preserva a leitura de quem subiu.
- Ao entrar ou voltar para um chat pela navegação, a conversa abre na última mensagem após estabilizar o conteúdo.
- Um único botão de cópia no rodapé permite copiar a resposta inteira ou escolher uma tabela pelo nome. A cópia usa texto com colunas separadas por ` | `, incluindo cabeçalhos, campos, linhas e totais disponíveis.
- A Memória abre na área do chat e organiza preferências pessoais, a fila com propostas completas e as regras e consultas aprovadas por setor.
- Ao concluir um pedido, o chat ancora a mensagem em leitura. A seta acima do campo aparece quando há mensagens mais abaixo e volta para a mais recente.
- A rolagem fica dentro do histórico e das tabelas. O feedback acessível da cópia fica no bloco de ações e já não amplia a altura da página.
- O chat tem títulos por assunto gerados na primeira triagem, cabeçalho com quebra de linha, grupo “Ontem” pela última mensagem enviada e campo de texto que cresce mantendo a posição de leitura.
- Respostas têm controles de copiar e refazer abaixo do resultado. A cópia inclui tabelas e totais, e a reexecução de uma resposta com várias consultas volta ao pedido que a originou. As mensagens e o andamento no chat dispensam o mascote e os avisos repetitivos.
- Tabelas de consulta têm altura máxima, cabeçalho fixo e rolagem própria. Ao atualizar a conversa em uso, o chat preserva a posição de leitura, a rolagem das tabelas e os detalhes abertos. O estilo acompanha a atualização da conversa para alcançar abas já abertas.

## Respostas com detalhes e resumo lado a lado

- Admissões e desligamentos por filial saem em dois indicadores separados, com os vínculos autorizados na mesma tabela de detalhes e totais no fim. Um pedido seguinte de apenas um número preserva o período.
- Contagens verificadas com a mesma dimensão e o mesmo escopo podem ser reunidas em uma tabela. As permissões de origem de cada campo continuam valendo ao restaurar no histórico.

## Admissões e desligamentos em separado

- Uma resposta curta de separação conserva o ano e a divisão por filial do pedido de interseção no mesmo chat. Com duas consultas aprovadas, o atendimento computa cada evento com a sua data de referência e verifica as duas entregas antes de publicar.

## Manutenção com ensaio contextual e receitas com várias entregas

- Antes de editar, o executor ensaia o pedido individual na versão ativa com as permissões do autor e as mensagens anteriores ao pedido. A conversa é descartada pela transação. Uma falha de ambiente interrompe o ciclo de edição e um pedido já atendido volta ao chat sem publicação.
- O atendimento faz uma busca autorizada de fonte antes de tratar uma capacidade de consulta como ausente. Pode guardar e reaproveitar uma sequência de consultas que comprovou todas as entregas, com conferência de acesso em cada uso.
- A candidata passa pelo teste novo, pela reprodução e pelo ensaio antes da suíte completa.

## Mudança M125 — contrato fiel ao pedido, falha do executor diagnosticada, receita verificada e tempos por etapa (2026-11-23)

- Diagnóstico nº 24 ("Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?"). Causas comprovadas:
  - Planejamento: a segunda leitura (M118) mandava separar cada evento e juntar a interseção e uma lista nominal. O pedido virou 4 entregas. A resposta certa (interseção por filial) entregava 1, o ciclo repetia a ação e abria incidente (trilha `preparar_consulta:entrega_pendente:calculo` → `acao_repetida`). Reproduzido com dados sintéticos: a versão anterior devolve "Seu pedido tem 4 partes… Ainda falta".
  - Manutenção (23/11, 08:11–09:03): duas tentativas terminaram em "tempo limite" de ~19 min cada (o agente tem só 240 s; o estouro foi em teste, imagem ou ensaio, sem registro de qual). A 3ª passou nos testes, mas o ensaio cobrava o mesmo contrato de 4 partes. O agente editou de fato (contagem.py + 3 testes); não era executor sem ferramentas.
  - Log do executor com buffer: todas as linhas apareciam no journal às 09:03.
  - Validado no servidor depois da 1ª publicação (teste.ti): o contrato já saía com 1 entrega e os parâmetros estavam certos (dois intervalos, contagem por filial). Mas a consulta aprendida tem junção com a tabela de funções (só descrição), e a prova da interseção só era registrada sem junção. Por isso a entrega nunca contava como cumprida. Reproduzido com dados sintéticos (consulta com junção).
- Correções:
  - `agent/fidelidade.py`: o contrato fica fiel ao pedido, qualquer que seja a leitura do modelo (na triagem, na continuação e na retomada).
    - "X e Y no mesmo ano/mês", "ambos" ou "os dois" = uma entrega (a interseção).
    - Lista só quando pedida. Preferência guardada vale só para contagem simples, não para agregado por filial nem interseção.
    - Prompt da 2ª leitura e nota do sistema alinhados.
  - Executor:
    - Tempo limite com o nome da etapa e duração de cada etapa no log; log sem buffer.
    - Falha do executor (login, cota, rede, imagem, ferramenta, agente que não editou) separada da falha da correção. Não gasta tentativa: o incidente volta à fila (`manutencao aguardar`), espera 10 min e é retomado; após 6 falhas seguidas, vai para a TI.
    - Agente que estoura o tempo mas editou tem a edição validada pelos testes.
  - Plano: no cálculo (agregação), junção que não entra em coluna nem filtro sai, como já valia no total (`plano_total`). A contagem fica na unicidade da tabela base, sem inflar nem cortar.
  - `agent/receita_pedido.py`: resposta conferida grava a receita do tipo de pedido. Contém assinatura, consulta, tabelas, parâmetros com {ANO}, métrica, evidência e versão, sem dados do ERP. Num pedido equivalente, ela é o 1º passo sem chamar o modelo, se a pessoa tiver acesso à consulta. Se for reprovada, é desativada.
  - `agent/tempos.py` + `manage.py tempos_pedido`: tempos por etapa (leitura, decisão, ERP, revisão, total, desde o pedido na continuação/retomada) na ficha da resposta e no log.
  - Tela: "Correção automática (registro nº X)", "Continuação da consulta" e "Correção no ar: refazendo" aparecem com nomes distintos; a espera do executor também aparece.
- Testes: `tests/test_m125_contrato_fiel.py` e `tests/test_m125_executor.py`.
  - Cobrem: 4 entregas viram 1; lista pedida continua; ponta a ponta com filial/quantidade; receita reaproveitada sem o modelo e de outro ano; receita fora do acesso; revisor fora não publica.
  - Executor: diagnóstico específico, tempo por etapa, fila de espera.

## Mudança M124 — registro cancelado ou bloqueado não trava o mesmo pedido para sempre (2026-11-19)

- O pedido "admitidos e desligados no mesmo ano" chegou a 3 de 4 partes e terminou com "Não vou repetir a mesma tentativa". O motivo: a mesma pergunta cai no mesmo registro, e esse registro (nº 26) tinha sido cancelado pelo botão parar. Registro cancelado ou bloqueado por "voltou depois da correção" ficava fora da correção para sempre.
- Agora:
  - o cancelamento vale só para aquele pedido: uma nova ocorrência reabre o registro com as tentativas zeradas;
  - o bloqueio por "voltou depois da correção" cai quando a versão do código mudou.
- O ensaio (M120) segura publicações erradas.

## Mudança M123 — ensaio da correção com o usuário de teste da TI (2026-11-19)

- O ensaio (M120) criava a conversa temporária na conta de quem perguntou. A pessoa via um chat novo com a pergunta e o campo travado, porque a mensagem era enviada por outro processo.
- Agora o ensaio roda com `teste.ti` (`IATIVA_ENSAIO_USUARIO`), que tem acesso a todos os setores. A conversa temporária fica só nessa conta e é apagada no fim.
- Quando o agente de manutenção sai com erro ("código 1"), o executor registra o fim do erro no log da manutenção, para diagnóstico.
- A trava de chat ocupado é por conversa; outro chat da mesma pessoa continua respondendo.

## Mudança M122 — ensaio roda o segundo plano; card mostra a tentativa e o motivo (2026-11-19)

- Incidente #25: o ensaio (M120) contou como falha um pedido que só precisava continuar em segundo plano ("continua: passos"), porque no ensaio não há worker. A correção voltou para "Implementando", sem explicação no chat.
- Agora:
  - o ensaio roda ali mesmo as continuações do pedido (até 4), como o worker faria para a pessoa;
  - quando falha, o motivo para o agente é a lista de partes que não saíram ("não entregou: X (entregou 1 de 2)"), em vez de um código interno;
  - o card mostra "Tentativa N de 3. A anterior ainda não entregava o seu pedido: …";
  - quando a estimativa estoura, o card diz "Está levando mais que o previsto; continuo trabalhando nisso.".

## Mudança M121 — pedidos com várias partes andam mais por rodada e o andamento fica claro (2026-11-19)

- A segunda leitura (M118) gerou 6 entregas (uma lista por evento). Com 5 passos por rodada, a IA entregava uma ou duas partes e empurrava o resto para o segundo plano repetidas vezes, com textos longos "Entregue: …; Não concluído: …".
- Agora:
  - os passos por rodada acompanham o tamanho do pedido (cerca de 2 por entrega, até 14);
  - a segunda leitura pede UMA lista de quem foi contado, com as datas de todos os eventos, e não uma lista por evento;
  - a mensagem de andamento vem em lista: "Já tenho X de Y partes", com cada parte entregue (✓) e cada parte que falta (•), seguida de "Sigo trabalhando nas partes que faltam, em segundo plano".

## Mudança M120 — a manutenção ensaia o pedido real antes de publicar (2026-11-19)

- O registro nº 24 publicou uma correção que passou nos testes sintéticos. O pedido refeito caiu na manutenção de novo.
- Agora, depois dos testes e antes de publicar, o executor:
  - constrói a imagem candidata com a correção;
  - refaz o pedido que abriu o incidente, como a própria pessoa e numa conversa temporária (apagada no fim), com o ERP real e o revisor de entrega (`manage.py manutencao ensaiar`, `IATIVA_ENSAIO=1` não abre incidente).
- Só publica se o ensaio entregar o que foi pedido. Se não, o motivo volta para o agente na próxima tentativa (até 3), na mesma cópia. O chat mostra a etapa "Ensaiando o seu pedido".

## Mudança M118 — o pedido é lido duas vezes antes de agir (2026-11-19)

- "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?" entregou só a interseção (72), sem a contagem de cada evento nem quem foi contado.
- Agora, depois da 1ª interpretação, a IA faz uma SEGUNDA leitura obrigatória (`planejamento._segunda_leitura`, modo `reler_pedido`): relê a pergunta inteira contra a 1ª lista de entregas e devolve a lista completa. Regras:
  - cada evento citado separado ("admitidos e desligados" → duas contagens), mais a interseção quando o texto também puder significá-la;
  - contagem de pessoas ou registros vem com a lista de quem foi contado, salvo "só o número";
  - mantém período, filiais e agrupamento.
- O contrato completo é o que o revisor (M109) cobra antes de enviar. Falha ou resposta inválida na 2ª leitura mantém a 1ª.

## Mudança M117 — "no mesmo ano" não é verificação; busca em lote aceita nome completo; verificação vazia não é entrega (2026-11-19)

- "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?" virou verificação de vínculos de 255 pessoas, toda inconclusiva, e encerrou sem a contagem. Três causas gerais:
  - "foram ... no mesmo ano" era lido como "a mesma pessoa" (detector de verificação). Agora "mesmo ano/mês/período" é recorte de tempo;
  - a busca em lote recusava o nome completo: 70 posições preenchidas com espaço passavam do limite de 40, e nomes acentuados também eram recusados. Agora o limite é 100 caracteres, com letras acentuadas e ainda sem aspas, ponto e vírgula ou comentário;
  - verificação que termina sem ninguém verificado agora falha, replaneja e, sem saída, vai para a correção automática; antes contava como concluída.
- As correções #13, #14, #16, #20, #21, #22 e #23, feitas pela manutenção no servidor, foram integradas ao GitHub. As de #21/#22 tratavam a pergunta como verificação; foram ajustadas para a exigência de cálculo, e os testes delas seguem passando.

## Mudança M116 — tudo que a IA não entrega vai para a correção automática (2026-11-19)

- "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?" terminou com "as tabelas encontradas não serviram; não é falta de função; reformule". Duas causas:
  - a causa "metadado/tabelas que não serviram" encerrava sem manutenção (M67);
  - pedidos de admissões, listas e comparações de períodos eram excluídos da manutenção como "capacidade existente" (M61).
- Agora só a falta de permissão (limite por função) encerra sem correção. Todo o resto que o ciclo não entrega vai para a correção automática; o "reformule o pedido" deixa de existir.

## Mudança M115 — manutenção com permissão para corrigir regras de pedido (2026-11-19)

- Diagnóstico do incidente #20 ("compare setembro e outubro por filial: contratos assinados e locações iniciadas"):
  - antes do 657ad3a, o agente ChatGPT rodava sem ferramentas e saía em ~13 s com "nenhuma alteração";
  - depois, ele corrigiu, mas a trava recusou porque tocava iativa/agent/comparacao.py, uma das regras de pedido travadas.
- Pedido de Diego (permissão total para corrigir no servidor):
  - ficam travados só segurança e acesso: accounts, auditoria, manutenção, credenciais/conexões, a conexão somente leitura com o ERP, o validador de SQL e os testes de segurança;
  - planejamento, comparação, contratos, entregas, memória e descoberta podem ser corrigidos, sempre com teste novo, suíte e revisão da TI depois;
  - limite por correção: 12 arquivos / 600 linhas;
  - o agente Claude tem shell completo dentro do container isolado;
  - a imagem ganhou git e ripgrep para os agentes analisarem o código.

## Ferramentas de manutenção do ChatGPT (2026-11-19)

- Os registros recentes terminavam sem alteração porque o Codex CLI respondia sem ferramentas. O container agora instala o executável auxiliar da mesma versão e habilita o modo de execução apenas para a manutenção. Uma falha de inicialização das ferramentas tem diagnóstico próprio.
- Depois de uma nova ocorrência numa versão posterior, um diagnóstico que falhou pode voltar à fila dentro do limite de reabertura. Pedidos de cálculo com critérios inválidos ou ciclo estagnado após consultar seguem para manutenção. A espera por uma dependência transitória fica no atendimento em segundo plano.

## Revisão antes de exibir comparações incompletas (2026-11-19)

- Pedidos com entregas pendentes ficam em segundo plano sem publicar a tabela intermediária. A comparação de contratos assinados e locações iniciadas exige uma contagem mensal por filial para cada evento, com o período e a data de referência conferidos.
- Uma falha transitória continua no worker. Um cálculo inválido persistente pode abrir manutenção mesmo quando a descoberta da fonte teve uma falha anterior. Investigações de divergência concluídas e consultas disparadas por resposta direta também passam pela revisão.

## Ranking percentual de clientes entre meses (2026-11-19)

- O diagnóstico 19 mostrou uma comparação cujo contexto se perdeu na confirmação do ano. O atendimento preserva o pedido, calcula os dois meses pela mesma consulta e ordena as quedas percentuais antes de aplicar o limite de clientes. Uma tabela intermediária fica fora da resposta enquanto faltar o ranking.

## Contagem por prazo de desligamento (2026-11-19)

- O diagnóstico 18 mostrou uma contagem comum para um pedido com prazo. Quando a base autorizada contém as duas datas, o cálculo usa o intervalo do vínculo e o limite explícito. Se faltar uma data na consulta, o agente recebe a instrução de completar a base antes de contar.

## Correção de pedidos acompanhados (2026-11-19)

- O executor revisa a mudança isolada em até três tentativas, usando a falha da validação anterior para orientar a próxima. O novo teste precisa falhar na versão afetada e passar na candidata, junto com a suíte.
- Uma nova correção publicada pode retomar o mesmo pedido. O chat exibe uma estimativa por etapa e o revisor respeita a diferença entre pedir a quantidade e pedir uma lista nominal.

## Compatibilidade do atendimento ChatGPT com Codex 0.167 (2026-11-19)

- O app-server rejeitava o parâmetro legado de leitura antes da classificação do pedido. A sessão de atendimento agora usa um perfil de permissões nomeado, com leitura da pasta temporária e rede desabilitada para ferramentas.

## Mudança M114 — parar cancela a correção automática e desfaz o que publicou (2026-11-19)

- Botão parar em pedido que espera a manutenção: se nenhum outro pedido espera o mesmo incidente, a correção é cancelada (incidente bloqueado "Cancelada pela pessoa", sai da fila).
- O executor do host interrompe o agente em até 2 s (container com nome, `docker kill`) e checa o cancelamento entre as etapas. Se já publicou, faz `git revert` do commit dela na main local e publica de novo (azul/verde). `manage.py manutencao concluir` não sobrescreve o cancelamento.

## Mudança M113 — revisor pragmático e com o total; reprovação após correção volta à manutenção (2026-11-19)

- O revisor reprovava respostas certas. Exemplos: "admitidos que saíram antes de 90 dias" com 41 de 72 ("filtro não aparece") e "demitidos nos últimos 8 meses" com o total geral do banco ("falta o total"). Ele só via a frase de cálculos.
- Agora o revisor recebe a frase-resumo de todo bloco (total geral do banco, resumo do cálculo), sem destaque de grupo e com nomes trocados por [nome]. A instrução pede para reprovar só quando a resposta claramente não responde, nunca por apresentação ou para pedir confirmação.
- Revisão reprovada depois de uma correção publicada volta à manutenção (antes ia direto para a TI); limites de tentativas/reaberturas e o diário seguram laço.
- Incidente #15 encerrado (falso negativo). Correções #13/#14 da manutenção seguem só no servidor, aguardando revisão da TI.

## Mudança M112 — manutenção entende "revisão reprovada" (2026-11-19)

- Incidente #12 (revisão reprovada) terminou "não reproduzido" em 32 s. O agente de manutenção recebia a instrução de não mexer quando "o modelo escolheu mal", e a descrição do que faltava chegava cortada em 300 caracteres.
- Agora, para a causa `revisao_reprovada`, a instrução diz que conduzir o modelo até a entrega certa É o defeito a corrigir, de forma geral (orientação em objetivos/agente, escolha de receita, cálculo), e como reproduzir (FakeProvider com `revisoes`). A descrição da capacidade vai inteira, até 600 caracteres.

## Mudança M110 — revisão reprovada não trava o assunto; IA aprende com a correção (2026-11-19)

- Depois da M109, refazer o pedido calculava sobre a resposta anterior juntada (vários blocos), dava "não misture blocos" e encerrava com "não é falta de função; reformule". Três correções gerais:
  - cálculo sobre resposta com vários blocos usa o bloco que tem as colunas pedidas (`calculos._bloco`);
  - critério recusado que persiste (ERRO_PARAMETROS) vai para a correção automática, não para "reformule" (substitui a M67 neste ponto);
  - reprovada e depois aprovada: a IA guarda na memória como corrigir (nota tipo correção; da empresa para administradores, do usuário para os demais), para o próximo pedido parecido sair certo.

## Mudança M109 — a IA confere a resposta antes de enviar (2026-11-19)

- Pedido de Diego: entender o pedido inteiro, montar a resposta em segundo plano, conferir se é o que foi pedido, tentar de outro jeito e, se não conseguir, cair na correção automática até entregar a resposta certa.
- `iativa/agent/revisao.py`: quando o ciclo acha que terminou, o revisor (mesmo modelo, modo `revisar_entrega`) recebe o pedido, o contrato e SÓ metadados do que foi montado (títulos, colunas, nº de registros, medida, critérios, resumo do cálculo; nunca linhas). Há também uma checagem determinística sem custo (cálculo de prazo sobre base sem a data final).
- Reprovada: nada é enviado, o motivo vira observação e o ciclo tenta de outro jeito (`IATIVA_REVISAO_TENTATIVAS`, padrão 2). Esgotado: causa `revisao_reprovada` vai para a manutenção automática (M107) com o que faltava. A retomada passa pelo revisor de novo. Desligável por `IATIVA_REVISAO=0`.
- O cálculo `intervalo_entre_datas` (correção #10 da manutenção, agora no GitHub) devolve também QUEM atende (identificação, datas e dias), não só o número.

## Mudança M108 — prazo entre dois eventos exige cálculo; etapa não corta a frase (2026-11-19)

- "Quantos admitidos em 2026 saíram antes de completar 90 dias?" entregava a lista de admitidos como resposta. Agora pedido com condição de tempo entre dois eventos (admissão→saída, vencimento→pagamento) exige o cálculo `intervalo_entre_datas`. Lista não conta como entrega; sem a capacidade, cai na manutenção automática (M107).
- O texto das etapas ("Vou buscar…") era cortado em 160 caracteres; agora vai até 400.
- Prazo entre eventos: a consulta base é a do PRIMEIRO evento citado (admissão), preferindo receita sem filtro de ativos; regra enviada ao modelo desde a 1ª decisão; contagem por intervalo cumpre item "contagem" do contrato. Receita nova no servidor: "Funcionários admitidos (inclusive desligados)".
- Manutenção: descarta edições em STATUS/DECISOES/PLANO feitas pelo agente (antes recusava a correção). Primeira correção real publicada só no servidor: incidente #10, operação `intervalo_entre_datas` em calculos.py (commit local, aguardando revisão da TI: `git log origin/main..main`).

## Mudança M107 — correção automática só no servidor (2026-11-19)

- Pedido de Diego: quando a IA esgota as formas nativas, a manutenção corrige o código no próprio servidor e entrega a resposta, sem GitHub e sem interromper ninguém.
- `MANUTENCAO_PUBLICAR=servidor` (scripts/manutencao.py): a correção validada (teste de regressão novo, suíte, travas de caminhos) vira commit na main LOCAL de /opt/IAtiva. `scripts/publicar_local.sh` constrói a imagem e troca a cor (azul/verde). Depois o pedido é refeito no mesmo chat.
- Padrão do agente de manutenção: Opus, esforço baixo, 25 turnos (a escolha da tela Conexões Claude prevalece). Reprodução e suíte rodam em paralelo.
- `scripts/atualizar.sh` reaplica as correções locais sobre a main do GitHub (rebase); conflito mantém a versão no ar e registra no log.
- A TI revisa (`git log origin/main..main` no servidor) e envia ao GitHub manualmente.

## Mudança M106 — legenda aprendida para campo de legenda dinâmica (2026-11-18)

- A SX3010 não grava os nomes do FP0_STATUS (X3_CBOX chama LOCA001CI). Diego informou a legenda. Ela fica como nota aprovada da empresa (`iativa/agent/legendas.py`). O catálogo a usa no lugar da rotina dinâmica, e a contagem por status sai como "código - nome".

## Mudança M105 — contagem por campo de legenda dinâmica não repete a consulta (2026-11-18)

- "contratos de outubro por status" (FP0_STATUS, legenda montada pela rotina LOCA001CI) entregava os códigos, marcava "não concluído" e repetia a mesma consulta em segundo plano. Agora a contagem agrupada reconhece a legenda dinâmica: entrega por código, explica que o nome não fica no banco e pede a legenda uma vez para guardar.

## Mudança M104 — tabela vazia na instalação é fonte errada (2026-11-18)

- Contratos: a CN9 (contratos padrão) está vazia nesta instalação; o pedido dava "0 linhas". Agora zero linhas de consulta aprendida cuja tabela está vazia (estatística do SQL Server, sem ler dados) volta à descoberta sem repetir a tabela. Teste: `tests/test_m104_fonte_vazia.py`.

## Mudança M103 — pedido abre no setor da receita (2026-11-18)

- Receita aprendida liberada só para outro setor (ex.: contratos no Comercial) era recusada quando quem pedia é de outro setor com acesso a ele; o pedido agora abre no setor da receita em que a pessoa tem acesso.

## Mudança M102 — continuar o pedido quando o usuário indica as tabelas (2026-11-18)

Uma indicação curta de aliases no mesmo chat retoma a pergunta anterior do mesmo autor. A descoberta inspeciona os aliases após autorização. Uma fonte inconclusiva retorna ao ciclo para pesquisar alternativas. O domínio literal e a legenda calculada pelo Protheus têm tratamentos distintos. Se faltar uma legenda dinâmica, a tabela agrupada por código é parcial e o pedido continua pendente de esclarecimento. O esquema enviado aos provedores inclui período, filial e agregação durante a descoberta, inclusive quando o setor ainda não tem receitas. Cobertura em `tests/test_descoberta_contratos.py`.

## Mudança M102 — leitura de fonte com setor inconclusivo (2026-11-18)

- Concluído o trabalho do Codex: TI/Diretoria com direitos iguais em todos os setores leem a fonte sem setor confirmado (receita pessoal, reconferida a cada execução). Testes: `tests/test_m102_leitura_transversal.py`.

## Mudança M101 — descoberta e contagem de entidades por situação (2026-11-18)

- O executor diferencia ausência no catálogo, classificação inconclusiva e acesso negado. Os dois primeiros motivos voltam ao ciclo de descoberta. O setor e as restrições de campos são reconferidos na execução.
- O índice público inclui índices e relacionamentos. O catálogo local fornece a chave e os códigos literais do SX3. A contagem distinta por filial verifica identificadores ausentes e estados conflitantes antes de concluir a distribuição.
- A primeira execução completa registra no catálogo a operação reutilizável e a assinatura dos metadados locais. O Markdown é gerado em `var/receitas/<codigo>/`. A referência pública e a receita aprendida têm versões independentes.
- Validação sintética em `tests/test_descoberta_contratos.py`, inclusive reutilização em outro processo Django. A correspondência dos códigos reais da Ativa continua dependente do dicionário e das regras da instalação.

## Mudança M100 — agrupar por campo e por filial (2026-11-18)

- Agregação "por CAMPO por filial" mantém cada filial com os seus grupos (não soma as filiais); a participação sai por Filial · grupo. Teste: `tests/test_m100_grupo_por_filial.py`.

## Mudança M99 — não travar por fonte sem setor nem por termo desconhecido (2026-11-18)

- Fonte sem setor herda o setor da tabela ligada ou fica com a TI (provisória); sem "escolha uma tabela" para quem a IA ensina sozinha; termo que não é campo vira o campo mais provável da tabela em uso, com a premissa dita. Teste: `tests/test_m99_sem_travar.py`.

## Mudança M98 — participação de cada grupo no total (2026-11-18)

- "Taxa de ocupação" calculava sobre a lista crua cortada (3626 de 63922) e desistia. Novo cálculo de participação: contagem/soma agrupada no banco + % de cada grupo no total; orientação ao modelo para agrupar antes e, sem definição de qual grupo conta, entregar a distribuição completa.

## Mudança M97 — setor Operações (2026-11-18)

- Setor Operações criado; ST9 classificada nele. Teste no servidor: "disponibilidade atual dos bens da ST9" trouxe 3626 bens em 18 s; "taxa de ocupação dos ativos" agrupou por status e perguntou qual status significa ocupado (definição de negócio).

## Mudança M96 — opções de filial completas e tempo esgotado sem erro 500 (2026-11-18)

- As opções do parâmetro mostram até 12 (8 filiais + "Todas as filiais"; antes cortava em 6).
- Tempo total esgotado durante a ação (ex.: aprendendo tabela nova, "taxa de ocupação dos ativos") virava erro 500 e apagava a mensagem; agora segue o caminho do prazo (continua em segundo plano) e há rede de segurança no envio.
- ST9 (Bens) aprendida, mas sem setor dono: a fonte não executa até definir o setor (pendente de Diego).

## Mudança M95 — memória técnica do Protheus e receitas persistentes (2026-11-18)

- Índice local da memória técnica (versão por hash, atômico, web e worker), busca obrigatória antes de toda execução, descoberta pela referência confirmada no dicionário local, receitas em Markdown no volume. Prova com dados sintéticos em RH (desligados), Comercial (clientes bloqueados) e Financeiro (movimentos bancários, tabela fora da referência): `tests/test_m95_*.py`. Documentação: `docs/MEMORIA_TECNICA.md`.

## Mudança M94 — parar vale na hora; busca vazia tenta a descrição (2026-11-18)

- Parar apaga na hora a mensagem ainda sem resposta e destrava o chat; o que a IA responder depois (presa no ERP) é descartado.
- Busca por nome sem resultado tenta uma vez o mesmo texto como "contém" na coluna de descrição da consulta.

## Mudança M93 — dicionário do ERP: nova tentativa na hora (2026-11-18)

- A checagem da tabela de consulta aprendida tenta de novo uma vez antes de dizer que o ERP não respondeu (falha intermitente da conexão ao dicionário).

## Mudança M92 — busca por nome vai para o campo de pessoa (2026-11-18)

- A consulta "Solicitações de compra com observação" buscava o nome na descrição do produto. Criação automática passa a preferir campo de pessoa (solicitante, cliente, fornecedor) para a busca por nome; a consulta existente foi corrigida no servidor.

## Mudança M90–M91 — filtro "contém" por coluna (2026-11-18)

- M90 (busca em todas as colunas de texto com OU) foi revertida: no SQL Server, 8 colunas em OU estouraram 180 s (1–2 colunas respondem na hora).
- M91: o filtro por campo aceita "contém" (`CAMPO~texto`, sem diferenciar maiúsculas) e todas as colunas de texto da consulta podem ser filtradas; a IA filtra a coluna onde o texto aparece (ex.: descrição do produto), uma coluna só.

## Mudança M89 — conversa abre na última mensagem (2026-11-18)

- Ao trocar de conversa ou abrir a página, a rolagem vai para o fim de novo depois que tabelas e mascote terminam de montar.

## Mudança M88 — login sempre na página principal (2026-11-18)

- Ao entrar, o sistema ignora o endereço guardado e abre a página principal sem conversa. Conversa excluída ou sem acesso leva à página principal em vez de erro.

## Mudança M87 — endereço de envio aberto no navegador volta à conversa (2026-11-18)

- Sessão expirada durante um envio levava, depois do login, a `/c/<id>/mensagem/` (só POST) e mostrava HTTP 405. Agora as ações do chat abertas pelo navegador levam à conversa (ou à página inicial).

## Mudança M86 — aniversariantes liberados para o RH (2026-11-18)

- Consulta "Aniversariantes" (SRA, só ativos, setor RH) aprovada; período vira mês de qualquer ano. Teste no servidor: "liste os aniversários desse mês pelo setor de RH" trouxe 53 funcionários em 14 s. Teste: `tests/test_m86_aniversario.py`.

## Mudança M84–M85 — continuação do assunto e dado protegido (2026-11-17)

- "tem observação do item X?" depois de listar compras: setor válido + mesmo assunto da conversa não pede esclarecimento.
- "aniversários do mês" exige data de nascimento (dado protegido): resposta imediata com o motivo, sem ciclo de minutos. Testes: `tests/test_m85_protegidos.py`, `tests/test_m82_explorar.py`.

## Mudança M83 — setor Compras e busca por nome sem diferenciar maiúsculas (2026-11-17)

- Setor Compras criado; acesso para diego.pessoa e teste.ti; teste.ti virou administrador da TI. SC1 classificada em Compras. Busca por nome usa `UPPER(campo) LIKE` (havia 49 solicitações com o nome pedido e a consulta trazia 0).

## Mudança M82 — IA procura no dicionário o assunto que não conhece (2026-11-17)

- "Solicitações de compra" (sem setor Compras) agora vai para a busca no dicionário: a IA achou a SC1 e montou a consulta "Solicitações de compra" sozinha (teste no servidor com teste.ti). Como teste.ti só consulta a TI, a consulta ficou como proposta para a TI aprovar (M81). O que a IA aprende vira nota na memória. Testes: `tests/test_m82_explorar.py`.

## Mudança M81 — etapa 4: conhecimento por domínio e TI aprova fontes (2026-11-17)

- Nota de setor vale para quem consulta o domínio; "Faturamento" virou nota da Cobrança. Consulta nova só vira oficial com aprovação da TI; fora da TI fica proposta e a IA avisa. Testes: `tests/test_m81_etapa4.py` e fixtures com administrador da TI.

## Mudança M80 — ERP lento: tenta de novo sozinho (2026-11-17)

- Pedido #57: o ERP demorou na primeira consulta (tempo esgotado) e a IA desistiu pedindo para tentar de novo; o dicionário respondia normalmente logo depois (0,4 s). Agora tempo esgotado ou falha passageira seguida de ação repetida continua sozinha em segundo plano e responde no chat. Teste: `tests/test_m80_erp_lento.py`.

## Mudança M79 — mensagem e "trabalhando" sobrevivem ao recarregar (2026-11-17)

- A mensagem da pessoa é gravada antes de a IA trabalhar (transação própria); se o envio for parado ou falhar, ela sai. Ao recarregar ou voltar à conversa, o pedido em andamento aparece com o "trabalhando", as etapas e o botão de parar; a resposta entra sozinha quando fica pronta. Testes: `tests/test_m79_envio_salvo.py`.

## Mudança M78 — campo bloqueado enquanto a IA responde (2026-11-17)

- Durante a resposta o campo de mensagem fica desabilitado ("Aguarde a resposta ou clique em parar."); volta ao terminar, ao parar ou em erro.

## Mudança M77 — etapa 3: envio idempotente, um pedido por chat, fila justa (2026-11-17)

- Mesmo envio repetido não roda de novo; chat ocupado recusa outro pedido; worker reivindica com trava do chat; fila justa com até 2 pedidos por pessoa ao mesmo tempo. Testes: `tests/test_m77_concorrencia.py` (as travas do PostgreSQL não rodam no SQLite dos testes).

## Mudança M76 — continuação curta herda a contagem anterior (2026-11-17)

- "e demitidos?" depois de "quantos funcionários foram admitidos em agosto?" vira a mesma pergunta com o assunto trocado (mesmo período, lista pela preferência guardada). Geral para qualquer contagem (`contagem._trocar_assunto`).

## Mudança M75 — etapa 2 concluída: contagem com detalhe é regra geral (2026-11-17)

- `contagem.py` substitui o atalho de admissões: vale para qualquer consulta aprovada com campo de data; lista só quando pedida ou por preferência na memória; cada contagem cumprida pela consulta do seu evento. Testes: `tests/test_m75_contagem_geral.py` e os de admissões ajustados para usar a preferência guardada.
- Itens da etapa 2 do prompt de melhorias concluídos (M63–M75). Próximo: etapa 3 (fila justa, idempotência).

## Mudança M74 — etapa 2: período/filial por entrega e pergunta de definição (2026-11-17)

- Entrega com período ou filial diferentes do pedido fica pendente; o modelo recebe o motivo e refaz (`planejamento.divergencia`).
- Termo sem definição aprovada no glossário vira pergunta objetiva antes de consultar (`iativa/agent/definicoes.py`).
- Falta da etapa 2: trocar os atalhos por frase por regra geral. Testes: `tests/test_m74_etapa2.py`.

## Mudança M73 — manutenção desligada; um agente por chat (2026-11-17)

- Manutenção automática desligada por padrão (decisão M73): sem correção automática; a resposta pede para comunicar alguém da TI com o nº do registro.
- Worker com 6 agentes em paralelo; um chat com pedido rodando não é pego por outro agente. Testes: `tests/test_m73_manutencao_desligada.py`.

## Mudança M72 — ajustes da exclusão e da troca de conversa (2026-11-17)

- Botão Cancelar da confirmação visível (cor própria). Ao trocar de conversa, a lateral mantém a posição da barra de rolagem.

## Mudança M71 — excluir conversa e troca de conversa sem recarregar (2026-11-17)

- Lixeira ao lado do lápis na lateral; confirmação "Excluir conversa?" (Cancelar/Excluir). Só o dono exclui a própria conversa individual; mensagens, resultados e pedidos dela vão junto. Rota `c/<id>/excluir/` (POST).
- Clicar em outra conversa troca só a área da conversa (htmx), sem recarregar a página; voltar/avançar do navegador funcionam. Memória e Ctrl+clique continuam abrindo a página normal.

## Mudança M70 — texto de cima estável na consulta por filial (2026-11-17)

- Durante "Consultando o ERP — filial", junções e total no banco, o passo atual mostra "Consultando no banco…" (sem trocar a cada filial); o detalhe por filial continua na seta.

## Mudança M69 — pedido misto fecha quando todas as entregas conferem; etapas recolhíveis (2026-11-17)

- Pedido #52: comparação e lista foram entregues no 2º passo, mas a exigência "cálculo" do 1º passo barrava o fim; o pedido repetia a ação e terminava com "O cálculo pedido ainda não foi feito". Agora, com várias entregas, todas conferidas = cumprido.
- Tela: etapas ficam atrás de uma seta ao lado do passo atual (abre/fecha); a lista não é mais cortada (servidor guarda até 80 passos).

## Mudança M68 — lista de pedido misto exige os campos dela (2026-11-17)

- Segunda parte (pedido #51 travado e incompleto): a comparação só roda no passo que é dela (antes a consulta de admissões virava "comparação julho x agosto" de contagens); o contrato tira os períodos separados ("Faturamento de julho/agosto") e contagens que a pessoa não pediu; a resposta final mostra todas as tabelas entregues em blocos (antes só a última e a lista sumia); o "agora vou buscar..." das etapas anteriores sai da resposta (já apareceu no andamento).
- Tela: passos do andamento sem repetição e até 12 visíveis; a resposta nova aparece palavra a palavra e as tabelas surgem em seguida (também na resposta em segundo plano). Comparação avisa "Calculando 07/2026 em …".

- Em pedido misto (comparação + lista), a entrega de lista passa a exigir os campos da própria descrição (ex.: nome e função). Antes, a tabela da comparação por filial era aceita como a lista e o pedido fechava como cumprido sem os funcionários.
- Teste: `tests/test_comparacao_m66.py::test_lista_do_pedido_misto_nao_e_cumprida_pela_comparacao`. 942 testes passam. Falta revalidar no chat.

## Mudança M67 — estado por entrega e causa da falha (2026-11-17)

Etapa 2 do prompt de melhorias (restante), validada no chat com pedido misto (comparação de faturamento + lista de admitidos).
- Limite/pausa mostra "Entregue: …" e "Não concluído: …" por entrega do contrato, em vez de mensagem genérica.
- Trilha guarda a causa real de cada passo (parâmetro inválido, metadado/ERP, permissão, transitória), não só "entrega pendente". Pedido que para por essas causas encerra com o motivo e NÃO abre manutenção (antes um parâmetro inválido repetido virava "capacidade ausente").
- "por D2_FILIAL" (campo de filial da tabela) vale como "por filial"; na comparação, "por mês" e agrupamento duplo são normalizados e os períodos são lidos só na oração da comparação (uma segunda data em outra pergunta da mensagem não desfaz a comparação).
- Já existente e conferido: contrato de entregas por pedido (M60) com conferência por entrega e continuação até todas comprovadas; herança de critérios só no mesmo assunto.
- Pendente desta etapa: verificação de período/filtros por entrega (hoje confere tipo, medida e campos), pergunta objetiva quando falta definição de negócio, e troca dos desvios por regex (admissões, "função deles", "quanto") por classificação geral.

## Mudança M66 — comparação geral de dois períodos (2026-11-17)

Etapa 2 do prompt de melhorias (parte da comparação).
- Comparação deixa de ser só faturamento/SD2/D2_TOTAL/por filial: qualquer consulta aprovada com campo de data e uma medida (soma, contagem, média, máximo, mínimo), com o agrupamento pedido (filial, cliente, CFOP, total) e a mesma definição nos dois períodos. Períodos: dois meses, "este mês × mês passado", dois anos, "este ano × ano passado".
- Sem medida escolhida pelo modelo, só usa a soma quando a consulta tem uma única medida numérica; senão o ciclo normal decide (não inventa definição). Ranking ("top N") não é comparado.
- Estados distintos: período sem registros (zero em soma/contagem; sem valor em média), grupo ausente em um período, base zero (variação indefinida), período em andamento (marcado "parcial" com premissa), período cortado (recusa), falha de um período (devolve a falha, não compara com vazio). Cancelamento é conferido entre os dois períodos.
- Herança de critérios em continuações já era por assunto (mesma consulta; busca e cálculo não herdados) — conferido, sem mudança.
- Testes: `tests/test_comparacao_m66.py`; suíte 937 passando.

## Mudança M65 — defeitos reproduzidos na auditoria de 17/11 (2026-11-17)

Base: `AUDITORIA-IATIVA-2026-11-17.md` (fornecida por Diego) e o prompt de melhorias. Etapa 1 do prompt concluída; etapas 2–5 pendentes.

- Validador AST (segunda barreira): exclusão lógica (`= ' '` exato) e filial agora precisam ser termos de AND no nível de cima, na tabela certa (WHERE da base, ON da junção), contra literal/parâmetro. Recusados: filial dentro de OR sempre verdadeiro, NOT, valor de exclusão errado, autoigualdade, filtro de outra tabela, TOP 0. O SQL do compilador continua aceito.
- Pedidos mistos: comparação de faturamento troca só a entrega de comparação e preserva as outras perguntas; lista com "calcule/diferença/percentual/variação/projeção" deixa de apagar o cálculo.
- Coluna parecida não é prova: "Valor original" não atende saldo, "Nome abreviado"/fantasia não atende nome; quando o campo do dicionário é conhecido, ele decide (E1_SALDO, RA_NOMECMP). Títulos a vencer/posição rotulam "Saldo em aberto" ou "Valor original" conforme o tratamento.
- "Inativos", "desligados", "não estão ativos" não aplicam mais o filtro de ativos (antes `ativ` casava "inativos").
- Regressões em `tests/test_auditoria_m63.py`: 15 falham no código anterior e passam agora; suíte 933 passando.

## Mudança M64 — entregas detalhadas por setor (2026-11-17)

- Incorpora a correção automática 884d393 que separa lista de funcionários com função atual de histórico individual. A nova verificação exige os campos expressamente pedidos; agregados não satisfazem nome/função/admissão ou cliente/vencimento/saldo. "Por filial" organiza a lista quando há detalhes; indicadores e rankings continuam agregados.
- Comparação de dois meses de faturamento executa a mesma consulta SD2 aprovada com os mesmos filtros nos dois períodos e calcula diferença e variação por filial no backend. Não vira investigação de divergência, não escolhe filtros para atingir um alvo e não envia resultados ao modelo. Base zero e valores indisponíveis são explicitados.
- Pergunta de cadastro de filiais devolve códigos/nomes autorizados na plataforma, com fonte e limite de cobertura explícitos; não apresenta o cadastro de acessos como inventário completo do ERP.
- Adaptação da consulta de RH aplicada para o campo de nome completo já autorizado no dicionário, mantendo os filtros aprovados e a descrição da função. Compilação e AST conferidas antes de salvar.
- Validação local: 916 testes aprovados e 10 pulados por dependências Linux; Django check sem problemas. Implantação azul/verde e conferência real no Chrome seguem antes de considerar a mudança concluída. Nenhuma regra financeira ou permissão foi alterada.
- Publicação f9b8d93 por azul/verde concluída com drenagem. Chrome confirmou uma tabela detalhada de RH preservada ao recarregar, títulos com cliente/vencimento/saldo e comparação com diferença/percentual. Não houve validação de números financeiros. Conferência encontrou bloqueio do snapshot de cadastro de filiais: ajuste identifica exclusivamente esse cadastro e revalida setor/filiais, sem liberar snapshots ERP sem campos; regressão cobre restauração e negativa para outro setor. Ajuste final em validação/publicação.

## Mudança M63 — correções do piloto de TI (2026-11-17)

- Classificação com schema próprio nos provedores Claude/ChatGPT: não exige decisão executável nem parâmetros de consulta durante o planejamento. Falhas técnicas de planejamento passam a ter diagnóstico sem conteúdo do usuário nos logs.
- Continuação curta de período de uma consulta de admissões recupera o assunto e exige novamente total, nomes e datas; novos assuntos explícitos não são substituídos. Permissões e filtros permanecem conferidos.
- URL da primeira conversa é atualizada pelo cabeçalho do servidor, sem habilitar cache de páginas no navegador. Clique de layout envia salvamento imediato com keepalive.
- Regressões sintéticas e suíte completa em validação; publicação azul/verde e repetição dos casos no Chrome seguem antes de considerar a mudança concluída. Sem alteração de regra financeira ou migração de banco.

## Mudança M62 — "qual a função deles?" depois de uma lista (2026-11-16)

- A IA usava o histórico de cargo (feito para uma pessoa) e caía na manutenção, que recusou com razão (escolha errada, não falta de código). Agora pergunta sobre um dado das pessoas da lista anterior refaz a mesma consulta com os mesmos critérios. A consulta aprendida de admissões passou a trazer a descrição da função (junção SRA→SRJ pelo código da função, conferida no dicionário e compilada antes de salvar), no lugar da coluna de cargo que vinha vazia.

## Mudança M61 — admissões, contagem e manutenção indevida (2026-11-16)

- Corrigida regressão M60: planejador só tinha soma/média no contrato de agregação e usava soma para contagem; COUNT já calculado não satisfazia SUM, gerando tentativas e manutenção desnecessárias. `contagem` agora tem schema, operação e verificação próprios; totais de contagem sobrevivem ao snapshot autorizado.
- Pergunta de admissões entrega contagem de vínculos e tabela com nome completo/data de admissão, salvo pedido explícito de apenas o número. Reutiliza consulta aprovada de SRA com filtro de admissão, mantém critérios corporativos/filiais e não agrega por filial para perder os nomes. Período explícito é lido deterministicamente; “este mês” vai do início do mês até hoje em todas as tentativas.
- Contagem completa usa total do banco; amostra cortada sem total e grupos não viram contagem de pessoas. Matrículas distintas não provam pessoas distintas. Pedido misto não troca fonte financeira/comercial por RH; contratação de equipamentos não é tratada como admissão.
- Tentativa que não cumpriu nenhuma entrega substitui a revisão anterior; parágrafos idênticos não são repetidos. A falta de escolha da ferramenta de contar/listar admissões não abre correção de código. Defeitos técnicos continuam com os mecanismos existentes; testes/publicação não são pulados para prometer correção instantânea.
- Contratos antigos de soma em pedidos de admissões são normalizados na continuação. A correção não reescreve respostas antigas nem encerra consultas de outros usuários. Conferência visual sintética em Chrome desktop/celular: uma tabela, nomes e datas preservados, sem transbordamento. Provedor real planejou contagem+lista e respeitou pedido de apenas número; nenhuma linha do ERP usada nessa avaliação.
- "Preso na fila": pedidos diferentes caíam no mesmo incidente (só pelo código), que esgotava as reaberturas e ficava bloqueado, deixando o chat esperando para sempre. Agora cada pedido tem incidente próprio, e o chat só fica em "corrigindo" quando a manutenção vai mesmo atuar; senão encerra com aviso.
- Validado (899 testes) e implantado; sem migração de banco nesta mudança.

## Mudança M60 — inteligência corporativa, memória e conclusão verificável (2026-11-16)

- Diego autorizou os seis ajustes da revisão e confirmou que TI, assim como Diretoria, consulta todos os setores. `preparar_setores_ia` cria RH/Comercial e concede políticas explícitas aos perfis autorizados usando apenas filiais já concedidas; preserva políticas mais restritas existentes. Preparação aplicada somente no servidor-bots, sem alterar ERP ou interruptores da manutenção.
- Planejamento semântico identifica assunto e entregas antes das ações. O backend intersecta o pedido com políticas vigentes; classificação ausente/incerta não libera consulta. Fontes aprendidas também são classificadas por metadados públicos do dicionário, com cache invalidado por assinatura. Não há lista manual obrigatória de tabelas por setor.
- Memória automática exige trecho literal e intenção durável: preferências de apresentação podem ser ativadas; definições/correções ficam propostas. Origem, confiança e setor são registrados. Tela pessoal permite revisar, editar, esquecer e desfazer preferências. Contextos resumidos podem ser recuperados entre chats da mesma pessoa, respeitando acesso atual; resultados do ERP não entram no resumo.
- Exceções pessoais a regras corporativas, filtros fixos e parâmetros obrigatórios dependem de aprovação explícita de Diretoria/TI com permissão de aprovação. A regra original permanece; mudança da regra invalida exceção de filtro antiga. Camadas pessoais legadas contraditórias não contornam o filtro corporativo.
- Cada entrega planejada exige evidência compatível: tabela não comprova dias úteis, soma não comprova projeção, investigação parcial não vira conclusão. Contrato e entregas cumpridas seguem nas continuações do worker. Tipos monetários/quantitativos do dicionário são preservados até cálculo e snapshot.
- Validação: 35 casos novos aprovados; suíte completa com 884 aprovados e 10 pulados no Windows. Implantação segue o fluxo azul/verde com teste Linux adicional e drenagem. Check Django e consistência de migrações aprovados; novas migrações são aditivas. Chrome desktop/celular sem transbordamento. Quatro classificações pelo provedor real no servidor acertaram RH, Comercial, Financeiro e pedido misto, apenas com perguntas sintéticas e nomes públicos dos setores, sem executar consultas. Fontes compartilhadas foram conferidas apenas por metadados; usos de consultas aprovadas complementam o dicionário para reconhecer fontes auxiliares sem baixar o limiar de confiança. Manutenção automática impedida de reescrever as políticas distribuídas de planejamento, memória, aprendizado, escopo e descoberta.
- Limites: classificação semântica ainda pode errar; confiança não substitui políticas. Memória estruturada não treina pesos do modelo. T29 e validação humana das definições financeiras permanecem pendentes; nenhuma definição de faturamento foi alterada nesta mudança.

## Mudança M59 — apresentação monetária dos cálculos (2026-11-16)

- Soma, média e diferença sobre campos monetários preservam tipo dinheiro e usam o formatador brasileiro existente, com R$, agrupamento de milhares e duas casas decimais. Resumo, tabela e resposta direta usam a mesma apresentação.
- Cálculos numéricos usam vírgula decimal; percentual mantém unidade na resposta curta. Snapshot novo preserva formato e tipo ao reabrir. Resultados antigos que perderam a informação de moeda não são convertidos por suposição: precisam refazer a consulta de origem.
- Testes sintéticos cobrem soma/média/diferença monetária, percentual, renderização normal/direta e persistência/restauração sem nova consulta ao ERP.
- Validação: 849 testes aprovados e 10 pulados no Windows; Django check sem problemas. O auxiliar de login dos testes agora aguarda o catálogo assíncrono após autenticar, corrigindo a leitura intermitente antes da conclusão; login de produção não foi alterado.

## Mudança M58 — pensamento visível no indicador de processamento (2026-11-16)

- Olhar baixo com deslocamento de 19 unidades para cada lado, ciclo de 2,8 segundos, balanço do corpo de até 6 graus e pontos com pulsos defasados. Geometria original e pensamento contínuo preservados; movimento reduzido continua respeitado.
- Atualizar o texto do progresso não reatribui um estado visual que já está ativo. Chrome verificou a identidade e o tempo da animação após atualizações repetidas, no mascote clonado de 62px dentro de `.pensando`, além de desktop/celular e estados de repouso/falha.
- Prévia capturada do indicador real do frontend em cenário sintético; nenhuma consulta ao ERP.
- Validação: 846 testes aprovados e 10 pulados no Windows; Django check sem problemas.

## Mudança M57 — olhar de pensamento e transformações mais frequentes (2026-11-16)

- Referência consultada no OpenMausBot: gestos downScan do diretor de animações. Adaptação própria em CSS: olhos menores e baixos percorrem esquerda/direita com piscada e leve movimento do corpo, mantendo a forma original durante consultas.
- Repouso antes de cada transformação reduzido de 90 para 12 segundos; duração de 6 segundos e alternância cabine/módulo preservadas. Processamento interrompe; movimento reduzido e aba oculta continuam respeitados.
- Chrome desktop/celular validou deslocamentos negativos/positivos dos olhos com posição vertical baixa, fases operacionais sem transformação, ciclo de repouso e interrupção. Prévia animada capturada de frames reais do navegador, com dados sintéticos.
- Validação: 846 testes aprovados e 10 pulados no Windows; Django check sem problemas.

## Mudança M56 — mascote com formas da frota e pensamento contínuo (2026-11-16)

- Referência visual fornecida por Diego: cabine azul com teto branco arqueado, porta, ferragens e base escura; módulo branco com faixas azuis e verdes, portas, cobertura e degraus. Desenhos SVG estilizados mantêm os olhos do mascote, sem sombras.
- Consulta, busca, organização, cálculo, escrita e conferência usam somente pensamento. Cartões de tarefa/objetivo/atividade também começam pensando; conclusão mantém corpo original e sorriso, sem virar lavatório.
- Formas da frota restritas à cápsula em repouso: após 90 segundos, uma forma por 6 segundos, alternando cabine e módulo. Processamento/erro/parada interrompem o ciclo. Aba oculta e movimento reduzido não iniciam a transformação.
- Chrome desktop/celular com dados sintéticos validou fases como pensamento, retorno de cabine/módulo ao repouso, interrupção ao consultar, falha, objetivo pendente e movimento reduzido; sem transbordamento horizontal.
- Validação: 846 testes aprovados e 10 pulados no Windows; Django check sem problemas.

## Mudança M55 — consultas sem programação e opções com botão parar (2026-11-16)

- Finalidade restrita a consultar/analisar dados e esclarecer as consultas; programação, SQL livre, scripts e atividades gerais não são entregáveis do chat, inclusive para TI. Pedidos explícitos reconhecidos localmente encerram antes do modelo; instrução compartilhada cobre as demais formulações. Blocos de código e padrões de código na resposta livre do modelo geram recusa fixa antes de executar a ação. Códigos de produtos/filiais continuam sendo dados consultáveis.
- Opções de resposta agora enviam pelo compositor, usando o mesmo identificador de pedido, acompanhamento e botão parar; não iniciam outro envio enquanto há processamento. Chrome com dados sintéticos validou envio único da opção, identificador, estado parar, bloqueio de duplo clique e retorno ao estado enviar ao interromper.
- Validação: suíte completa Windows com 836 aprovados e 10 pulados; após ampliar a recusa genérica, os 13 casos específicos passaram. Django check sem problemas. Classificação semântica continua apoiada no modelo para formulações que não correspondem às proteções locais.

## Mudança M55 — achados do roteiro do piloto (2026-11-16)

- Roteiro rodado pela Claude com o usuário de teste (modelo pago): inadimplência, top 10, por filial, resumo, só o número, vencimentos da semana, faturamento do mês, filial que mais faturou e continuação "e em julho?" certos, em ~18–20 s cada.
- Corrigido: "só o número" de um total mostra o valor em reais (não a contagem); preferência sobre parâmetro ("para mim, atraso só depois de 5 dias") vira preferência salva do usuário (`lembrar` com parâmetros), não regra de campo; posição de cliente pelo nome usa inadimplência/a vencer com busca pelo nome em vez de parar no cadastro.

## Mudança M54 — finalidade corporativa e revisão da memória (2026-11-16)

- Política de finalidade corporativa compartilhada pelos provedores conversacionais: assuntos profissionais, acompanhamentos contextuais e uso da IAtiva permitidos; assuntos pessoais/aleatórios devem produzir `fora_escopo`. Dúvidas pedem contexto profissional, sem fabricar justificativa.
- Backend encerra `fora_escopo` com resposta fixa, sem executar consulta, gravar nota ou abrir objetivo/tarefa/manutenção; não reutiliza conteúdo livre do modelo. Classificação semântica depende do modelo, não substitui autorização de dados e ainda precisa avaliação adversarial com modelo real.
- Revisão e prioridades para memória confiável, recuperação entre conversas, indicadores, isolamento e avaliações: `docs/EVOLUCAO_MEMORIA_CORPORATIVA.md`. Evoluções propostas não implementadas nesta mudança.
- Validação: 827 testes aprovados e 10 pulados na suíte sequencial Windows; Django check sem problemas. Testes específicos verificam recusa fixa sem memória, manutenção, objetivo ou tarefa. Na primeira suíte paralela houve uma falha de catálogo ChatGPT, não reproduzida isoladamente nem na suíte sequencial; avaliação semântica com modelo real ainda pendente.

## Mudança M54 — inadimplência e a vencer por filial ou só o total (2026-11-16)

- Faturamento: a IA usava às vezes consultas pelo cabeçalho da nota (SF2), sem filtro de TES, que somam remessas e transferências. As duas consultas aprendidas de SF2 foram removidas e a instrução fixa faturamento = D2_TOTAL dos itens de saída com TES que gera duplicata (F4_DUPLIC = S), a confirmar com o Financeiro.

- Achado no teste do piloto: "atraso por filial" vinha por cliente e "quanto vamos receber" vinha como lista de títulos. Agora as duas consultas fixas aceitam "por filial" (um total por filial) e "total" (um número só, somando as filiais); a inadimplência mantém o "top N".

## Mudança M53 — IA declara o que falta, manutenção visível e narração ao vivo (2026-11-16)

- Nova ação `falta_capacidade`: depois de verificar que nenhum caminho existente resolve o pedido inteiro, a IA descreve em termos técnicos o que o sistema precisa ganhar; o pedido vai na hora para a manutenção (um incidente por capacidade), e a descrição segue mascarada como especificação para o agente de manutenção.
- O card do pedido em correção mostra as etapas da manutenção (na fila, analisando, implementando, testando, publicando, refazendo), atualizadas pelo executor do host (`manage.py manutencao etapa`).
- As frases do modelo sobre o que está fazendo ("Vou levantar…") aparecem ao vivo no andamento e saem da resposta final quando há resultado.
- `iativa-manutencao.path` instalado (2026-11-16): a manutenção começa no instante em que o incidente é registrado; o timer virou conferência horária.
- Validado: "quantos dias úteis faltam para acabar o mês" foi declarado, implementado pela manutenção, publicado e respondido sozinho (16 min). Para encurtar: suíte em paralelo (pytest-xdist, 4 CPUs) no executor e na atualização, o agente de manutenção roda só os testes relacionados, e a revalidação final é pulada quando a main não mudou.
- Campo do chat destrava sozinho quando o pedido em correção termina.

## Mudança M52 — projeção reconhecida como pedido não cumprido pela soma (2026-11-16)

- "Projeção", "previsão de fechamento" e "quanto vai fechar" passam a exigir um cálculo de projeção: a soma do realizado no período não encerra o pedido. Sem essa capacidade, o pedido segue para a manutenção (com a pergunta mascarada) para que ela seja criada.
- Operação de cálculo `projecao`: realizado do mês até ontem ÷ dias úteis passados × dias úteis do mês (feriados nacionais descontados), por linha e total, com a premissa de que é estimativa linear.
- Usuário de teste `teste.ti` criado no servidor de teste com as mesmas permissões de consulta do usuário de TI, sem administração, para os testes feitos pela Claude no navegador (senha fora do repositório).

## Mudança M51 — pergunta mascarada no diagnóstico da manutenção (2026-11-13)

- Validado em produção (2026-11-14): a manutenção recebeu a pergunta de dias úteis entre datas com feriados nacionais, criou o cálculo de calendário (feriados fixos e Sexta-feira Santa), publicou e refez o pedido sozinha. Falha encontrada: o resultado de calendário não tinha escopo e não era salvo, então a resposta saiu sem o número; corrigido com escopo próprio de calendário (sem ERP, visível a quem fez a pergunta).

- Capacidade ausente leva à manutenção a pergunta da pessoa com nomes próprios, números longos (CPF, CNPJ, matrícula, valores) e e-mails trocados por marcadores; datas curtas ficam. Antes só iam códigos e a manutenção não sabia o que construir (recusava com "não reproduzido"). Nenhum dado do ERP entra. O executor do host revalida o formato. Recorrência atualiza a pergunta do incidente.

## Mudança M50 — correção na hora com chat ocupado (2026-11-13)

- Incidente elegível grava `var/manutencao-acordar` no volume; a unidade `iativa-manutencao.path` inicia o executor na hora. O timer passou de 15 min para varredura de hora em hora.
- Pedido em correção (situação manutenção) ocupa o chat: barra "Corrigindo…" com o mascote, campo travado com aviso para abrir um novo chat, botão enviar vira parar (encerra o pedido sem refazê-lo). A resposta chega no mesmo chat quando a correção entra no ar.
- O aviso de correção não mostra mais texto do modelo sem resultado conferido (antes vazou um número não calculado).

## Mudança M49 — hora do servidor para a IA (2026-11-13)

- O contexto do modelo traz "agora" (data, hora, dia da semana e fuso do servidor, lido a cada chamada). "Que horas são?" é respondido direto. Sem acesso de shell/Docker ao modelo: só o relógio é exposto.

## Mudança M48 — resposta no formato pedido e correção automática (2026-11-13)

- A IA declara o formato da resposta pelo que a pessoa pediu, sem palavra fixa: "direto" mostra só o valor montado pelo backend depois de executar (ex.: "195 dias úteis", um total, a medida de uma linha), sem tabela, critérios nem texto extra; "resumo" tira as premissas e limita o texto. Resposta direta fica salva no snapshot e reaparece igual ao reabrir.
- O modelo recebe "consultas_desta_conversa": as últimas consultas executadas no chat, com consulta, tabelas, critérios, filtros (fixos e do usuário), colunas e premissas. Só metadados; resultado fora das permissões atuais fica de fora e premissas com valores em reais são removidas.
- Instruções: responder exatamente o que foi pedido, sem extras; preferência de forma para sempre vai à memória do usuário; perguntas sobre uma consulta já feita são respondidas sem rodar de novo; antes de desistir, tentar outros caminhos.
- Capacidade ausente encaminhada à manutenção (agente Opus) passa a dizer "Não tenho isso ainda, corrigindo…" e que o pedido será refeito e respondido no chat após a correção publicada (fluxo M32/M38 existente).
- Validação: 800 testes aprovados e 10 pulados no Windows.

## Mudança M47 — leitura estável e ajustes do mascote (2026-11-13)

- Atualizações de progresso, objetivos e conclusões não reinicializam a sincronização nem levam a rolagem ao final. Histórico autorizado possui versão própria, igual na página inicial e no polling; mudanças somente na lateral não remontam tabelas/cartões nem repetem o carregamento de conclusões.
- Uma atualização real do histórico preserva a posição de leitura e o rascunho; acompanha o fim apenas quando a pessoa já estava perto dele. Novas conversas continuam aparecendo entre sessões.
- A conclusão de objetivos em segundo plano remove somente a barra de atividade; não envia mais `HX-Refresh` para recarregar a página inteira.
- Cabeçalho de 64px no desktop e 62px no celular, cápsula menor e sem sombra. Sombra SVG removida; capacete amarelo legível e movimentos do personagem inteiro durante conferência, busca, organização, cálculo e escrita. Movimento reduzido continua respeitado.
- Chrome com dados sintéticos conferiu identidade da tabela no primeiro polling e na atualização da lateral, progresso sem deslocamento, mensagem remota mantendo rolagem/rascunho, tabela única e acompanhamento do fim. Desktop/celular sem transbordamento; estados e movimento reduzido verificados. Testes de integração cobrem estabilidade da versão e invalidação por acesso/mensagem.
- Validação local: 796 testes aprovados e 10 pulados por dependências de shell no Windows. Django check sem problemas; nenhuma migração nova.

## Mudança M46 — mascote IAtiva no chat (2026-11-13)

- Cápsula central no cabeçalho com mascote, nome e estado. Mascote também nas respostas, no pensamento durante envio e nas atividades/tarefas em segundo plano.
- SVG próprio inspirado na prévia aprovada: corpo e olhos persistem nas transformações em toalete portátil, container e lavatório. Lupa, calculadora, prancheta e capacete/check representam busca, cálculo, escrita e conferência; inclui pensamento, respiração/piscada, conclusão, atenção e interrupção.
- Estados seguem os textos operacionais existentes, sem simular novas etapas. Erro/formulário inválido não comemora; tarefa/objetivo pendente mantém atividade. Animações das respostas antigas ficam estáticas, e movimento reduzido desativa transições/animações.
- Verificação em Chrome com dados sintéticos: desktop/celular sem transbordamento horizontal; estados compartilhados entre cápsula e pensamento, falha, objetivo pendente e movimento reduzido conferidos. Sem alterações nas consultas ou nos dados.
- Validação: 794 testes aprovados e 10 pulados no Windows; Django check sem problemas e nenhuma migração nova. Chrome desktop/celular validou estados, movimento reduzido e layout. Detalhes: `docs/MASCOTE.md`.

## Mudança M45 — consultas acionáveis e cálculo até a conclusão (2026-11-13)

- “E dias úteis?” recupera a pessoa, o intervalo de estágio e a filial, inclusive após uma resposta M44 que tinha mostrado só admissões. Não calcula o vínculo de outro cargo até hoje nem aceita troca de pessoa/filial sugerida pelo modelo sem pedido.
- Nova ferramenta `calcular_resultado`: dias úteis/corridos, meses de calendário, soma, média, diferença e percentual sobre resultado autorizado salvo ou recém-consultado no mesmo ciclo. Modelo recebe esquema, tipos, operações e indicador de intervalos; linhas/datas derivadas ficam no backend. As consultas e a descoberta de tabelas existentes continuam disponíveis.
- Pedido de dias úteis/corridos exige a medida correspondente, não uma tabela ou contagem de registros. Observações dizem qual cálculo falta; fontes insuficientes ficam explícitas, sem inventar datas. Regras de acesso a conversa, setor, filial e campos são reconferidas.
- Snapshots incluem intervalos derivados, cobertura e moedas. Soma de recorte truncado/moedas distintas é recusada; data final ausente não vira hoje automaticamente; percentual com base zero é indefinido. Compatível com campos opcionais nulos do schema estrito ChatGPT.
- Contagem de segunda a sexta, início incluído e fim excluído, consistente com a diferença de datas. Sem desconto de feriados enquanto não houver calendário definido; essa premissa aparece na resposta. Cadastro sem histórico suficiente continua identificado como estimativa.
- Validação: 790 testes aprovados e 10 pulados no Windows, incluindo 31 regressões novas; Django check sem problemas e nenhuma migração pendente de geração. Nenhum resultado real registrado nesta documentação.

## Mudança M44 — sincronização, tabelas permanentes, layout e tempo de estágio (2026-11-13)

- Chats e mensagens autorizados sincronizados entre sessões por polling de 2 segundos; sem executar consultas ao ERP para atualizar a tela. Preserva compositor, busca, grupos e posição de leitura; resposta de navegação antiga é descartada.
- Tabelas passam a ser snapshots ligados à mensagem, incluindo resultados do worker e diagnósticos/consolidados. Texto e ficha enviados ao modelo continuam sem linhas. CPF/CNPJ são omitidos dos snapshots; SQL não é serializado. Leitura reconfere políticas, filiais e restrições de campos. Conversas com resultados ou tarefas não expiram automaticamente. Resultados antigos descartados exigem reexecução única para passar a ficar salvos.
- Layout fica na conta do usuário, separado da sessão. Largura aplicada pelo JavaScript permitido pela CSP, com gravação ao encerrar arraste/sair da página. Mantido suporte ao layout antigo da sessão.
- Pedido de tempo de estágio usa a consulta encadeada, com tabela de cargos e duração por vínculo em meses de calendário/dias. Histórico registrado e estimativa pelo cadastro são diferenciados; homônimos, lacunas, datas inválidas/futuras e mudança sem data comprovada não viram duração inventada.
- Migração 0005 do chat é aditiva: JSON opcional e flag com padrão no próprio banco, tolerando gravações da versão antiga durante a drenagem. Navegador real confirmou sincronização, rascunho preservado, CSP e largura após recarregar, com dados sintéticos.
- Validação: 759 testes aprovados e 10 pulados no Windows; 16 novos casos da M44. Django check sem problemas, migrações consistentes e teste de navegador aprovado. Nenhum resultado real registrado nesta documentação.

## Revisão M43 — correções das mudanças M40–M42 (2026-11-13)

- Ranking geral reúne todos os grupos de cada filial antes de aplicar top N; se o teto de leitura impedir a cobertura, o resultado não é validado como ranking geral. Limites de leitura e permissões preservados.
- Criação e filtros obrigatórios são atômicos: filtro inválido não deixa consulta aprovada sem o critério. Nome já existente com filtros diferentes exige outra consulta, sem alterar a definição compartilhada.
- Filtros pessoais têm junções próprias; antes de ajustar/desfazer filtros globais, dependências antigas e versões pessoais são preservadas usando apenas metadados. Ajustes pessoais e globais serializam pelo mesmo registro da consulta.
- Critérios diferentes contam como ações diferentes no ciclo. Pergunta pendente não fica cumprida por uma afirmação sem consulta; memória só conta como registrada após confirmação do backend e não substitui a resposta às demais perguntas.
- Diagnóstico de possíveis títulos repetidos não afirma que as diferenças se resumem ao prefixo/tipo sem conferir os demais critérios.
- Validação: 743 testes aprovados e 10 pulados no Windows, incluindo 10 regressões novas; Django check sem problemas e nenhuma migração pendente de geração. Nenhuma consulta ao ERP real nesta revisão.

## Mudança M40 — ajustar e voltar filtro de consulta a pedido (2026-11-13)

- Ações `ajustar_filtro` e `voltar_filtro`: a pessoa pede para mudar o filtro de uma consulta aprendida (inclusive campo de tabela ligada, ex.: TES) e pode voltar como era antes.
- Regra de faturamento escolhida por Diego: itens de saída cuja TES gera duplicata.
- Ciclo: a consulta criada numa etapa fica disponível na seguinte; ação repetida pede outra estratégia; "responda e guarde" faz as duas coisas. Permissão geral (TI) tem aprovação imediata do que ensina.
- Validado no chat real: com a correção, a IA somou o faturamento de setembro pelos itens com TES que gera duplicata (valor esperado por Diego).
- Rodada de perguntas no chat real (M40): ranking "todas as filiais" passa a ser geral (grupos somados entre filiais); a resposta diz o maior/menor grupo; consulta nova nasce com os filtros da definição do termo; investigação confere duplicidade (fornecedor/cliente, número, parcela, valor) e natureza, com resumo do assunto certo (sem texto de faturamento fora de SF2/SD2).
- Definido por Diego: "contas a pagar" é o saldo em aberto (E2_SALDO). Registrado pelo chat como definição da empresa (aprovação imediata); conversa nova já soma o saldo.
- M41 (rodada de perguntas): inadimplência "10 maiores" sem perguntas extras e com top 10 geral; "quanto vamos receber" pela consulta fixa num passo; "admitidos e saídos no ano" responde as duas partes. A IA tinha tirado sozinha o filtro de demitidos da consulta de admissão; foi restaurado pelo chat e agora ajuste sem pedido é recusado.
- M42: filtros em duas camadas (fixos do T.I para todos; do usuário só para quem pediu). Validado no chat: "só pra mim, admissões contam também quem já foi demitido" virou filtro só de Diego; os demais continuam com a definição original.
- Testes: 716 aprovados e 10 pulados no Windows (9 novos em `tests/test_ajuste_filtro.py`).

## Mudança M39 — correções da autonomia e conexões de IA (2026-11-13)

- Escopo “todas as filiais” não exige verificação de pessoas nem desativa a proteção contra anúncios sem execução. Contagens e consultas comuns não geram manutenção indevida.
- O orçamento de continuação é consumido ao reivindicar o trabalho, inclusive se o processo falhar; indisponibilidade anterior à primeira ação encerra pelo coordenador. Uma execução antiga não sobrescreve a nova, e mensagem recebida durante o worker interrompe a ação/resposta antiga.
- Falhas técnicas do diagnóstico ficam vinculadas ao objetivo e permitem retomada após publicação. Tarefa agendada deixa o objetivo aguardando o worker; conclusão exige cobertura, e cancelamentos/falhas atualizam o objetivo. Retomada reutiliza a tarefa já existente.
- Migração 0009 adiciona vínculo opcional com a tarefa e reconcilia metadados antigos; alteração de choices é só de estado, sem DDL. A checagem de implantação aceita SeparateDatabaseAndState apenas sem operações de banco.
- Tela **Conexões de IA** reorganizada por agente, com provedores lado a lado, indicação da IA em uso, modelos junto à conta e ações secundárias recolhíveis. Logins, contas e seleção preservados; status de autenticação atualiza só o agente correspondente. Conferência visual em desktop e celular com dados de demonstração.
- Validação: 706 testes aprovados, 10 pulados no Windows; Django check sem problemas e nenhuma migração pendente de geração. Integração isolada concluída sem falhas: 13 verificações, incluindo publicação, troca azul/verde, retomada única e conclusão da tarefa sem nova mensagem. Nenhum resultado real usado em testes ou registrado na documentação. A validação isolada da manutenção usa decisões/correção roteirizadas; não comprova programação autônoma da IA real.

## Mudança M38 — autonomia geral: objetivo, evidência, continuação e manutenção (2026-11-13)

- O pedido só termina com evidência do que foi pedido; consulta que deu certo não encerra verificação nem investigação. Anúncio sem execução e resposta repetida voltam ao modelo; sem progresso, o ciclo para.
- No limite de passos/tempo o worker continua sozinho; capacidade ausente sem exceção vai para a manutenção com diagnóstico só de códigos; depois da correção publicada, o pedido original é refeito uma vez.
- Testes: 678 aprovados e 10 pulados no Windows (28 novos em `tests/test_objetivos.py`).
- **Comprovado de ponta a ponta** no servidor autorizado, em ambiente isolado com dados sintéticos e defeito injetado só na cópia (`scripts/testar_autonomia.sh`, 12 verificações): pedido parou sem a evidência → incidente elegível → executor reproduziu, corrigiu, validou, publicou num repositório local e implantou por azul/verde → versão conferida → worker refez o pedido → verificação concluída com a lista inteira.
- **Não comprovado:** decisões do modelo real nesse fluxo (o teste usa roteiros fixos no lugar da IA) e a correção de um incidente de capacidade escrita pela IA de manutenção real. Ver limitações em `docs/AUTONOMIA.md`.
- Atenção: a manutenção está ligada com publicação no servidor; incidentes de capacidade passam a ser elegíveis à triagem automática, dentro dos limites existentes (3 por dia, 6 arquivos, 250 linhas).

## Mudança M37 — investigação de divergências (2026-11-12)

- Sucesso de uma consulta não encerra investigação: ação semântica `investigar_divergencia`, decomposições e confronto de fontes antes da conclusão, sem depender de nova mensagem.
- Diagnóstico genérico de consultas aprendidas e adaptador SF2/SD2/SF4: moedas separadas, critérios originais, junções sem multiplicar cabeçalhos e evidências transitórias. Não ajusta filtros a um valor esperado, não salva resultados e não aprova regra financeira automaticamente.
- Conferido no servidor autorizado com permissões da conversa original: 12 etapas concluídas em cerca de 16 segundos, transação da aplicação desfeita ao final; dados exibidos somente na resposta da aplicação. Sem alterações no ERP ou nas contas/modelos de IA. Pedido também conferido com a Claude conectada; agrupamento textual por filial passa a ser aceito sem ampliar o escopo.
- Validação: 650 testes aprovados, 10 pulados no Windows; Django check sem problemas e nenhuma migração pendente. Implantação pelo fluxo azul/verde com testes e drenagem. Detalhes: `docs/DIAGNOSTICO_DIVERGENCIAS.md`.

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
