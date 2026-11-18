# Memória técnica do Protheus e receitas aprendidas (M95)

## O que é cada coisa

| | Onde fica | Papel |
| --- | --- | --- |
| Dicionário público (referência) | `/app/var/conhecimento-externo/MEMORIA-PROTHEUS-IATIVA.md` | Descobrir tabelas, campos e relações pelos termos do negócio. Nunca vira SQL. |
| Índice da referência | `/app/var/conhecimento-externo/indice/<sha12>.sqlite` | Busca (FTS5) por alias, campo, seção, título e termos. Um arquivo por versão. |
| Dicionário local da instalação | ERP (SX2/SX3/SIX/SX9), via `metadata.catalogo` | Confirma campos físicos/virtuais, tipos, chave, filial e compartilhamento. Vale sempre sobre a referência. |
| Receita executável (canônica) | Banco: consultas aprendidas (`ConsultaModelo`) | O que o compilador executa, com permissões por setor. |
| Receita legível | `/app/var/receitas/<codigo>/v<versao>-<assinatura>.md` e `atual.md` | Gerada do banco a cada gravação; reconstruível. Sem dados do ERP. |

`/app/var` é o volume persistente compartilhado pela aplicação e pelo worker.

## Índice

- A versão é o SHA-256 do Markdown. O hash só é recalculado quando tamanho ou data do arquivo mudam; com hash novo, o
  índice é construído num temporário e publicado com `os.replace` (atômico). Web e worker leem o mesmo arquivo.
- Saúde: `python manage.py shell -c "from iativa.knowledge.memoria_tecnica import saude; print(saude())"`
  (estado, versão, contagens). O log registra só versão, estado e contagens; nunca a pergunta.
- Trocar o Markdown: substituir o arquivo no volume. A próxima consulta reindexa sozinha. Índices antigos podem ser
  apagados de `indice/` sem efeito.

## Busca obrigatória

- Toda execução de consulta do ERP passa por `query.servico.executar_solicitacao`, que consulta o índice pelos aliases
  da consulta ANTES de preparar o SQL: chat, reexecução, continuação, acompanhamentos e worker. O resultado leva
  `memoria_tecnica = {estado, versao, trechos, aliases}` e uma premissa visível.
- Descoberta de consulta nova (`aprendizado.candidatos`): a referência sugere tabelas pelos termos; só entram as que
  existem no dicionário local e não são restritas. O modelo recebe no máximo 4 trechos técnicos por tabela autorizada
  (campos ausentes na instalação são descartados). Tabela local fora da referência: trecho "ausente" e segue pelo
  dicionário local.
- Contingência: sem arquivo ou com índice quebrado, o estado é `sem_arquivo`/`indisponivel`, a consulta segue só com o
  dicionário local e a premissa diz "Memória técnica indisponível". Nunca finge que consultou.

## Receitas

- Gravar/aprovar uma consulta aprendida gera o Markdown da receita (sinal `post_save`). Reconstruir todas:
  `python manage.py gerar_receitas`.
- Conteúdo: identificador, versão e assinatura, estado, escopo (setorial/corporativo), intenção, sinônimos, parâmetros
  reutilizáveis (mês, filial, pessoa), fontes, campos, junções validadas, critérios obrigatórios, prova esperada,
  versão da memória técnica e exemplos sintéticos.
- Aprovação: TI (M81). Revogação de acesso tira a receita do catálogo da pessoa na hora (checada em cada execução).

## Limitações conhecidas

- A receita não é revalidada em lote quando a memória técnica muda; a validação acontece a cada execução contra o
  dicionário local (campo que sumiu é recusado e a consulta não executa).
- Sinônimos ainda são o nome/intenção da receita; não há cadastro de sinônimos pelo chat.
- Busca por texto em várias colunas ao mesmo tempo foi descartada (estouro de tempo no SQL Server, M90); o filtro é
  por coluna (`CAMPO~texto`).
