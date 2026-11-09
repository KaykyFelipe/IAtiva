# STATUS — IAtiva

**Atualizado em:** 2026-11-09
**Etapa atual:** 2 — Autorização e memória de parâmetros
**Situação:** **não iniciada.** Etapa 1 aprovada; a Etapa 2 depende das informações pendentes abaixo (setores e perfis).

## Histórico

- Etapa 0 — Levantamento e proposta: **aprovada** em 2026-11-09 (D1–D12; ver `DECISOES.md`).
- Etapa 1 — Fundação em localhost: **aprovada** em 2026-11-09 (inclui T1–T7, M1). Commit de segurança antes da aprovação (M2). Pull request para `main` aberto, sem merge (M3).

## Etapa 1 — o que foi entregue

- Projeto Django 5.2 (`config/`, `iativa/core`, `iativa/chat`, `iativa/synthetic_erp`).
- Interface mínima (templates + htmx local): login, chat que confirma recebimento sem armazenar, logout.
- Configuração via `.env` (`.env.example` versionado; `.env` ignorado).
- Banco da aplicação PostgreSQL 16; base sintética do ERP separada e somente leitura (`manage.py seed_synthetic`).
- Restrições: `runserver` só em 127.0.0.1 (exceção controlada em container, T6); verificações do sistema; `DEBUG` fixo em `False`; `no-store`; CSP; filtro de redação de logs.
- Ambiente de desenvolvimento em containers (`Dockerfile` Ubuntu 24.04 + `compose.yaml`), portas só em 127.0.0.1.
- 82 testes automatizados (`pytest`), sem rede.

## Pendências da Etapa 1 (não bloqueiam a Etapa 2)

- [ ] Executar no servidor Windows: `docker compose up -d --build` e `docker compose run --rm app pytest -p no:cacheprovider` (ainda não executado em Docker real).
- [ ] Confirmar a versão do Windows Server e se o Docker roda em WSL2 ou em VM Ubuntu.
- [ ] Revisar e fazer o merge do pull request para `main` (decisão de vocês).

## Informações pendentes

Necessárias para a Etapa 2:

1. Lista de setores e perfis iniciais (e quem aprova conhecimento em cada nível).
2. Quem terá a visão consolidada (diretoria/TI) e de quais setores.

Necessárias a partir da Etapa 3 (equipe do ERP):

3. Versão do Protheus e do SQL Server; collation.
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

Receber as informações 1 e 2, apresentar o plano da Etapa 2 e iniciá-la.
