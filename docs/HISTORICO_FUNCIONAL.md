# Histórico de cargos e funções (M35)

O pedido de histórico de uma pessoa é uma consulta encadeada. Encontrar as matrículas é apenas
uma etapa: o atendimento continua até consultar os registros e apresentar as funções/cargos.

1. Localiza vínculos na SRA pelo nome informado, dentro das filiais autorizadas.
2. Consulta SR7 por matrícula **e filial** de cada vínculo. Uma matrícula pode se repetir em outra filial.
3. Usa descrições da SR7 ou consulta funções SRJ e cargos SQ3 quando permitido. Respeita o
   compartilhamento do dicionário; descrições ambíguas ou restritas não são inventadas.
4. Apresenta registros históricos em ordem temporal e identifica o cadastro SRA do vínculo
   com admissão mais recente, priorizando vínculos iniciados e ainda não encerrados.

O modelo escolhe `historico_cargo`, com o nome em `alvo`; o backend resolve os dados e monta a
resposta. A detecção de pedidos explícitos também impede que uma decisão de listar matrículas
ou uma resposta "não vejo as linhas" encerre prematuramente a consulta.
Continuações recuperam o nome fornecido pelo usuário nos critérios anteriores, sem enviar linhas
do ERP ao modelo. Desde a M44, a tabela e a conclusão são salvas com a mensagem, sem expiração
automática, respeitando as permissões atuais ao reabrir.

## Tempo de estágio (M44)

Pedido de duração de estágio usa a mesma ferramenta, mesmo que o modelo proponha apenas buscar
matrículas. Classifica pelas descrições de função/cargo; não presume o significado de códigos
de categoria. Calcula intervalos registrados entre eventos, encerramento do vínculo e data atual,
por matrícula/filial. Meses são de calendário, com dias restantes e total de dias; não divide por 30.
Cadastro de estágio sem eventos fornece duração do vínculo explicitamente como estimativa.
Mudança atual sem data histórica de saída do estágio não permite inventar o término do intervalo.

## Limites de interpretação

- Todo o período disponível por padrão, sem inventar um recorte de 12 meses. Período explicitamente
  informado filtra os registros da SR7, sem retirar vínculos pela sua data de admissão.
- Mesmo nome não comprova identidade: os resultados permanecem separados por matrícula e filial.
  Nomes completos diferentes encontrados por busca parcial exigem escolha do usuário.
- SR7 registra também alterações salariais; quantidade de linhas não equivale a quantidade de trocas de função.
- Admissão na SRA não é a data comprovada de mudança da função atual. A origem e a interpretação
  de cada linha são mostradas. Readmissão não comprova efetivação ou promoção.
- Datas inválidas não definem cronologia; registros futuros são marcados como previstos.
- Falta de histórico não prova ausência de mudança. O cadastro mais recente é mostrado com essa ressalva.
- Campos indisponíveis mantêm código/aviso, sem instruir o usuário a interpretar as linhas sozinho.
- A data de evento `R7_DATA`, descrita no dicionário real como "Data Alteracao Salarial", não é
  um valor salarial. A ferramenta a permite apenas com tipo data e sem restrição explícita;
  campos financeiros continuam fora da seleção.
- Excesso de linhas bloqueia uma conclusão supostamente completa. Cada leitura passa pelo
  compilador/validador e reconfere autorização, restrições, prazo e cancelamento.

## Validação

`python -m pytest -q tests/test_historico_funcional.py`

Casos sintéticos: duas matrículas da mesma busca, descrições antigas e recentes, matrícula repetida
em outra filial, exclusão lógica, continuação, nomes ambíguos, falta de SR7, período explícito,
restrição de campo, perda de permissão, filial negada, prazo e cancelamento.
