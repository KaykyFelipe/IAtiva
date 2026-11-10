---
titulo: Glossário de contas a receber (sintético)
dominio: financeiro_receber
origem: sintetico
versao: 1
responsavel: Financeiro — Cobrança (exemplo)
situacao: aprovado
aprovado_por: exemplo-sintetico
aprovado_em: 2026-11-10
tabelas: SE1, SA1
---

## Título a receber
Cada parcela de uma venda a prazo. Identificado por filial, prefixo, número, parcela e tipo
(`E1_FILIAL+E1_PREFIXO+E1_NUM+E1_PARCELA+E1_TIPO`).

## Vencimento e vencimento real
`E1_VENCTO` é o vencimento original. `E1_VENCREA` é o vencimento real, ajustado para dia útil.
A definição de atraso precisa dizer qual dos dois usar e quantos dias de tolerância considerar.

## Saldo
`E1_SALDO` é o valor ainda em aberto. Título com saldo zero está quitado.

## Tipos que não são cobrança
Tipos como `RA` (recebimento antecipado) e `NCC` (nota de crédito) não representam valor a
receber do cliente. Se devem ou não entrar numa análise é uma definição de negócio a confirmar.

## Inadimplente
Ainda sem definição aprovada. Perguntar ao usuário: data de corte, vencimento usado, tolerância
em dias, tratamento de baixa parcial e quais tipos de título considerar.
