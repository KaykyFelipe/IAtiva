# Evolução da IAtiva — revisão de 16/11/2026

Objetivo solicitado por Diego: assistente corporativa que conhece cada usuário ao longo do tempo,
com conversas individuais e setoriais, visão consolidada autorizada e finalidade exclusiva da empresa.
Este documento propõe evolução; somente o bloqueio de finalidade M54 foi implementado nesta revisão.

## Base existente observada

- `agent/models.py`: NotaAgente com usuário/setor/empresa, preferências, termos, regras, correções,
  hipóteses, aprovação, validade e substituição. FiltroUsuario mantém critérios pessoais separados.
- `agent/memoria.py`: recuperação com radicais de palavras; até 12 notas e 2400 caracteres.
  Notas individuais prevalecem sobre setoriais e corporativas na apresentação do contexto.
- `agent/agente.py`: histórico curto de 12 mensagens, contexto de consultas anteriores sem linhas
  do ERP, ciclo de execução/observação e cálculos determinísticos no backend.
- `accounts/authz.py`: políticas por setor, filiais e permissões. Acesso consolidado depende de
  políticas explícitas; o nome do cargo não concede acesso automaticamente.
- `agent/objetivos.py`, `tarefas.py` e manutenção: trabalho persistido, continuação e correção.
  Isso já é execução em segundo plano; um processo/modelo permanente por pessoa não é necessário.
- `knowledge/busca.py` e `documentos.py`: conhecimento documental existente, a evoluir e integrar
  com busca autorizada e atribuição de fontes.

## Prioridade 1 — aprender com evidência

Criar extração de candidatos a memória após cada interação profissional. Separar preferência
explicitamente declarada, definição, correção, hipótese e pergunta. Pergunta não é fato;
discordância de total não estabelece automaticamente o total correto.

Adicionar vínculo à mensagem de origem, confiança, justificativa, última confirmação e versão.
Preferências pessoais podem entrar automaticamente, com aviso e opção de desfazer. Critérios
financeiros/RH e regras compartilhadas exigem validação e autoridade apropriada. Uma preferência
pessoal nunca substitui uma regra corporativa obrigatória ou uma política de acesso.

Não copiar todas as perguntas ou resultados para a memória permanente. Separar histórico,
snapshot autorizado e conhecimento durável; sempre conferir dados atuais no ERP quando necessário.

## Prioridade 2 — recuperar contexto entre conversas

Adicionar perfil profissional explícito e resumo incremental por conversa, com referências de origem.
Busca híbrida (texto e semântica) em notas e documentos, filtrando usuário, setor e permissões ANTES
da recuperação. Aplicar novamente a autorização ao utilizar a informação. Testar a recuperação
após revogação de acesso e mudança de setor. Não colocar todos os históricos em todo prompt.

## Prioridade 3 — linguagem de negócio e conclusão verificável

Catálogo versionado de indicadores: definição, fonte, critérios obrigatórios, devoluções/cancelamentos,
período, unidade e responsável. Uma diferença de faturamento exige reconciliação de critérios;
não testar filtros até coincidir com um valor esperado.

Planejador deve registrar entregáveis e verificador deve comparar pedido com evidências reais antes
de concluir. Produzir cálculo e resposta final, além da tabela. Mostrar a origem e limitações.
Modelo pode receber uma síntese mínima autorizada quando aprovada a política de dados; atualmente
o código limita linhas do ERP no contexto. Ampliar isso exige decisão explícita de privacidade.

## Prioridade 4 — agentes e isolamento

Coordenador com ferramentas limitadas; especialistas de domínio quando houver necessidade,
extrator de memória e verificador. Cada execução carrega identidade e escopo imutáveis do backend.
Fila persistente com idempotência, limites, cancelamento e isolamento por usuário/conversa.
Compartilhar modelos/workers é possível; compartilhar memórias pessoais e permissões não.

Manutenção deve ficar separada das consultas: pedido de usuário não autoriza alterar permissões,
ensinar assunto fora da finalidade ou publicar regra de negócio sem responsável. Manter testes,
ambiente isolado, validação e reversão de publicação. Código que passou testes não garante resultado
de negócio correto.

## Prioridade 5 — avaliação e transparência

Corpus sintético de perguntas reais representativas: acompanhamentos curtos, nomes ambíguos,
divergências, preferências entre chats, mudança de setor, acesso negado e tentativas de mudar finalidade.
Medir conclusão correta, qualidade da recuperação, memórias erradas, vazamentos e custo/tempo.
Tela "O que a IAtiva sabe sobre mim", com origem, editar, esquecer e desfazer aprendizado.

## Limite da M54

Todos os provedores conversacionais recebem a política corporativa e a ação `fora_escopo`.
Ao recebê-la, o backend usa recusa fixa antes de executar, memorizar ou encaminhar à manutenção.
A classificação semântica ainda depende do modelo: não é garantia absoluta contra contorno.
Autorização de dados continua no backend. Próximo reforço: classificador dedicado e avaliação
adversarial com modelo real, incluindo pedidos mistos e falsas justificativas profissionais.
