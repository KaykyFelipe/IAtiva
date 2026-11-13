# Autonomia de execução, verificação, recuperação e manutenção (M38)

Referência: coordenação de tarefas do OpenMausBot (módulos executáveis, não os planos): instruções
de autonomia, aviso quando o modelo só anuncia uma ação, guarda contra continuação sem progresso,
continuação com limites. Aqui foi adaptado ao IAtiva: o modelo continua sem ferramentas próprias e
sem ver dados do ERP; quem executa, mede e decide se terminou é o backend.

## Ideia central: exigência × evidência

| | O que é | Exemplos |
|---|---|---|
| **Exigência** | o que o pedido precisa entregar para estar cumprido | resposta, consulta, cálculo, verificação, investigação, histórico |
| **Evidência** | o que a ação executada de fato entregou | consulta executada, cálculo no banco, verificação concluída com cobertura, diagnóstico completo, histórico levantado |

A exigência vem, nesta ordem: de um detector do backend (o mínimo garantido), do campo `entrega`
que o modelo declara na decisão, ou da ação escolhida. O pedido só encerra quando a evidência cobre
a exigência (`iativa/agent/objetivos.py`, `SATISFAZ`). Consulta que deu certo é evidência de
"consulta": não encerra verificação nem investigação. O escopo “todas as filiais” não é um pedido
para investigar vínculos de pessoas. Uma tarefa agendada fica em `aguardando_tarefa`, vinculada ao
objetivo, até que o worker comprove a cobertura. Cancelamento/falha não contam como entrega.

## O ciclo (`_ciclo_agente`)

1. O modelo decide uma ação; o backend executa e mede a evidência.
2. Evidência cobre a exigência → encerra. Pergunta à pessoa ou falta de permissão → param
   legitimamente (aguardando / impedimento real), sem contorno.
3. Evidência menor que a exigência (sucesso intermediário) → observação `etapa_concluida` dizendo o
   que falta e qual ação serve; o ciclo segue sem nova mensagem.
4. Texto que só anuncia ("Vou consultar…") ou repete a resposta anterior para **outra** pergunta não
   é progresso: volta ao modelo uma vez (`anuncio_sem_execucao`, `resposta_repetida`). A promessa não
   é gravada como resposta.
5. Erro corrigível (parâmetro, metadado) → o modelo recebe o motivo e tenta outro caminho.
6. Mesma ação com os mesmos dados, ou passos seguidos sem evidência nova → o ciclo **para** (não
   insiste).

## Quando o ciclo para sem cumprir

| Causa | O que acontece |
|---|---|
| Limite de passos/tempo, IA ou ERP lentos, com trabalho andando | o pedido é preservado (`Objetivo` em "continuar") e o **worker continua** sozinho, até 2 vezes; a tela avisa e recarrega quando chega a resposta |
| Falha passageira que persiste | impedimento explicado; não vira correção de código |
| Sem progresso (capacidade ausente ou defeito **sem exceção**) | diagnóstico sanitizado para a manutenção; o pedido fica aguardando a correção |
| Falta de permissão | impedimento real; nada é contornado nem encaminhado |

O worker não responde fora de ordem: se a pessoa já mandou outra mensagem, o pedido antigo é
marcado como superado. Essa condição é reconferida antes das ações e antes de publicar a resposta;
uma tarefa criada nesse intervalo é cancelada. A concessão é conferida para evitar gravação por
uma execução antiga. O orçamento é consumido na reivindicação, mesmo se o worker falhar antes da
primeira decisão. O resultado obtido pelo worker não é gravado: a mensagem aponta o
"Executar de novo".

## Encaminhamento à manutenção

- Erros técnicos em uma etapa do diagnóstico são vinculados ao objetivo; após publicação, ele pode ser retomado. Limitação de fonte ou regra de negócio continua sendo impedimento, sem inventar critérios.
- O incidente leva só códigos: exigência, causa, trilha `ação:situação:evidência` e, se houver, o
  alias da tabela. Nunca o texto do pedido, nomes ou resultados.
- Categoria "capacidade ausente" com trilha é **elegível à triagem** (antes era só registrada).
  A triagem é a prova de reprodução: o executor só publica se o agente escrever um teste com dados
  sintéticos que **falha na versão afetada** e passa com a correção, com a suíte inteira passando.
  Sem reproduzir (`NAO_REPRODUZIDO`), nada é publicado e a pessoa é avisada na conversa.
- Fora do alcance da manutenção automática: permissões, auditoria, privacidade, conexões de IA,
  validador de SQL, conexão com o ERP, migrações, settings/scripts e **regras de negócio**
  (`iativa/query/plano.py`, `iativa/agent/contratos.py`, `iativa/memory/`, `knowledge/`). O prompt
  proíbe afrouxar a exigência ou escolher filtros para chegar a um valor esperado.
- Os limites existentes continuam: 3 correções por dia, 6 arquivos, 250 linhas, 2 tentativas por
  incidente, publicação só com `MANUTENCAO_PUBLICAR=1`.

## Retomada depois da correção

Correção publicada e versão conferida no ar → `manutencao retomar` devolve o pedido à fila **uma
vez** → o worker refaz o ciclo a partir da mensagem original, com permissões reconferidas → a
resposta começa com "Depois da correção publicada, refiz o seu pedido". Se voltar a falhar, o
incidente reabre uma vez (regra existente) e o pedido fica como impedido: não há ciclo infinito.

## O que é gravado

`Objetivo`: exigência, origem, situação, trilha de códigos, contadores, vínculo com o incidente.
Não grava respostas, linhas, nomes ou números do ERP. O texto do pedido continua só na mensagem do
usuário, como sempre.

## Configuração (`IATIVA_AGENTE_CICLO`)

`passos` 5 · `tempo_s` 120 · `retentativas` 1 · `cutucadas` 2 · `sem_progresso` 2 ·
`continuacoes` 2 · `espera_s` 45. Variáveis: `IATIVA_AGENTE_PASSOS`, `IATIVA_AGENTE_TEMPO_S`,
`IATIVA_AGENTE_CONTINUACOES`, `IATIVA_AGENTE_ESPERA_S`.

## Como validar

- Comportamento e regressões (decisões roteirizadas, dados sintéticos): `tests/test_objetivos.py` e `tests/test_regressoes_autonomia.py`.
- Ponta a ponta real, isolado, no servidor: `scripts/testar_autonomia.sh` (projeto `iativa-teste`,
  porta 18000, "GitHub" local, defeito injetado só na cópia). A IA é trocada por roteiros fixos:
  prova o encanamento inteiro, não a qualidade das decisões do modelo real.

## Limitações

- A exigência depende do detector ou do que o modelo declara em `entrega`; se os dois errarem para
  menos, o pedido pode encerrar cedo.
- Desde a M44, resultados do worker são gravados com a mensagem e aparecem pela sincronização automática da conversa, respeitando as permissões atuais.
- Capacidade que exija mudança grande, regra de negócio ou migração não é corrigida sozinha.
- A manutenção com IA real (Claude/ChatGPT) corrigindo um incidente de capacidade não foi exercitada
  no teste isolado.
