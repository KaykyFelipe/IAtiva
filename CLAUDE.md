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
- Commit e push seguem a decisão D8 em `DECISOES.md`: um commit por etapa, somente após aprovação, na branch `claude/new-session-w616rq`, sem pull request salvo pedido.
