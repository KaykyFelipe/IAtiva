# Roteiro do piloto — Etapa 7

Perguntas reais para usar no chat durante o piloto. Para cada uma, registrar **só** a avaliação
(entendeu? consulta certa? critérios certos? número confere com o relatório? tempo de resposta),
**nunca** os valores, nomes ou linhas do ERP (R3).

Período de teste dentro da base disponível. Coluna "Conferir com" é o relatório do Protheus usado na
comparação; o Financeiro confirma o relatório certo para cada linha (pendência T29).

| # | Tema | Pergunta | Conferir com |
|---|---|---|---|
| 1 | Inadimplência | Quais clientes estão inadimplentes? | Posição de títulos a receber (vencidos) |
| 2 | Inadimplência | Quais os 10 maiores inadimplentes, com o valor em atraso? | Posição de títulos a receber (vencidos) |
| 3 | Inadimplência | Quanto está em atraso há mais de 30 dias, por filial? | Posição de títulos a receber por filial |
| 4 | Inadimplência | Quanto está em atraso só na filial 001002? | Posição de títulos a receber da filial |
| 5 | A vencer | Quais títulos vencem nesta semana? | Títulos a receber por vencimento |
| 6 | A vencer | Quanto vamos receber nos próximos 30 dias? | Títulos a receber por vencimento |
| 7 | A vencer | Quanto vamos receber? E quanto está em atraso? (duas perguntas) | Posição de títulos a receber |
| 8 | Cliente | Qual a posição do cliente X? (nome real escolhido pelo Financeiro) | Consulta de cliente / posição financeira |
| 9 | Cliente | Busque pelo nome parcial do cliente | Cadastro de clientes |
| 10 | Cliente | Quais títulos em aberto o cliente X tem? | Posição de títulos do cliente |
| 11 | Faturamento | Quanto faturamos em agosto de 2026? | Relatório de faturamento (notas de saída) |
| 12 | Faturamento | Faturamento de agosto por filial | Relatório de faturamento por filial |
| 13 | Faturamento | Qual filial faturou mais no mês? | Relatório de faturamento por filial |
| 14 | Faturamento | Faça uma projeção do quanto vamos faturar este mês | Faturamento realizado do mês (base da projeção) |
| 15 | Todas as filiais | Quanto está em atraso em todas as filiais? | Posição consolidada |
| 16 | Formato | Quanto está em atraso? Quero só o número | Mesmo da #15 |
| 17 | Formato | Resuma a inadimplência por filial | Mesmo da #3 |
| 18 | Datas | Quantos dias úteis tem entre 01/03/2026 e 30/04/2026, descontando feriados nacionais? | Calendário |
| 19 | Datas | Quantos dias úteis faltam para acabar o mês? | Calendário |
| 20 | Consulta nova (M18) | Uma pergunta sobre tabela ainda não aprendida (ex.: pedidos de venda em aberto) | Relatório equivalente do módulo |
| 21 | Memória | Ensinar um critério ("para mim, atraso é a partir de 5 dias") e perguntar de novo | Comportamento esperado |
| 22 | Memória do setor | Propor uma regra para o setor e aprovar | Fila de aprovações |
| 23 | Chat do setor | A mesma pergunta da #1 no chat do setor | Mesmo da #1 |
| 24 | Chat mestre | Pergunta consolidada no chat mestre (diretoria/TI) | Posição consolidada |
| 25 | Continuação | "e em julho?" depois da #11 | Relatório de faturamento de julho |
| 26 | Correção | "não era isso, quero só as vencidas" depois de uma lista | Comportamento esperado |
| 27 | Capacidade nova | Pergunta que o sistema ainda não sabe (manutenção automática) | Resposta chega sozinha, com as etapas visíveis |

## Critérios de aceite (PLANO.md §8)

- ≥ 90% das perguntas interpretadas certo;
- 100% dos números iguais ao relatório, ou diferença explicada e aceita;
- `manage.py auditar_armazenamento` sem achados;
- latência média até 20 s (consultas simples).

## Registro de avaliação

Usar uma planilha fora do repositório com: nº da pergunta, data, usuário, entendeu (s/n), consulta
certa (s/n), critérios certos (s/n), número confere (s/n/não se aplica), tempo (s), observação sem dados.
