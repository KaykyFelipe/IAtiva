"""M125: receita verificada de um TIPO de pedido, reaproveitada em pedidos equivalentes sem código novo.

Quando uma resposta passa pela conferência (contrato cumprido + revisor), a lógica que a produziu vira uma nota
técnica da empresa (NotaAgente tipo termo, `criterios = {"receita": {...}}`):
- intenção: assinatura do pedido (métrica, eventos citados, interseção, agrupamento, lista), sem o período;
- fonte: a consulta aprovada usada (código e tabelas) e os parâmetros, com o ano do pedido trocado por {ANO};
- definição da métrica, evidência (tipo, colunas, revisada) e versão do sistema que a validou.
Nunca linhas, valores ou nomes do ERP: os dados ficam no histórico autorizado da conversa.

Num pedido equivalente, a receita vira o PRIMEIRO passo (sem chamar o modelo), só se a pessoa tiver acesso à
consulta (lista `objetivos` já filtrada pelas permissões dela). O resultado passa pelas mesmas conferências; se
falhar ou for reprovado, o ciclo segue com o modelo e a receita reprovada é desativada.
"""
import json
import os
import re

from .periodo import _norm

from .fidelidade import _EVENTO as _EV

_EVENTO = re.compile(rf'\b({_EV})\b')
_NAO_EVENTO = {'pedi', 'quan', 'perio', 'ida', 'cada', 'part'}
_ANO = re.compile(r'\b((?:19|20)\d{2})\b')
_ACOES = ('preparar_consulta', 'executar_consulta')


def assinatura(texto: str) -> dict | None:
    from . import fidelidade
    t = _norm(texto)
    eventos = sorted({w[:4] for w in _EVENTO.findall(t) if w[:4] not in _NAO_EVENTO})[:4]
    if not eventos or not fidelidade._CONTA.search(t) and not fidelidade.lista_pedida(None, texto):
        return None
    return {"metrica": "contagem" if fidelidade._CONTA.search(t) else "lista", "eventos": eventos,
            "intersecao": bool(fidelidade.eventos_intersecao(texto)),
            "agrupamento": sorted(set(fidelidade._AGRUPAMENTO.findall(t))),
            "lista": fidelidade.lista_pedida(None, texto)}


def _chave(sig: dict) -> str:
    return json.dumps(sig, sort_keys=True, ensure_ascii=False)


def _ano(texto: str) -> str:
    anos = sorted(set(_ANO.findall(_norm(texto))))
    return anos[0] if len(anos) == 1 else ""


def _notas():
    from .models import EscopoNota, NotaAgente, SituacaoNota, TipoNota
    return NotaAgente.objects.filter(ativo=True, escopo=EscopoNota.EMPRESA, situacao=SituacaoNota.APROVADA,
                                     tipo=TipoNota.TERMO, criterios__has_key="receita")


def guardar(user, texto: str, d, saida, contrato: list, ev: str):
    """Grava (ou confirma) a receita de um pedido cuja resposta foi conferida. Falha aqui nunca afeta a resposta."""
    from django.utils import timezone

    from .contratos import OBJETIVOS
    from .models import EscopoNota, NotaAgente, SituacaoNota, TipoNota
    res = getattr(saida, "resultado", None)
    sig = assinatura(texto)
    if (sig is None or d.acao not in _ACOES or d.objetivo not in OBJETIVOS or not OBJETIVOS[d.objetivo].modelo_id
            or len(contrato or []) > 1 or not getattr(res, "ok", False) or getattr(res, "consolidado", False)):
        return None
    ano = _ano(texto)
    parametros = [[k, v.replace(ano, "{ANO}") if ano else v] for k, v in d.parametros]
    if not ano and any(_ANO.search(v) for _, v in d.parametros):
        return None  # período fixo que não veio do pedido: não generaliza
    receita = {"assinatura": sig, "objetivo": d.objetivo, "tabelas": sorted(getattr(res, "escopo", {}).get("campos", {}))[:8],
               "parametros": parametros, "metrica": getattr(res, "medida_calculada", "")
               or dict(d.parametros).get("agregacao", ""),
               "evidencia": {"tipo": ev, "colunas": [str(c)[:40] for c in getattr(res, "colunas", [])][:12],
                             "revisada": True},
               "versao": os.environ.get("IATIVA_VERSAO", "dev"), "verificada_em": timezone.now().isoformat()}
    chave = _chave(sig)
    for n in _notas():
        r = n.criterios["receita"]
        if _chave(r.get("assinatura", {})) == chave:
            if r.get("objetivo") == receita["objetivo"] and r.get("parametros") == parametros:
                n.criterios = {**n.criterios, "receita": {**r, "versao": receita["versao"],
                                                          "verificada_em": receita["verificada_em"],
                                                          "usos": int(r.get("usos", 0)) + 1}}
                n.save(update_fields=["criterios"])
                return n
            n.ativo, n.situacao = False, SituacaoNota.SUBSTITUIDA
            n.save(update_fields=["ativo", "situacao"])
    desc = (contrato[0].get("descricao", "") if contrato else "") or " e ".join(sig["eventos"])
    nome = OBJETIVOS[d.objetivo].nome
    texto_nota = (f"Receita verificada: “{desc[:120]}” usa a consulta “{nome[:80]}” "
                  f"({', '.join(k for k, _ in parametros)[:80]}).")
    return NotaAgente.objects.create(
        texto=texto_nota[:300], escopo=EscopoNota.EMPRESA, situacao=SituacaoNota.APROVADA, tipo=TipoNota.TERMO,
        criterios={"receita": {**receita, "usos": 1}}, criado_por=user, aprovado_por=None, aprovado_em=timezone.now(),
        automatica=True)


def receita_para(texto: str, objetivos) -> tuple:
    """(Decisao, id da nota) da receita verificada para este tipo de pedido, ou (None, None)."""
    sig = assinatura(texto)
    if sig is None:
        return None, None
    chave, ano = _chave(sig), _ano(texto)
    permitidos = {o.codigo for o in objetivos}
    from .decisao import Decisao
    for n in _notas().order_by("-pk"):
        r = n.criterios["receita"]
        if _chave(r.get("assinatura", {})) != chave or r.get("objetivo") not in permitidos:
            continue
        params = [(str(k), str(v)) for k, v in r.get("parametros", [])]
        if any("{ANO}" in v for _, v in params):
            if not ano:
                continue
            params = [(k, v.replace("{ANO}", ano)) for k, v in params]
        return Decisao("Uso a receita já verificada para este tipo de pedido.", "preparar_consulta",
                       r["objetivo"], tuple(params), "", "", False, ()), n.pk
    return None, None


def descartar(nota_id):
    """Receita que não serviu mais (reprovada pelo revisor ou sem resultado): sai de uso."""
    from .models import NotaAgente, SituacaoNota
    if nota_id:
        NotaAgente.objects.filter(pk=nota_id).update(ativo=False, situacao=SituacaoNota.SUBSTITUIDA)
