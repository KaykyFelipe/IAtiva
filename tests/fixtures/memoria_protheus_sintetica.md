# Dicionário público do Protheus para a IAtiva (SINTÉTICO, testes)

Referência técnica externa para descoberta. Arquivo sintético no mesmo formato do real, para testes.

## SA1 - Clientes

[Página de origem de SA1](https://exemplo.invalido/tabelas/sa1.php)

Base publicada: P12.1.2510.

- Chave Unica: A1_FILIAL + A1_COD + A1_LOJA
- Modo: E

### SA1 — Campos

| Campo | Titulo | Descricao (termos de busca) | Tipo | Contexto | Tamanho |
| --- | --- | --- | --- | --- | --- |
| A1_FILIAL | Filial | filial, sistema | C | Real | 2 |
| A1_COD | Codigo | cliente, codigo | C | Real | 6 |
| A1_LOJA | Loja | loja, cliente | C | Real | 2 |
| A1_NOME | Nome | nome, cliente, razao social | C | Real | 40 |
| A1_NREDUZ | N Fantasia | nome fantasia, reduzido | C | Real | 20 |
| A1_MSBLQL | Bloqueado | cliente bloqueado, inativo | C | Real | 1 |

### SA1 — Indices

| Ordem | Chave | Descricao |
| --- | --- | --- |
| 1 | A1_FILIAL+A1_COD+A1_LOJA | Codigo + Loja |

### SA1 — Relacionamentos

| Tabela Origem | Tabela Destino | Expressao Origem | Expressao Destino | Relacionamento |
| --- | --- | --- | --- | --- |
| SA1 | SE1 | A1_COD + A1_LOJA | E1_CLIENTE + E1_LOJA | 1 para N |

## SE1 - Contas a Receber

[Página de origem de SE1](https://exemplo.invalido/tabelas/se1.php)

- Chave Unica: E1_FILIAL + E1_PREFIXO + E1_NUM + E1_PARCELA + E1_TIPO
- Modo: E

### SE1 — Campos

| Campo | Titulo | Descricao (termos de busca) | Tipo | Contexto | Tamanho |
| --- | --- | --- | --- | --- | --- |
| E1_NUM | No. Titulo | titulo, numero | C | Real | 9 |
| E1_CLIENTE | Cliente | cliente | C | Real | 6 |
| E1_EMISSAO | DT Emissao | emissao, data | D | Real | 8 |
| E1_VENCREA | Vencto Real | vencimento real | D | Real | 8 |
| E1_VALOR | Vlr.Titulo | valor, titulo | N | Real | 16 |
| E1_SALDO | Saldo | saldo, aberto, receber | N | Real | 16 |
| E1_XOBS | Obs Virtual | observacao, campo virtual | C | Virtual | 30 |

## SRA - Funcionarios

[Página de origem de SRA](https://exemplo.invalido/tabelas/sra.php)

- Chave Unica: RA_FILIAL + RA_MAT
- Modo: E

### SRA — Campos

| Campo | Titulo | Descricao (termos de busca) | Tipo | Contexto | Tamanho |
| --- | --- | --- | --- | --- | --- |
| RA_MAT | Matricula | matricula, funcionario | C | Real | 6 |
| RA_NOME | Nome | nome, funcionario | C | Real | 30 |
| RA_ADMISSA | Data Admis. | admissao, contratacao | D | Real | 8 |
| RA_DEMISSA | Dt. Demissao | demissao, desligamento, rescisao | D | Real | 8 |
| RA_SITFOLH | Sit. Folha | situacao folha, ativo, demitido | C | Real | 1 |
