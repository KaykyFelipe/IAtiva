# Projeto: IA corporativa para consultas ao TOTVS Protheus

Você atuará como arquiteta de software e desenvolvedora deste projeto. Precisamos construir um chat corporativo que consulte o banco do TOTVS Protheus, entenda solicitações em linguagem natural e aprenda os parâmetros necessários para interpretar corretamente as perguntas.

**Trabalhe por etapas e aguarde nossa validação explícita antes de avançar para a próxima.**

## 1. Forma de trabalho obrigatória

Em cada etapa:

1. Explique o objetivo e as decisões técnicas propostas.
2. Inspecione o que já existe antes de criar ou alterar arquivos.
3. Implemente somente o escopo daquela etapa.
4. Execute os testes disponíveis.
5. Informe:
   - O que foi implementado.
   - Os arquivos criados ou alterados.
   - Como executar e testar.
   - Os resultados dos testes.
   - As limitações e decisões pendentes.
6. Apresente um checklist objetivo para nossa validação.
7. **Pare e aguarde nossa aprovação explícita.**

Não interprete silêncio, passagem de tempo ou uma pergunta sobre o trabalho como aprovação para avançar.

Se identificarmos um problema, corrija a etapa atual e submeta novamente à validação. Não avance com problemas relevantes pendentes.

Dentro de uma etapa aprovada, faça os ajustes e testes necessários sem pedir confirmação para cada operação rotineira. Peça esclarecimentos quando faltar uma decisão de negócio, permissão ou informação indispensável.

Não realize deploy, exposição externa, alterações no ERP ou operações destrutivas sem autorização específica. Não faça commit ou push sem combinarmos previamente essa política.

## 2. Objetivo do sistema

O chat deverá permitir que funcionários façam perguntas sobre os dados da empresa sem precisar conhecer tabelas, campos ou SQL.

O agente deverá:

- Entender o objetivo e o contexto da solicitação.
- Consultar os metadados relevantes do Protheus sob demanda.
- Identificar tabelas, campos, relacionamentos e regras necessários.
- Identificar parâmetros ausentes, ambíguos, inválidos ou conflitantes.
- Solicitar esclarecimentos antes de executar consultas potencialmente incorretas.
- Gerar SQL temporariamente.
- Submeter a consulta à validação e autorização do servidor proxy.
- Consultar os dados na origem.
- Apresentar resultados e explicações.
- Registrar os parâmetros e esclarecimentos úteis para solicitações futuras.

O aprendizado será por memória estruturada e recuperação de contexto. Não presumir treinamento automático dos pesos do modelo.

## 3. Tecnologias e arquitetura

Utilizaremos **Python e Django** para o backend e as APIs.

Arquitetura:

```text
Funcionário autenticado
          ↓
        Web
          ↓
Django / servidor proxy
├── Autenticação e autorização
├── Orquestração do agente
├── Memória de parâmetros
├── Descoberta de metadados
├── Validação de consultas
├── Execução controlada
└── Auditoria sanitizada
          ↓
SQL Server / integrações TOTVS Protheus
```

O servidor proxy deve aplicar regras e permissões, além de encaminhar requisições.

A escolha da tecnologia do frontend e do banco próprio da aplicação deverá ser proposta e validada na primeira etapa. O banco da aplicação deve permanecer separado do banco do ERP.

Criar uma interface substituível para o provedor de IA. Avaliar API, modelo local ou CLI conforme compatibilidade, condições de uso, confidencialidade, latência e custo. Não presumir que uma assinatura de chat autoriza automaticamente seu uso como backend comercial.

## 4. Decisão central: armazenar parâmetros, não resultados

Esta regra é obrigatória:

**Não replicar os registros do ERP para uma base da IA. Não persistir resultados nem o SQL gerado durante os atendimentos.**

A memória poderá guardar:

- Objetivo estruturado da solicitação.
- Definições dos parâmetros necessários.
- Valores de parâmetros cuja persistência seja autorizada.
- Critérios de interpretação.
- Esclarecimentos fornecidos.
- Origem, escopo, aprovação e validade dessas informações.
- Correções e referências às definições substituídas.

Não guardar na memória:

- SQL gerado em cada atendimento.
- Linhas retornadas pelas consultas.
- Cópias das tabelas do Protheus.
- Listas de clientes, títulos, contratos ou outros resultados.
- Transcrições completas que reproduzam esses dados.
- Credenciais, senhas ou tokens.

Parâmetros também podem conter dados pessoais. Proponha uma lista do que pode ser persistido e suas regras de acesso e retenção para nossa aprovação.

SQL e resultados podem existir temporariamente durante a execução, conforme política definida, mas devem ser descartados depois.

A regra deve abranger banco, logs, filas, cache, arquivos temporários, APM, captura de exceções, analytics e armazenamento do navegador.

Não prometa ausência de retenção por serviços externos sem verificar as condições e configurações do provedor.

## 5. Implantação primeiro em localhost

A primeira versão deverá funcionar exclusivamente em localhost.

Durante essa fase:

- Django e Web devem ser acessíveis somente por `127.0.0.1`.
- Não abrir portas para acesso externo.
- Não configurar túneis nem publicar serviços.
- Começar com dados sintéticos.
- Conectar ao ambiente autorizado do ERP somente após nossa aprovação.
- Usar inicialmente acesso somente para leitura.
- Documentar instalação, inicialização, configuração e testes locais.

O SQL Server poderá estar em outro computador da rede interna. Localhost refere-se ao acesso ao chat e à API.

O acesso externo será uma etapa posterior, com validação e autorização próprias. Não utilizar o servidor de desenvolvimento do Django como servidor de produção.

## 6. Dicionário e particularidades do Protheus

O dicionário é grande. Não carregue tudo no contexto do modelo a cada pergunta nem copie o banco inteiro.

Implemente descoberta progressiva:

1. Identificar o domínio de negócio.
2. Localizar entidades relacionadas.
3. Consultar tabelas e campos necessários.
4. Buscar relacionamentos e regras relevantes.
5. Pedir esclarecimento quando faltar uma definição.

As fontes poderão incluir:

- Metadados físicos do SQL Server.
- Dicionário do Protheus.
- Documentação das personalizações.
- Regras de negócio aprovadas.

A forma suportada de acesso ao dicionário deverá ser confirmada com a equipe responsável pelo ERP. Não presumir que SX2, SX3 ou outros componentes estejam disponíveis como tabelas SQL convencionais.

Validar por domínio:

- Exclusão lógica, incluindo `D_E_L_E_T_`.
- Filial e compartilhamento de cadastros.
- Nomes físicos reais.
- Chaves completas de relacionamento.
- Status e códigos técnicos.
- Datas e valores.
- Baixas parciais.
- Cancelamentos e devoluções.
- Campos e tabelas personalizados.

Não inferir personalizações apenas pelos nomes ou prefixos dos campos.

O dicionário técnico não garante, sozinho, a correção de uma consulta.

## 7. Conhecimento em Markdown

Podemos manter arquivos `.md` no servidor para conhecimento aprovado:

```text
knowledge/
├── empresa.md
├── procedimentos/
├── glossario/
├── protheus/
└── personalizacoes/
```

Esses documentos poderão conter:

- Descrições de entidades e campos.
- Regras de negócio.
- Relacionamentos aprovados.
- Definições de indicadores.
- Parâmetros necessários.
- Exceções e procedimentos.

Registrar origem, versão, responsável e situação de aprovação.

Não colocar resultados, dados cadastrais do ERP, credenciais ou transcrições completas nesses arquivos.

A IA deve recuperar somente os trechos relevantes. A existência dos arquivos, por si só, não equivale a aprendizado.

## 8. Memórias por usuário, setor e empresa

Precisamos de:

- Memória da solicitação atual.
- Memória privada do usuário.
- Memória compartilhada por setor.
- Definições gerais aprovadas da empresa.

A diretoria e TI terão um chat com visão consolidada dos setores que estiverem autorizados a consultar.

As memórias devem continuar separadas por origem e escopo. Não criar um único arquivo global com todas as conversas.

Cada informação de memória deverá identificar:

- Parâmetro ou definição.
- Origem e evidência autorizada.
- Usuário e setor.
- Escopo.
- Situação de aprovação.
- Data e validade.
- Histórico de correções.

Separar a definição de um parâmetro de seu valor numa solicitação específica.

Exemplo: “data de corte” é uma definição reutilizável; a data escolhida para uma análise não deve virar automaticamente o padrão da empresa.

Informações conflitantes exigem uma política explícita de precedência. Quando essa política não resolver o conflito, perguntar ao usuário.

Alterações de definições compartilhadas devem respeitar permissões e aprovação. Uma afirmação recebida no chat não vira automaticamente uma regra oficial.

## 9. Parâmetros e esclarecimentos obrigatórios

Antes de executar uma consulta, o sistema deve estabelecer:

- Objetivo.
- Critérios de negócio.
- Parâmetros obrigatórios.
- Requisitos condicionais.
- Relacionamentos e cálculos validados.
- Escopo autorizado.
- Limites da operação.

Exemplo:

> “Quais clientes estão inadimplentes?”

O sistema deverá verificar quais critérios já estão definidos e esclarecer os que faltam, como data de corte e tratamento de baixas parciais.

Perguntar apenas o necessário. Aproveitar informações já confirmadas e válidas.

Bloquear a execução quando houver:

- Parâmetro obrigatório ausente.
- Valor inválido ou expirado.
- Definição ambígua.
- Relacionamento não validado.
- Regra de negócio desconhecida.
- Conflito relevante.
- Falta de autorização.

Não depender apenas da confiança declarada pelo modelo. A liberação deve seguir requisitos verificáveis pelo backend.

Se uma inconsistência for identificada após a execução, sinalizar que o resultado não foi validado e pedir os esclarecimentos necessários antes de refazer a consulta.

## 10. Autorização

A identidade, função, setor e filiais permitidas devem ser resolvidos pelo backend.

O modelo não pode:

- Escolher sua própria identidade.
- Ampliar as filiais acessíveis.
- Trocar o cliente ou funcionário autenticado.
- Conceder permissões.
- Escolher credenciais.
- Contornar uma restrição pedida em linguagem natural.

Separar permissões de:

- Consulta.
- Leitura de memória.
- Alteração operacional.
- Aprovação de conhecimento.
- Administração da plataforma.

Acesso de diretoria e administração de TI não devem ser tratados como permissões universais automáticas.

Aplicar restrições também na descoberta de metadados.

## 11. Geração e execução de SQL

A primeira versão deve executar somente leitura.

O SQL gerado será temporário e deverá ser validado quanto a:

- Sintaxe e estrutura T-SQL.
- Operações permitidas.
- Objetos e campos autorizados.
- Relacionamentos.
- Filtros obrigatórios.
- Escopo do usuário.
- Parâmetros vinculados.
- Limites de linhas e tempo.
- Acesso a recursos externos não autorizados.

Utilizar parâmetros para valores. Identificadores dinâmicos devem vir de listas permitidas.

Não usar apenas expressões regulares como validador de SQL. A análise sintática deve ser complementada por autorização e permissões mínimas no próprio SQL Server.

Não liberar execução de SQL arbitrário recebido do navegador.

Um resultado vazio não autoriza retirar filtros ou ampliar permissões automaticamente.

## 12. Resultados e continuidade

O resultado deve ser consultado na origem e apresentado com contexto suficiente:

- Critérios usados.
- Filtros relevantes.
- Abrangência autorizada.
- Momento da consulta.
- Limitações.

Para pedidos como “agrupe isso por filial”, recuperar os parâmetros e executar novamente. Informar quando se tratar de uma nova consulta, pois os dados podem ter mudado.

Ao reabrir um atendimento, restaurar o contexto permitido e oferecer nova execução. Não apresentar um resultado antigo como atual.

Definir uma política para eventuais resultados temporários, com expiração e descarte.

## 13. Ações operacionais compartilhadas

Exemplo discutido:

> Um funcionário da cobrança retira um cliente da lista; outra pessoa consulta depois e deve enxergar o estado atualizado.

Isso deve funcionar por atualização da fonte operacional oficial, não por uma memória textual afirmando que o cliente foi retirado.

Fluxo:

1. Identificar a entidade corretamente.
2. Verificar a autorização.
3. Solicitar confirmação conforme a política da operação.
4. Executar pela integração autorizada.
5. Confirmar somente após sucesso.
6. Registrar a operação sem copiar o resultado.
7. Consultar a fonte atualizada nos próximos atendimentos.

Se a lista pertence ao Protheus, usar uma integração suportada. Não executar alterações diretas por SQL gerado pela IA.

Não confundir retirada de uma lista com exclusão do cadastro do cliente.

Essas operações serão uma fase posterior à validação das consultas de leitura.

## 14. Modelo de dados da aplicação

Proponha e valide modelos Django equivalentes a:

- `Department`
- `AccessPolicy`
- `ConversationContext`
- `RequestContext`
- `ParameterDefinition`
- `ParameterBinding`
- `MemoryRevision`
- `KnowledgeApproval`
- `OperationAudit`

Evitar campos genéricos destinados a armazenar conversas completas, SQL ou resultados.

Projetar migrações preservando os dados existentes.

## 15. API

Proponha contratos para:

- Receber mensagens transitórias.
- Consultar situação e parâmetros de uma solicitação.
- Informar e corrigir parâmetros.
- Solicitar execução.
- Cancelar uma solicitação em preparação.
- Consultar memórias autorizadas.
- Propor e aprovar definições compartilhadas.

Descoberta de metadados e execução SQL devem permanecer em serviços internos controlados.

A API deve fornecer estados claros para a interface, incluindo “aguardando parâmetros”, “validando”, “executando”, “concluído” e “não validado”.

## 16. Testes obrigatórios

### Interpretação

- Perguntas vagas.
- Referências ao contexto.
- Correções e negações.
- Mudanças de assunto.
- Solicitações com múltiplas interpretações.
- Pedidos fora das capacidades disponíveis.

### Protheus e negócio

- Filiais compartilhadas.
- Chaves compostas.
- Exclusão lógica.
- Baixas parciais.
- Cancelamentos.
- Personalizações.
- Agregações que possam duplicar valores.

### Memória

- Separação entre usuários e setores.
- Visão consolidada autorizada.
- Validade dos parâmetros.
- Correções e conflitos.
- Aprovação de definições.
- Retenção.

### Segurança e armazenamento

- Autorização no backend.
- Rejeição de consultas não permitidas.
- Proteção das credenciais.
- Ausência de SQL e resultados nos armazenamentos persistentes.
- Descarte dos dados temporários.

Utilizar dados sintéticos nos testes automatizados. Validações com dados reais exigem ambiente e acesso autorizados.

Também deverão existir avaliações com o modelo real. Provedores simulados validam contratos e execução, mas não comprovam a capacidade real de interpretação.

## 17. Etapas com nossa validação

### Etapa 0 — Levantamento e proposta

Antes de implementar:

- Inspecionar o projeto e o ambiente disponíveis.
- Identificar o que já existe.
- Levantar módulos do Protheus e personalizações.
- Confirmar as fontes do dicionário.
- Propor o domínio inicial.
- Propor tecnologias e organização.
- Listar decisões e informações faltantes.
- Definir os critérios de aceite das próximas etapas.

**Não implementar as demais etapas antes de validarmos esta proposta.**

### Etapa 1 — Fundação em localhost

Implementar:

- Projeto Django.
- Interface Web mínima.
- Configuração local.
- Autenticação inicial.
- Banco próprio da aplicação.
- Dados sintéticos.
- Instruções reproduzíveis de execução.

**Aguardar nossa validação.**

### Etapa 2 — Autorização e memória de parâmetros

Implementar:

- Usuários, setores e permissões.
- Contextos de solicitação.
- Definições e valores de parâmetros.
- Validade, correções e escopos.
- Auditoria sanitizada.

**Aguardar nossa validação.**

### Etapa 3 — Conhecimento e metadados

Implementar:

- Recuperação dos documentos aprovados.
- Adaptadores de metadados.
- Descoberta progressiva.
- Restrições de acesso.

Começar com fontes sintéticas. Utilizar o ambiente Protheus autorizado após nossa liberação.

**Aguardar nossa validação.**

### Etapa 4 — Agente e esclarecimentos

Implementar:

- Provedor de IA configurável.
- Interpretação estruturada.
- Recuperação de contexto.
- Contratos de parâmetros.
- Perguntas de esclarecimento.
- Validação de completude.
- Tratamento de falhas do modelo.

**Aguardar nossa validação.**

### Etapa 5 — Consulta de leitura

Implementar:

- Geração temporária de SQL.
- Validação.
- Execução parametrizada.
- Limites.
- Verificação de resultados.
- Apresentação sem persistência.

**Aguardar nossa validação.**

### Etapa 6 — Memórias setoriais e chat consolidado

Implementar:

- Memórias compartilhadas por setor.
- Aprovação de definições.
- Resolução de conflitos.
- Visão consolidada por permissão.

**Aguardar nossa validação.**

### Etapa 7 — Piloto local

Executar:

- Conversas reais do domínio escolhido.
- Conferência com relatórios confiáveis.
- Avaliação de interpretação.
- Medição de latência e consumo.
- Auditoria de não armazenamento.
- Correção das falhas encontradas.

**Aguardar nossa aprovação do piloto.**

### Etapa 8 — Operações de alteração

Propor e implementar somente as operações especificamente autorizadas:

- Integrações suportadas.
- Confirmações.
- Prevenção de execução duplicada.
- Auditoria.
- Verificação do estado oficial após a alteração.

**Aguardar nossa validação.**

### Etapa 9 — Preparação para acesso externo

Propor:

- Acesso por rede corporativa, VPN ou publicação.
- Servidor de produção e proxy reverso.
- HTTPS.
- Autenticação.
- Configurações Django de produção.
- Gestão de credenciais.
- Monitoramento e limites.

Manter SQL Server e serviços internos sem exposição pública direta.

**Não publicar nem abrir portas sem nossa autorização explícita.**

## 18. Critérios gerais de aceite

O sistema deverá demonstrar que:

- Pergunta quando falta informação necessária.
- Preserva os parâmetros confirmados sem assumir valores indevidos.
- Respeita permissões em todas as etapas.
- Consulta os dados na origem.
- Trata as particularidades do domínio Protheus validado.
- Não persiste SQL gerado nem resultados.
- Separa memória privada, setorial e geral.
- Registra correções de forma rastreável.
- Não declara uma operação concluída antes da confirmação da integração.
- Funciona em localhost antes de qualquer publicação.

## Sua primeira tarefa

**Execute somente a Etapa 0.**

Apresente:

1. Seu entendimento do projeto.
2. O que precisa inspecionar.
3. A arquitetura proposta.
4. As decisões técnicas que precisam da nossa validação.
5. As informações indispensáveis que ainda faltam.
6. O escopo e os critérios de aceite da Etapa 1.

Depois, aguarde nossa aprovação para começar a implementação.

---