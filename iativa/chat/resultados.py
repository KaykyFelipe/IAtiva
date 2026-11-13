"""M44: snapshots de apresentação autorizados por Diego. Sem SQL ou envio ao modelo.
O acesso a cada bloco é reconferido contra políticas, filiais e restrições atuais."""
from types import SimpleNamespace

from iativa.accounts.authz import Permissao, filiais_autorizadas, tem_permissao
from iativa.accounts.models import Department
from iativa.metadata.descoberta import _Visibilidade
from iativa.query.servico import Resultado
from iativa.memory.validadores import _CPF_RE, _CNPJ_RE


def _texto(valor):
    return _CNPJ_RE.sub('[documento omitido]', _CPF_RE.sub('[documento omitido]', str(valor)))


def empacotar(res):
    if res is None:
        return {}
    if getattr(res, "consolidado", False):
        blocos = [{"titulo": getattr(b, "titulo", "") or getattr(b, "setor", ""),
                   "resultado": empacotar(b.resultado), "mensagem": b.mensagem} for b in res.blocos]
        if any(b.resultado is not None and not p["resultado"] for b, p in zip(res.blocos, blocos)):
            return {}
        return {"consolidado": True, "resumo": _texto(res.resumo), "blocos": blocos}
    if not getattr(res, "ok", False) or not res.escopo:
        return {}
    return {"mensagem": _texto(res.mensagem), "colunas": [_texto(c) for c in res.colunas], "tipos": res.tipos,
            "linhas": [[_texto(v) if v is not None else "" for v in l] for l in res.linhas],
            "premissas": [_texto(p) for p in res.premissas], "escopo": res.escopo,
            "periodos": [{k: _texto(v) if isinstance(v, str) else v for k, v in p.items()} for p in res.periodos],
            "truncado": res.truncado, "moedas": res.totais.moedas if res.totais else [],
            "medida_calculada": res.medida_calculada}


def guardar(msg, res):
    dados = empacotar(res)
    if dados:
        msg.resultado = dados
        msg.save(update_fields=["resultado"])
        msg.conversa.__class__.objects.filter(pk=msg.conversa_id).update(preservar_historico=True)


def _permitido(user, escopo):
    setor = Department.objects.filter(pk=escopo.get("setor"), ativo=True).first()
    if not setor or not tem_permissao(user, Permissao.CONSULTAR, setor):
        return False
    filiais = escopo.get("filiais") or []
    if not filiais or not set(filiais) <= filiais_autorizadas(user, setor):
        return False
    campos = escopo.get("campos") or {}
    vis = _Visibilidade({setor.pk}, list(campos))
    return bool(campos) and all(vis.tabela(a) and all(vis.campo(a, c) for c in cs) for a, cs in campos.items())


def restaurar(user, dados):
    if not dados:
        return None
    if dados.get("consolidado"):
        blocos = [SimpleNamespace(titulo=b["titulo"], resultado=restaurar(user, b["resultado"]), mensagem=b["mensagem"])
                  for b in dados["blocos"]]
        # O resumo pode conter totais de todos os setores, inclusive de um bloco agora inacessível.
        if any(b["resultado"] and r.resultado is None for b, r in zip(dados["blocos"], blocos)):
            return None
        return SimpleNamespace(consolidado=True, resumo=dados["resumo"], blocos=blocos)
    if not _permitido(user, dados.get("escopo", {})):
        return None
    return Resultado(True, dados["mensagem"], colunas=dados["colunas"], tipos=dados["tipos"],
                     linhas=dados["linhas"], premissas=dados["premissas"], escopo=dados['escopo'],
                     truncado=dados.get('truncado', False), periodos=dados.get('periodos', []),
                     medida_calculada=dados.get('medida_calculada', ''))
