# Investigação de divergências (M37)

O defeito era confundir consulta bem-sucedida com conclusão do pedido. O ciclo antigo encerrava
no primeiro sucesso, mesmo quando o texto prometia investigar a diferença depois.

A ação `investigar_divergencia` aceita o mesmo objetivo/parâmetros de `preparar_consulta`.
O modelo identifica semanticamente o pedido; há também uma proteção para divergências explícitas
que tenham sido tratadas como consulta simples. A execução inicial vira a primeira evidência,
não a conclusão. A expressão de cálculo `por filial` é aceita como o agrupamento já feito
pelo serviço; não muda o parâmetro de escopo nem amplia filiais. A recusa textual numa continuação sem escopo novo reaproveita a consulta anterior.
Um período/empresa/filial novo não reaproveita silenciosamente os critérios antigos.

O executor faz decomposições por filial/moeda, tipo, série, status e datas disponíveis.
Para SF2, acrescenta os cenários com/sem título associado, F2_VALFAT, itens SD2 por TES/CFOP
e indicador financeiro da TES. A moeda vem do cabeçalho quando não existe no item.
As chaves do cabeçalho e da TES são conferidas antes de agregar: somam-se valores dos itens,
nunca o valor do cabeçalho repetido por item. Filtros e regras originais não são removidos.
Fontes/campos ausentes, restritos, cardinalidade inválida, truncamento e prazo produzem limitações
explícitas. Cancelamento interrompe; revogação descarta os dados já lidos nesta resposta.

Cada leitura reconfere objetivo/setor/filial e restrições, passa por compilador/AST e usa a conexão
somente leitura. Limites: 96 leituras do executor, 200 grupos por leitura e o prazo total existente.
O snapshot da requisição guarda apenas metadados. Resultados e cenários financeiros ficam somente
na resposta HTTP, sem ir ao modelo, histórico, memória, auditoria ou incidente. A ficha guarda
apenas o tipo da operação e o estado da cobertura/conclusão. Não há mudança de banco da aplicação.

Não se procuram combinações de filtros para atingir o valor sugerido pelo usuário. O executor testa
hipóteses definidas antes de comparar com a expectativa; aproximação não aprova uma regra.
Gerar duplicata não é sinônimo universal de receita e o cadastro atual da TES não comprova sua
configuração no momento da emissão. Devoluções, impostos e definições específicas de receita
não são corrigidos com critérios inventados. Nenhuma regra é aprendida/aplicada automaticamente
a partir de um valor esperado. A manutenção técnica continua separada da validação de negócio.

O chat mestre mantém os diagnósticos separados por setor, sem somar escopos sobrepostos.
As mensagens de andamento indicam as verificações efetivamente executadas. Não há promessa
de continuação depois de apresentar uma soma como resposta final.

## Referências de semântica (não substituem as regras da empresa)

- [TOTVS: geração de títulos no F2_DUPL e campos dos itens](https://centraldeatendimento.totvs.com/hc/pt-br/articles/360052730514-Cross-Segmentos-Backoffice-Protheus-SIGAFAT-Campos-de-movimenta%C3%A7%C3%A3o-financeira-estoque-na-NF).
- [TOTVS: relatórios de faturamento e seus parâmetros](https://tdn-homolog.totvs.com/pages/releaseview.action?pageId=402068451).

## Validação

`python -m pytest -q tests/test_diagnostico.py` cobre continuidade após sucesso, ação semântica,
fonte genérica, moedas, filtros do cabeçalho, chave de ligação, restrições, revogação, cancelamento,
prazo, privacidade, renderização, chat mestre e independência do valor esperado.
O executor foi conferido no servidor autorizado com as permissões da conversa de origem,
sob transação da aplicação desfeita no final. Nenhum resultado real é registrado nesta documentação.
