---
titulo: Código de filial e compartilhamento de tabelas
dominio: financeiro_receber
origem: erp
versao: 1
responsavel: TI / equipe do ERP
situacao: aprovado
aprovado_por: Diego
aprovado_em: 2026-11-10
tabelas: SA1, SE1, SE5
---

## Formato do código de filial
O campo filial (ex.: `E1_FILIAL`) tem 6 posições: as 3 primeiras identificam a empresa do grupo
(ex.: `001` Ativa, `002` Eficaz) e as 3 últimas a filial dentro dela (ex.: `001` matriz).
Exemplo: `001001` é a matriz da Ativa; `002001` é a matriz da Eficaz.

## Compartilhamento
O dicionário (SX2) define, para cada tabela, se ela é exclusiva por filial (`X2_MODO`), por
unidade de negócio (`X2_MODOUN`) e por empresa (`X2_MODOEMP`):
- Exclusiva em todos os níveis (ex.: SE1, SE5): o campo filial tem o código completo (`001001`).
- Exclusiva só por empresa (ex.: SF4, TES): o campo filial tem só a empresa e espaços (`001   `).
- Compartilhada em todos os níveis (ex.: SA1): o campo filial fica em branco.

## Empresa do sistema
Todas as tabelas usadas são da empresa de sistema `010` (sufixo do nome físico, ex.: `SE1010`).
O sufixo não tem relação com a filial.
