# Inteligência corporativa — M60

## Como um pedido passa a ser atendido

1. O provedor de atendimento classifica o assunto em setores configurados, identifica entregas e possíveis memórias. Essa chamada usa pergunta, histórico autorizado e resumos; não usa linhas do ERP nem recebe a lista de permissões para escolher um setor conveniente.
2. O backend recusa setores não autorizados e pede esclarecimento se a classificação estiver ausente ou incerta. Durante a execução, o escopo fica restrito à interseção entre pedido e políticas vigentes. TI/Diretoria têm políticas explícitas para todos os setores preparados; a função isolada não concede acesso.
3. Fontes aprendidas recebem classificação pelo alias, descrição e campos do dicionário, complementados pelos nomes/descrições/colunas das consultas já aprovadas que usam a fonte. O cache guarda classificação e assinatura desses metadados, sem resultados. Tabelas compartilhadas podem ter vários domínios. Fonte desconhecida não é liberada automaticamente.
4. O agente usa as ferramentas existentes e observa os resultados. Cada entrega tem tipo e, quando aplicável, métrica. O backend verifica evidência antes de marcar a entrega cumprida; o contrato é preservado no worker. Limites de tempo, passos, permissões e capacidade continuam podendo interromper o trabalho com impedimento explícito.

## O que é aprendido

Memórias automáticas têm usuário, origem, trecho literal, confiança e setores. Preferência explicitamente durável sobre apresentação pode valer imediatamente; definição ou correção inferida fica proposta para confirmação. Valor contestado, hipótese e resultado do ERP não viram fato oficial. Contexto persistente guarda resumo de intenção/critério/pendência e é recuperado apenas para a mesma pessoa, sob acesso atual.

A tela **Memória e aprovações** mostra memórias pessoais e sua origem, permite editar/esquecer e desfazer edição de preferência. Não é treinamento dos pesos, nem garantia de lembrar toda mensagem. A recuperação é limitada, prioriza o chat atual e proximidade textual, e entrega os resumos ao modelo para interpretação; não foi adicionada base vetorial.

## Regras corporativas

Alteração pessoal conflitante com regra corporativa estruturada, filtro fixo ou parâmetro obrigatório cria proposta de exceção. Só Diretoria/TI com permissão correspondente pode aprovar. A regra original continua valendo para os demais. Para filtros, a aprovação só vale enquanto a assinatura da regra corporativa não mudar; camadas legadas sem aprovação não podem desativá-la. Preferências de apresentação não concedem acesso nem mudam os cálculos.

O executor de manutenção também recusa alterações automáticas nos módulos de planejamento/autorização semântica, memória, aprendizado, escopo e descoberta, além das proteções anteriores. Incidente técnico não autoriza reescrever essas políticas. Essas mudanças exigem revisão de desenvolvimento como a M60.

## Administração e validação

`python manage.py preparar_setores_ia --ti-todos` mostra o plano. `--aplicar` cria RH/Comercial e novas políticas para TI/Diretoria com filiais já explicitamente concedidas. Setor que já tinha concessão de consulta mantém seu limite. Contas sem filial concedida são deixadas para configuração explícita. Não cria funcionários nem descobre permissões no ERP. Novos setores posteriores exigem nova concessão administrativa.

Os testes sintéticos cobrem negação antes das ações, escopo temporário isolado, aprovação de exceções, revogação, evidência da memória, isolamento entre contas, contexto entre chats, tipagem, cache de fontes e contratos de múltiplas entregas. Conferência visual usa dados sintéticos. Validação de classificação real usa somente assuntos e metadados públicos, sem executar consultas.

Permanecem necessários o piloto com usuários RH/Comercial, avaliação de classificação/latência em perguntas reais e T29 (conferência financeira com responsável do negócio). A inferência do modelo pode falhar; o bloqueio de casos incertos e as políticas do backend permanecem essenciais. Não há garantia de correção semântica absoluta nem de solução autônoma de toda pergunta.
