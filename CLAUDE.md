# Instruções para a Claude neste repositório

Antes de qualquer trabalho, leia nesta ordem:

1. `STATUS.md` — etapa atual, pendências e validações.
2. `DECISOES.md` — decisões tomadas e mudanças de escopo.
3. `PLANO.md` — plano (verifique se está marcado como aprovado).
4. `docs/PEDIDO_INICIAL.md` — requisitos e regras obrigatórias originais.

Regras de trabalho:

- Trabalhe somente no escopo da etapa atual indicada em `STATUS.md`; não avance sem aprovação explícita registrada em `DECISOES.md`.
- Ao terminar uma interação que mude o estado do projeto, atualize `STATUS.md` (e `DECISOES.md` quando houver decisão ou mudança de escopo).
- Nunca registre nesses arquivos SQL gerado, resultados, dados do ERP, credenciais ou transcrições completas.
- Commit e push seguem a decisão M7 em `DECISOES.md` (substitui a D8): commit e push direto na `main`, sem pull request salvo pedido. A `main` dispara a atualização automática do servidor (M4), então rode `pytest` antes de cada push.
