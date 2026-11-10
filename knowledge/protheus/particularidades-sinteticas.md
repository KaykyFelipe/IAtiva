---
titulo: Particularidades técnicas da base sintética
dominio: financeiro_receber
origem: sintetico
versao: 1
responsavel: TI (exemplo)
situacao: aprovado
aprovado_por: exemplo-sintetico
aprovado_em: 2026-11-10
tabelas: SA1, SE1, SE5
---

## Exclusão lógica
Registros excluídos continuam na tabela com `D_E_L_E_T_ = '*'`. Toda consulta deve filtrar
`D_E_L_E_T_ = ' '` em cada tabela envolvida, inclusive nas tabelas ligadas por relacionamento.

## Filial e compartilhamento
O cadastro de clientes (SA1) é compartilhado: `A1_FILIAL` fica em branco. Títulos (SE1) e
movimentos (SE5) são exclusivos por filial: o mesmo número de título pode existir em filiais
diferentes, então a filial faz parte da chave.

## Datas
Datas ficam gravadas como texto `AAAAMMDD`. Data vazia é texto em branco, não nulo.

## Collation e comparação de texto
O banco real usa collation binária (`Latin1_General_BIN`): comparações diferenciam maiúsculas,
minúsculas e acentos, e campos de texto têm espaços à direita até o tamanho do campo.
