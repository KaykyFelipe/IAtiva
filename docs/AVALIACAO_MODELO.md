# Avaliação do agente com o modelo real (M22)

Os testes automáticos usam um provedor de teste (`FakeProvider`): provam que o fluxo funciona
(executar, observar, corrigir, calcular, não gravar números), mas **não provam que o modelo real
entende as perguntas**. Este roteiro mede isso, de forma reproduzível.

## O que o roteiro faz

- Envia perguntas em português ao provedor de IA configurado.
- Roda só contra o **ERP sintético** (dados fictícios). Nunca contra o Protheus.
- Compara a resposta do sistema com uma referência calculada direto no banco sintético.
- Desfaz tudo ao final de cada caso: nenhuma conversa, consulta aprendida ou nota fica gravada.

## Pré-requisitos

1. `IATIVA_METADADOS_FONTE=sintetico` e a base sintética criada (`seed_synthetic`, `seed_exemplos`).
2. Provedor de IA real configurado e com o envio já liberado (regra de privacidade do projeto).
3. Um usuário com "consultar" e pelo menos duas filiais do sintético (`01`, `02`).

## Como rodar

```bash
python manage.py avaliar_agente --usuario <login> --confirmar-envio
```

`--confirmar-envio` existe porque as perguntas saem para o provedor. Elas não contêm dados do ERP.

## Casos e critérios

| Caso | Pergunta | Aprovado quando |
|---|---|---|
| soma | "Some o valor de todos os títulos a receber da filial 01, sem filtro de período." | soma do banco = referência; quantidade de registros = referência |
| quantidade | "Quantos títulos a receber existem na filial 02?" | quantidade = referência |
| media-filiais | "Qual a média do valor dos títulos a receber considerando todas as filiais?" | média = soma ÷ quantidade das filiais juntas (não média de médias) |
| ranking | "Quais os 3 clientes com maior valor de títulos na filial 01?" | 3 linhas; total geral = referência de todos os registros |
| vazio | "Some os títulos a receber emitidos em 1990 na filial 01." | resultado vazio, sem ampliar o período |
| sem-permissao | "Liste os títulos a receber da filial 99." | nenhum dado devolvido |

Referência: `SELECT SUM(E1_VALOR), COUNT(*) FROM SE1010` nas filiais do caso, só registros não excluídos.

## Como ler o resultado

Cada linha sai como `[OK]` ou `[FALHOU]` com o valor obtido e a referência. O comando termina com
erro se algum caso falhar. Guarde a saída junto da data, do provedor e do modelo usados.

## O que este roteiro não cobre

- Dados reais do Protheus (nomes de campos e regras de negócio da empresa).
- Perguntas ambíguas em que só a pessoa pode decidir.
- Regras da memória confirmadas pelo usuário (cobertas pelos testes automáticos de fluxo).

Para o Protheus real, a conferência é a do piloto: o Financeiro compara os números com os relatórios
oficiais (pendência T29).
