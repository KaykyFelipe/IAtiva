"""Receitas aprendidas em Markdown legível (M95).

Fonte canônica da receita executável: a consulta aprendida (`ConsultaModelo`, no banco). Este módulo gera, a partir
dela, um Markdown por receita e versão no volume persistente (`IATIVA_RECEITAS_DIR/<codigo>/v<versao>-<hash>.md` e
`<codigo>/atual.md`). Pode ser reconstruído a qualquer momento (`manage.py gerar_receitas`); aprender no chat só
grava o conhecimento validado (banco + Markdown), sem commit, push ou deploy.

O Markdown guarda a RECEITA: fontes, campos, relações validadas, critérios, parâmetros e prova esperada. Nunca
registros do ERP, nomes reais, documentos, credenciais ou valores de resultado. A referência pública (memória
técnica do Protheus) fica separada: aqui só a versão dela usada na validação.
"""
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def pasta() -> Path:
    return Path(getattr(settings, "IATIVA_RECEITAS_DIR", Path(settings.BASE_DIR) / "var" / "receitas"))


def versao(cm) -> int:
    return len(cm.spec.get("anteriores", [])) + 1


def assinatura(cm) -> str:
    """Hash da parte executável (o que muda a receita); muda junto com filtros, colunas e junções."""
    nucleo = {k: cm.spec.get(k) for k in ("colunas", "juncoes", "filtros_fixos", "campo_data", "campo_busca",
                                          "data_aniversario")}
    if "operacao_validada" in cm.spec:
        nucleo["operacao_validada"] = cm.spec["operacao_validada"]
    return hashlib.sha256(json.dumps([cm.tabela, nucleo], sort_keys=True, default=str).encode()).hexdigest()[:10]


def _escopo(cm) -> str:
    from iativa.accounts.models import Department
    codigos = sorted(cm.setores.values_list("codigo", flat=True))
    ativos = set(Department.objects.filter(ativo=True).values_list("codigo", flat=True))
    if codigos and set(codigos) >= ativos:
        return "corporativo (todos os setores)"
    return "setorial: " + ", ".join(codigos) if codigos else "sem setor (não executa)"


def conteudo(cm) -> str:
    from .contratos import OBJETIVOS, campo_busca, campos_filtraveis
    from iativa.knowledge import memoria_tecnica as mt
    obj = OBJETIVOS[cm.codigo] if cm.codigo in OBJETIVOS else None
    parametros = list(obj.parametros) if obj is not None else []
    opcionais = list(getattr(obj, "opcionais", ()) or ()) if obj is not None else []
    spec = cm.spec
    memoria = mt.recuperar(aliases=[cm.tabela] + [j["tabela"] for j in spec.get("juncoes", [])])
    linhas = [
        f"# Receita: {cm.nome}", "",
        f"- Identificador: `{cm.codigo}`",
        f"- Versão: {versao(cm)} (assinatura `{assinatura(cm)}`)",
        f"- Estado: {cm.get_situacao_display()}" + (f", aprovada em {cm.aprovado_em:%d/%m/%Y}" if cm.aprovado_em else ""),
        f"- Escopo: {_escopo(cm)}",
        f"- Intenção: {cm.descricao or cm.nome}",
        f"- Sinônimos: {', '.join(spec.get('sinonimos', [])) or cm.nome.lower()}",
        f"- Versão dos metadados: memória técnica `{memoria.versao or memoria.estado}`; estrutura confirmada no "
        "dicionário local da instalação a cada execução",
        "", "## Parâmetros reutilizáveis", "",
        *(f"- `{p}` (obrigatório)" for p in parametros),
        *(f"- `{p}` (opcional)" for p in opcionais),
        "", "Mês, período, filial e pessoa são parâmetros: não criam receita nova.",
        "", "## Fontes e campos", "",
        f"- Tabela principal: `{cm.tabela}`",
        *(f"- Junção validada: `{j['tabela']}` por " + ", ".join(f"`{a}` = `{b}`" for a, b in j.get("pares", []))
          for j in spec.get("juncoes", [])),
        "", "| Campo | Rótulo | Tipo |", "| --- | --- | --- |",
        *(f"| `{c['campo']}` | {c.get('rotulo', '')} | {c.get('tipo', 'texto')} |" for c in spec.get("colunas", [])),
        "", "## Critérios obrigatórios", "",
        "- Registros excluídos logicamente (`D_E_L_E_T_`) ignorados; filial aplicada pelo escopo de quem pergunta.",
        *(f"- `{f['campo']}` {f.get('op', '=')} `{f['valor']}`" for f in spec.get("filtros_fixos", [])),
        *([f"- Período no campo `{spec['campo_data']}`" + (" (mês de qualquer ano: aniversário)"
                                                            if spec.get("data_aniversario") else "")]
          if spec.get("campo_data") else []),
        *([f"- Busca por nome em `{campo_busca(spec)}`"] if campo_busca(spec) else []),
        *([("- Filtros por coluna (exato `=` ou contém `~`): " + ", ".join(f"`{c}`" for c, _ in campos_filtraveis(spec)))]
          if campos_filtraveis(spec) else []),
        "", "## Prova esperada", "",
        "- O resultado traz as colunas acima (as entregas pedidas são conferidas pelo campo de origem).",
        "- Zero linhas é resposta válida e dita como tal; nunca é trocada por outra tabela.",
        "", "## Exemplos sintéticos", "",
        f"- \"{cm.nome} do mês passado na filial 01\"",
        f"- \"{cm.nome} deste ano em todas as filiais\"",
        "",
    ]
    if calculo := spec.get("operacao_validada"):
        linhas += ["## Operação validada", "", f"- Operação: `{calculo['agregacao'] or 'listagem'}`",
                   f"- Chave local: {', '.join(calculo['chave_local'])}",
                   f"- Campo de período: {calculo['campo_data']}",
                   f"- Dicionário local: {calculo['dicionario_local']}.",
                   "- Evidência: execução completa no escopo autorizado.",
                   *(f"- `{cod}` — {nome}" for cod, nome in calculo["dominio"].items()), ""]
    return "\n".join(linhas)


def registrar_validacao(cm, prova):
    """A primeira execução completa documenta o plano. Reexecutar com outro período mantém a versão."""
    from django.db import transaction
    from .models import ConsultaModelo
    if cm.spec.get("operacao_validada"):
        return
    with transaction.atomic():
        atual = ConsultaModelo.objects.select_for_update().get(pk=cm.pk)
        if atual.situacao == "aprovada" and atual.spec == cm.spec:
            atual.spec = {**atual.spec, "operacao_validada": prova}
            atual.save(update_fields=["spec"])


def gerar(cm) -> Path | None:
    """Grava (atômico) `v<versao>-<assinatura>.md` e `atual.md`. Falha aqui nunca interrompe o atendimento."""
    try:
        destino = pasta() / cm.codigo
        destino.mkdir(parents=True, exist_ok=True)
        texto = conteudo(cm)
        arquivo = destino / f"v{versao(cm)}-{assinatura(cm)}.md"
        for alvo in (arquivo, destino / "atual.md"):
            with tempfile.NamedTemporaryFile(dir=destino, suffix=".tmp", delete=False) as f:
                tmp = Path(f.name)
            try:
                tmp.write_text(texto, encoding="utf-8")
                os.replace(tmp, alvo)
            finally:
                tmp.unlink(missing_ok=True)
        return arquivo
    except Exception as exc:
        logger.warning("receita %s não gerada: %s", getattr(cm, "codigo", "?"), type(exc).__name__)
        return None


def reconstruir() -> int:
    from .models import ConsultaModelo
    return sum(1 for cm in ConsultaModelo.objects.exclude(situacao="removida") if gerar(cm) is not None)
