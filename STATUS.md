# STATUS — IAtiva

**Atualizado em:** 2026-11-10
**Etapa atual:** 3 — Conhecimento e metadados
**Situação:** **aguardando aprovação do plano.** Etapa 2 aprovada em 2026-11-10. Informação 3 (versões e collation) recebida; informações 4–8 pendentes.

## Histórico

- Etapa 0 — Levantamento e proposta: **aprovada** em 2026-11-09 (D1–D12; ver `DECISOES.md`).
- Etapa 1 — Fundação em localhost: **aprovada** em 2026-11-09 (inclui T1–T7, M1). Commit de segurança antes da aprovação (M2). Pull request KaykyFelipe/IAtiva#1 com merge na `main` (M3).
- Etapa 2 — Autorização e memória de parâmetros: **aprovada** em 2026-11-10 (T8–T13). Migrações aplicadas no PostgreSQL do servidor.

## Etapa 2 — o que foi entregue

- `iativa/accounts`: `Department`, `UserProfile` (setor e função informativa), `AccessPolicy` (permissões separadas por setor, filiais, vigência). Autorização em `authz.py`; sem permissão universal; superusuário e função não concedem acesso a dados.
- `iativa/chat`: `ConversationContext`, `RequestContext` (objetivo como código estruturado, nunca texto) e máquina de estados; checagem determinística de completude (`avaliar_completude`).
- `iativa/memory`: `ParameterDefinition`, `ParameterBinding` (escopos solicitação/usuário/setor/empresa, aprovação, validade, versão), `MemoryRevision`; resolução por precedência (D12) com conflitos; dados pessoais só na solicitação (30 dias); CPF/CNPJ recusados.
- `iativa/audit`: `OperationAudit` imutável, só códigos e contagens; comando `expurgar_retencao`.
- Comando `seed_exemplos` (setores e definições fictícios). Admin com aprovação/rejeição de propostas via serviços.
- 161 testes passando (10 da rotina M4 pulados no Windows), sem rede.

## Pendências da Etapa 2 (não bloqueiam a Etapa 3)

- [ ] Lista real de setores e perfis; quem aprova em cada nível; quem tem visão consolidada (informações 1 e 2). Hoje: exemplos fictícios.
- [ ] Agendar `expurgar_retencao` diariamente no servidor.
- [ ] Solicitações abandonadas (não encerradas) não são expurgadas; definir política.
- [ ] Interface de usuário para memória/aprovação: hoje só pelo admin (API e telas nas Etapas 4 e 6).

## Etapa 1 — o que foi entregue

- Projeto Django 5.2 (`config/`, `iativa/core`, `iativa/chat`, `iativa/synthetic_erp`).
- Interface mínima (templates + htmx local): login, chat que confirma recebimento sem armazenar, logout.
- Configuração via `.env` (`.env.example` versionado; `.env` ignorado).
- Banco da aplicação PostgreSQL 16; base sintética do ERP separada e somente leitura (`manage.py seed_synthetic`).
- Restrições: `runserver` só em 127.0.0.1 (exceção controlada em container, T6); verificações do sistema; `DEBUG` fixo em `False`; `no-store`; CSP; filtro de redação de logs.
- Ambiente de desenvolvimento em containers (`Dockerfile` Ubuntu 24.04 + `compose.yaml`), portas só em 127.0.0.1.
- 82 testes automatizados (`pytest`), sem rede (92 com a rotina M4).

## Aprovado fora das etapas

- **M4 — Atualização automática a partir do GitHub** (`scripts/atualizar.sh`, `scripts/instalar_agendamento.sh`): aprovada em 2026-11-09; testada com Docker simulado (10 testes). Falta executar no servidor.
- **M5 — Servidor Ubuntu nativo**: aprovada em 2026-11-09; `compose.yaml` sem alteração; acesso por túnel SSH.

## Pendências da Etapa 1 (não bloqueiam a Etapa 2)

- [ ] Executar no servidor Ubuntu (a partir da `main`): `docker compose up -d --build` e `docker compose run --rm app pytest -p no:cacheprovider` (ainda não executado em Docker real).
- [ ] Servidor alterado para Ubuntu nativo (informado em 2026-11-09). Confirmar versão do Ubuntu e instalação do Docker Engine.
- [ ] Confirmar se o repositório é privado (o servidor precisará de credencial somente leitura para a atualização automática).

## Informações pendentes

Necessárias para a Etapa 2:

1. Lista de setores e perfis iniciais (e quem aprova conhecimento em cada nível).
2. Quem terá a visão consolidada (diretoria/TI) e de quais setores.

Necessárias a partir da Etapa 3 (equipe do ERP):

3. ~~Versão do Protheus e do SQL Server; collation.~~ Recebida em 2026-11-10: Protheus 12.1.2510; SQL Server 2022 (16.0.4212.1); collation `Latin1_General_BIN` (comparação binária: diferencia maiúsculas/minúsculas e acentos; ordenação por código de caractere).
4. Forma suportada de acesso ao dicionário (SX2, SX3, SIX, SX9, SX5, SX6 — banco ou arquivos).
5. Estrutura de empresas/filiais (SM0), sufixo das tabelas e modo de compartilhamento de SA1, SE1, SE5.
6. Documentação das personalizações do domínio.
7. Base de homologação disponível e responsável pelo login somente leitura.
8. Responsável de negócio no Financeiro para aprovar definições (ex.: "inadimplente").

Necessárias antes da Etapa 4:

9. Provedor de IA, condições contratuais e posição do DPO/LGPD (D5, D6).

Necessárias antes da Etapa 7 (piloto):

10. Prazos de retenção de auditoria e revisões (D11).

## Próximo passo

Aprovar o plano da Etapa 3 (começa só com fontes sintéticas). Informações 4–8 quando disponíveis; o Protheus real só entra com liberação explícita.
