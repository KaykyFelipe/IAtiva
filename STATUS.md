# STATUS — IAtiva

**Atualizado em:** 2026-11-09
**Etapa atual:** 1 — Fundação em localhost
**Situação:** implementada; **aguardando validação explícita**. A Etapa 2 não deve ser iniciada antes da aprovação.

## Histórico

- Etapa 0 — Levantamento e proposta: **aprovada** em 2026-11-09 (D1–D12; ver `DECISOES.md`).
- Etapa 1 — Fundação em localhost: implementada em 2026-11-09; enviada à branch por commit de segurança (exceção M2); aguardando validação.

## Etapa 1 — o que foi entregue

- Projeto Django 5.2 (`config/`, `iativa/core`, `iativa/chat`, `iativa/synthetic_erp`).
- Interface mínima (templates + htmx local): login, chat que confirma recebimento sem armazenar, logout.
- Configuração via `.env` (`.env.example` versionado; `.env` ignorado).
- Banco da aplicação PostgreSQL 16 (`compose.yaml` opcional, porta só em 127.0.0.1).
- Base sintética do ERP (`manage.py seed_synthetic`), separada e somente leitura.
- Restrições: `runserver` só em 127.0.0.1; verificações do sistema; `DEBUG` fixo em `False`; `no-store`; CSP; filtro de redação de logs.
- Ambiente de desenvolvimento em containers (`Dockerfile` Ubuntu 24.04 + `compose.yaml` com PostgreSQL 16), portas só em 127.0.0.1 — solicitado para o servidor Windows. **Ainda não executado em Docker real** (o ambiente de desenvolvimento da Claude não tem Docker).
- 82 testes automatizados (`pytest`), sem rede.
- README com instalação (Linux/macOS/Windows), execução e testes.

## Checklist de validação da Etapa 1

- [ ] `runserver` só em `127.0.0.1`; check do sistema falha se `ALLOWED_HOSTS`/bind fugirem de localhost
- [ ] Páginas exigem autenticação (exceto login); CSRF; cookies `HttpOnly`/`SameSite`
- [ ] Banco da aplicação e ERP sintético distintos; ERP sintético somente leitura (com teste)
- [ ] Dados sintéticos fictícios e reprodutíveis, com exclusão lógica, várias filiais, chave composta, campo personalizado
- [ ] `Cache-Control: no-store`; sem storage do navegador (com teste)
- [ ] Filtro de redação de logs; página técnica de erro desativada
- [ ] Nenhum segredo no repositório
- [ ] `pytest` passa sem rede
- [ ] README reproduz tudo do zero numa máquina limpa (validar na máquina de vocês)
- [ ] `docker compose up -d --build` sobe banco + aplicação no servidor; chat abre em http://127.0.0.1:8000/ no servidor
- [ ] `docker compose run --rm app pytest -p no:cacheprovider` passa no servidor
- [ ] Decisões técnicas T1–T7 e mudança de escopo M1 (`DECISOES.md`)

## Informações pendentes

Necessárias para a Etapa 2:

1. Lista de setores e perfis iniciais (e quem aprova conhecimento em cada nível).
2. Servidor: Windows com containers Linux (informado em 2026-11-09). Falta confirmar a versão do Windows Server e se o Docker roda em WSL2 ou em VM Ubuntu.

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

Aguardar validação da Etapa 1. Após aprovação: commit da etapa (D8), registrar em `DECISOES.md` e iniciar a Etapa 2.
