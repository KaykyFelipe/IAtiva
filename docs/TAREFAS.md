# Tarefas longas do agente (M25)

## Para que servem

Pedidos sobre uma lista inteira ("todos foram demitidos mesmo?", "confira cada um", "algum teve o
cargo trocado?") viram uma **tarefa**. Um exemplo citado na pergunta é pista, não escopo: a tarefa
verifica todos os integrantes da lista que a pessoa estava vendo.

## Fluxo

1. **Chat (requisição web):** o modelo escolhe `investigar_lista` e diz quais campos do dicionário
   representam início, fim, situação, cargo e categoria do vínculo. O backend valida no dicionário
   (o modelo corrige na mesma mensagem se errar) e cria a tarefa. Se o pedido cobre a lista inteira
   e o modelo tenta consultar só o exemplo, ou deixar "para depois", a ação volta para ele sem executar.
2. **Worker (`manage.py executar_tarefas`, serviço `worker` no compose):** pega a tarefa e executa as
   etapas: conferir permissões → consultar de novo a lista original → buscar outros vínculos de
   todos, em lote (uma consulta por filial e lote de até 40 pessoas) → comparar e classificar →
   conferir a cobertura.
3. **Tela:** o cartão da tarefa se atualiza a cada 2 s (etapas, progresso, cobertura) e permite
   cancelar. Ao terminar, a conclusão aparece. Fechar a página não interrompe nada.

## Quando a tarefa termina

| Situação | Quando |
|---|---|
| Concluída | todos da lista têm classificação ou motivo de inconclusão, e a lista não foi cortada |
| Parou num limite | prazo, consultas ou volume de registros: mostra a cobertura e o que faltou |
| Bloqueada | sem permissão (reconferida antes de cada consulta) ou plano impossível |
| Aguardando você | falta uma decisão que só a pessoa pode dar |
| Cancelada / Falhou | a pedido / erro que não se resolveu nas tentativas |

## Classificação (determinística, no backend)

Compara datas de início e fim de cada registro com os outros registros da mesma pessoa:
"encerrado sem outro vínculo", "encerrado com novo vínculo depois" (com aviso de possível
transferência quando começa em outra filial no dia seguinte e diferença de cargo/categoria),
"outro vínculo no mesmo período", "só vínculo anterior", "sem data de fim". Nunca afirma
efetivação, promoção ou o significado de um código.

Identidade:
- **Documento** (`IATIVA_VINCULO_POR_DOCUMENTO=1`): o CPF da própria tabela é usado só dentro da
  consulta, trocado por um resumo criptográfico em memória, nunca exibido nem gravado.
- **Nome** (padrão): nome igual não prova que é a mesma pessoa; esses casos saem **inconclusivos**.

## O que é gravado e o que não é

- Gravado (tabela `Tarefa`): objetivo, critérios já mostrados no histórico, plano (nomes de campos),
  etapas, limites, consumo (contagens), cobertura (contagens), situação e motivo.
- Gravado também (M29, decisão do Diego, muda a R3 só para tarefas): a **conclusão** (nome,
  matrícula, classificação, textos e registros comparados), sem prazo, junto da conversa. Ao recarregar,
  ela aparece sem nova consulta e mostra a data da verificação.
- Não gravado: CPF/documento, nem nas evidências. Mensagens, auditoria e logs continuam só com
  contagens e situação.

## Concorrência e falhas

- Reivindicação por compare-and-swap (`execucao`, `lease_ate`): dois workers nunca executam a mesma
  tarefa. O lease é renovado a cada operação.
- Worker que morre: o lease vence, outro worker assume, as etapas que dependiam de evidências voltam
  a pendente e a lista é consultada de novo (a tarefa mostra "retomada"). A execução antiga, se
  voltar, perde a posse e não grava nada.
- Cancelamento e prazo são conferidos antes de cada consulta.

## Limites (`IATIVA_TAREFAS`)

`prazo_s` 900, `chamadas_modelo` 4, `consultas` 150, `tentativas` 2 por etapa, `registros` 2000 por
consulta, `lote` 40, `lease_s` 90. Orçamento de consumo do provedor: não disponível.
