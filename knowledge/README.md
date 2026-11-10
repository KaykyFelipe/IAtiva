# Conhecimento aprovado

Documentos que o IAtiva pode recuperar para responder perguntas. Só entram na busca os que têm
`situacao: aprovado`, cabeçalho completo e nenhum conteúdo proibido. Este README não tem cabeçalho
e por isso não é carregado.

## Formato

```text
---
titulo: Nome do documento
dominio: financeiro_receber
origem: sintetico | erp | negocio | personalizacao
versao: 1
responsavel: setor ou pessoa responsável pelo conteúdo
situacao: rascunho | aprovado | obsoleto
aprovado_por: quem aprovou (obrigatório se aprovado)
aprovado_em: AAAA-MM-DD (obrigatório se aprovado)
setores: financeiro-cobranca, diretoria   (opcional; vazio = setores habilitados no domínio)
tabelas: SE1, SE5                          (opcional; tabelas citadas)
---

## Seção
Texto.
```

Cada seção (`#`, `##`, `###`) vira um trecho de busca. Escreva seções curtas e autossuficientes.

## Regras

- Não colocar resultados de consultas, dados cadastrais do ERP, credenciais ou transcrições.
- Documentos com `origem: sintetico` descrevem só a base sintética e nunca são usados com o ERP real
  (e vice-versa).
- Confira com `python manage.py validar_conhecimento` antes do commit.

## Pastas

- `protheus/` — particularidades técnicas do Protheus.
- `glossario/` — termos de negócio.
- `procedimentos/` — regras e procedimentos aprovados.
- `personalizacoes/` — campos e tabelas personalizados.
