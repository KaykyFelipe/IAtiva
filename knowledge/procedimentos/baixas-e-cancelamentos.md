---
titulo: Baixas parciais e cancelamentos (sintético)
dominio: financeiro_receber
origem: sintetico
versao: 1
responsavel: Financeiro — Cobrança (exemplo)
situacao: aprovado
aprovado_por: exemplo-sintetico
aprovado_em: 2026-11-10
tabelas: SE1, SE5
---

## Baixa parcial
Quando o cliente paga parte do título, `E1_SALDO` diminui e `E1_BAIXA` recebe a data da última
baixa. O título continua em aberto pelo saldo. Como tratar baixa parcial numa análise é o
parâmetro `tratamento-baixa-parcial`.

## Movimentos de baixa
Cada baixa gera um movimento em SE5 com `E5_TIPODOC = 'VL'` e `E5_RECPAG = 'R'`, ligado ao título
pela chave completa (filial, prefixo, número, parcela e tipo).

## Baixa cancelada
Uma baixa cancelada tem o movimento original e o estorno (`E5_TIPODOC = 'ES'`) marcados com
`E5_SITUACA = 'C'`. Movimentos cancelados não contam como recebimento.
