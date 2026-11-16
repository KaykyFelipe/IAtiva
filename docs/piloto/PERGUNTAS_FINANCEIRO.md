# Perguntas para o Financeiro — conferência do IAtiva (pendência T29)

O IAtiva já responde perguntas sobre contas a receber e faturamento direto do Protheus. Antes de
liberar para mais pessoas, precisamos confirmar com vocês as regras de cálculo e qual relatório
do Protheus usar para conferir cada número.

## Parte 1 — Regras de cálculo

**1. Tipos de título excluídos.** Hoje ficam de fora da inadimplência e do "a receber":
RA (recebimento antecipado), NCC (nota de crédito), PR (provisório) e abatimentos (tipos terminados em "-").

- Está correto excluir esses tipos?
- Algum outro tipo deveria ficar de fora?
- Algum desses deveria entrar?

**2. Saldo do título.** Hoje usamos o saldo atual do título, já descontando pagamentos parciais,
inclusive os feitos depois da data de corte.

- É assim que vocês analisam?
- Ou o saldo deveria ser o que existia na data de corte?

**3. Quando um título conta como atrasado.** Hoje: vencimento real anterior à data de corte menos uma tolerância.

- Qual tolerância vocês usam: 0 dias ou outra (ex.: 5 dias)?
- O atraso conta pelo vencimento real ou pelo vencimento original?

**4. O que é "faturamento".** Hoje: soma do valor total dos itens das notas fiscais de saída (D2_TOTAL).

- Devoluções de venda devem ser descontadas?
- Notas de remessa, bonificação, transferência entre filiais e outras saídas que não são venda devem ficar de fora?
- Notas canceladas estão sendo excluídas corretamente?
- O valor deve ser com ou sem impostos (IPI, ICMS-ST)?

**5. Qual valor representa o faturamento (achado nos testes de 16/11/2026).** O IAtiva hoje usa
duas medidas diferentes, dependendo da pergunta:

- valor bruto da nota fiscal de saída (`F2_VALBRUT`, cabeçalho da nota), usado em "quanto faturamos em agosto";
- valor total dos itens da nota (`D2_TOTAL`), usado na projeção do mês.

Elas dão números bem diferentes. Qual das duas (ou outra) é o faturamento que vocês acompanham?
A partir da resposta, o IAtiva passa a usar sempre a mesma.

**6. Títulos muito antigos.** Há títulos em aberto vencidos desde 2023 entrando na inadimplência.
Devem contar, ou títulos acima de certa idade (ex.: em perda/PDD, protesto, cobrança jurídica)
ficam de fora ou separados?

## Parte 2 — Relatório do Protheus para conferir

Para cada tema, qual relatório vocês usam hoje e confiam como oficial?

| Tema | Exemplos de pergunta | Relatório do Protheus (preencher) |
|---|---|---|
| Inadimplência | "Quais clientes estão inadimplentes?", "Os 10 maiores inadimplentes", "Atraso há mais de 30 dias por filial" | |
| Títulos a vencer | "O que vence nesta semana?", "Quanto vamos receber nos próximos 30 dias?" | |
| Posição de cliente | "Qual a posição do cliente X?", "Títulos em aberto do cliente X" | |
| Faturamento | "Quanto faturamos em agosto?", "Faturamento por filial", "Qual filial faturou mais?" | |

## Parte 3 — Piloto

- Quem do Financeiro (cobrança) pode participar do piloto? Precisamos de 1 ou 2 pessoas, com as filiais que cada uma acompanha.
- Podem indicar um cliente real para usarmos como exemplo na conferência da posição de cliente?
