"""M129: tabela personalizada com os dados que a pessoa pediu, montada com consultas que a IA já sabe fazer.

"Quantos funcionários foram admitidos e desligados em setembro e outubro, por filial?" não precisa de código novo:
cada evento já tem consulta aprovada (o campo de data dela é o evento). Aqui:
1. lê o pedido: eventos citados ("admitidos e desligados"), períodos (um ou dois meses/anos) e agrupamento
   (por filial);
2. acha, entre as consultas que a pessoa pode usar, a consulta de cada evento (rótulo do campo de data com a raiz
   da palavra do pedido; prefere a sem filtro fixo, que não esconde quem já saiu);
3. conta no banco cada evento em cada período, pelo mesmo caminho autorizado (permissões, filiais, auditoria);
4. junta numa tabela só: grupo | Evento - período | ... e o total de cada coluna no rodapé.
Sem consulta para algum evento, devolve None e o ciclo normal decide. Interseção ("no mesmo ano") não é tabela:
é UMA contagem com as duas datas (fidelidade.py).
"""
import re
from .periodo import _norm, periodo_da_mensagem

_CONTA = re.compile(r'\b(quantos|quantas|quantidade|numero de|total de)\b')
_POR = re.compile(r'\bpor\s+filia(?:l|is)\b|\bem cada filial\b')


def eventos(texto: str) -> list[str]:
    """Palavras dos eventos citados juntos ("admitidos e desligados", "admitidos, transferidos e desligados")."""
    from .fidelidade import _EVENTO
    t = _norm(texto)
    m = re.search(rf'\b((?:{_EVENTO})(?:\s*,\s*(?:{_EVENTO}))*\s+e\s+(?:{_EVENTO}))\b', t)
    if not m:
        return []
    palavras = [w for w in re.split(r'\s*,\s*|\s+e\s+', m.group(1)) if w]
    return palavras if len({w[:4] for w in palavras}) == len(palavras) else []


def _periodos(texto: str):
    from datetime import date

    from django.utils import timezone

    from .comparacao import _MES_NOME, _mes
    from .periodo import MESES
    t = _norm(texto)
    hoje = timezone.localdate()
    meses = list(re.finditer(_MES_NOME, t))
    if 1 <= len(meses) <= 3:
        ano = next((int(m.group(2)) for m in meses if m.group(2)), hoje.year)
        return [_mes(int(m.group(2) or ano), MESES[m.group(1)]) for m in meses]
    p = periodo_da_mensagem(texto, hoje)
    if p and '..' in p:
        a, b = p.split('..')
        ini = date(int(a[6:10]), int(a[3:5]), int(a[:2]))
        fim = date(int(b[6:10]), int(b[3:5]), int(b[:2]))
        ano_todo = (ini.month, ini.day) == (1, 1) and ini.year == fim.year
        return [(ini, fim, str(ini.year) if ano_todo else f'{ini:%d/%m/%Y} a {fim:%d/%m/%Y}')]
    return []


def pedido(texto: str, analise: dict | None = None) -> dict | None:
    """{"eventos", "rotulos", "periodos", "por_filial"} da grade pedida; None quando não é uma tabela.

    M130: vem da leitura do MODELO (analise.tabela), que entende o pedido do jeito que a pessoa falou ("separado por
    filial", "em cada unidade", "quem entrou e quem saiu", "de julho a outubro"); aqui só se valida. Sem essa
    leitura (modelo antigo, falha), as regras do texto servem de reserva."""
    from .comparacao import contagens_mensais
    from .fidelidade import eventos_intersecao
    if contagens_mensais(texto):
        return None
    spec = _do_modelo((analise or {}).get('tabela'))
    if spec is None:
        t = _norm(texto)
        from .contagem import _PEDE_DETALHE
        if not (_CONTA.search(t) or _PEDE_DETALHE.search(t)) or eventos_intersecao(texto):
            return None
        ev, per = eventos(texto), _periodos(texto)
        if len(per) < 2:
            return None  # reserva sem o modelo: só a grade evento x período (status listados não são eventos)
        so_nomes = re.search(r'\b(?:apenas|so|somente)\s+(?:os\s+)?nomes\b', t)
        spec = {"eventos": ev, "rotulos": [e.capitalize() for e in ev],
                "periodos": [(i.isoformat(), f.isoformat(), r) for i, f, r in per], "por_filial": bool(_POR.search(t)),
                "contagem": bool(_CONTA.search(t)),
                "lista": {"pedida": bool(_PEDE_DETALHE.search(t)), "campos": ["nome"] if so_nomes else []}}
    if len(spec["periodos"]) < 2 and len(spec["eventos"]) < 3:
        # Um período só: a composição de contagens separadas (contagem.py, resumo Filial | Evento | Evento com os
        # detalhes dos vínculos) já responde; aqui é a grade evento x período.
        return None
    if not spec["eventos"] or not spec["periodos"]:
        return None
    return spec


def _do_modelo(t) -> dict | None:
    from datetime import date
    if not isinstance(t, dict) or t.get('intersecao') is True:
        return None
    medidas = [m for m in (t.get('medidas') or []) if isinstance(m, dict) and str(m.get('evento', '')).strip()][:6]
    periodos = []
    for p in (t.get('periodos') or [])[:12]:
        try:
            i, f = date.fromisoformat(str(p.get('inicio'))), date.fromisoformat(str(p.get('fim')))
        except (TypeError, ValueError, AttributeError):
            return None
        if i > f:
            return None
        rot = " ".join(str(p.get('rotulo') or '').split())[:24] or f'{i:%m/%Y}'
        periodos.append((i.isoformat(), f.isoformat(), rot))
    if not medidas or not periodos:
        return None
    lista = t.get('lista') if isinstance(t.get('lista'), dict) else {}
    campos = [" ".join(str(c).split())[:30] for c in (lista.get('campos') or []) if str(c).strip()][:8]
    contagem = t.get('contagem') is not False
    if not contagem and not lista.get('pedida'):
        return None
    return {"eventos": [" ".join(str(m['evento']).split())[:60] for m in medidas],
            "rotulos": [" ".join(str(m.get('rotulo') or m['evento']).split())[:40] for m in medidas],
            "periodos": periodos, "por_filial": t.get('agrupar_por') == 'filial', "contagem": contagem,
            "lista": {"pedida": bool(lista.get('pedida')), "campos": campos}}


def _coluna(spec, k, rot):
    return rot if len(spec["eventos"]) == 1 else f'{spec["rotulos"][k]} - {rot}'


def planejar(texto: str, analise: dict) -> dict:
    """O contrato vira UMA entrega: a tabela com todos os eventos e períodos pedidos (a especificação vai junto)."""
    spec = pedido(texto, analise)
    if spec is None:
        return analise
    cols = ', '.join(_coluna(spec, k, r) for k in range(len(spec["eventos"])) for _, _, r in spec["periodos"])
    if _lista_pedida(spec):
        cols = ('lista de quem são' + (f' ({", ".join(spec["lista"]["campos"])})' if spec["lista"]["campos"] else '')
                + (f'; quantidade: {cols}' if spec.get("contagem", True) else ''))
    antes = [e for e in analise.get('entregas', []) if isinstance(e, dict) and not e.get('tabela')]
    return {**analise, 'entregas': [{
        'descricao': f'Tabela{" por filial" if spec["por_filial"] else ""}: {cols}'[:200],
        'tipo': 'calculo', 'metrica': 'contagem', 'tabela': {**spec, 'antes': antes}}]}


# Palavras do pedido que nomeiam o mesmo evento do campo de data com outra raiz.
_SINONIMOS = {'desl': 'demi', 'resc': 'demi', 'cont': 'admi'}


def _raiz(palavra: str) -> str:
    r = _norm(palavra)[:4]
    if r == 'cont' and not _norm(palavra).startswith('contratad'):
        return r
    return _SINONIMOS.get(r, r)


def _consulta_do_evento(palavra: str, modelos):
    from .contagem import _detalhavel, _evento
    palavras = [w for w in re.findall(r'[a-z]+', _norm(palavra)) if len(w) >= 4] or [_norm(palavra)]
    candidatos = [m for m in modelos if m.spec.get('campo_data')
                  and any(_raiz(w) == _raiz(_evento(m)) for w in palavras)]
    candidatos = [m for m in candidatos if _detalhavel(m)] or candidatos
    return min(candidatos, key=lambda m: (bool(m.spec.get('filtros_fixos')), m.pk)) if candidatos else None


def _lista_pedida(spec) -> bool:
    return bool((spec.get("lista") or {}).get("pedida"))


def executar(user, c, texto, defs, objetivos, interrompido=None, spec=None):
    """Saida com as tabelas pedidas, ou None se algum evento não tiver consulta que a pessoa possa usar.

    M131: "quantos ... e quais são" pede duas coisas: a lista (quem são) vem PRIMEIRO e a quantidade, tabela
    menor, por último. "Quais são ... apenas os nomes" entrega só a lista, só com os nomes."""
    from .agente import Saida
    from .models import ConsultaModelo
    spec = spec if isinstance(spec, dict) else pedido(texto)
    if spec is None:
        return None
    permitidas = {o.codigo for o in objetivos if getattr(o, 'modelo_id', None)}
    modelos = list(ConsultaModelo.objects.filter(codigo__in=permitidas, situacao='aprovada').order_by('pk'))
    consultas = [_consulta_do_evento(e, modelos) for e in spec["eventos"]]
    if any(cm is None for cm in consultas) or len({(cm.tabela, cm.spec['campo_data']) for cm in consultas}) != len(consultas):
        return None
    partes = []
    if _lista_pedida(spec):
        lista = _lista(user, c, spec, consultas, defs, interrompido)
        if lista is None:
            return None
        partes.append(("Quem são", lista))
    if spec.get("contagem", True):
        cont = _contagens(user, c, spec, consultas, defs, interrompido)
        if cont is None:
            return None
        partes.append(("Quantidade", cont))
    if not partes:
        return None
    ultima = partes[-1][1][1]
    if len(partes) == 1:
        res = partes[0][1][0]
    else:
        from .diagnostico import Bloco, Diagnostico
        res = Diagnostico([Bloco(t, r) for t, (r, _) in partes])
        res.agregado = True
    res.tabela_pedida = True  # só estas tabelas cumprem a entrega (planejamento.conferir)
    return Saida('', {'tipo': 'calculo_resultado'}, solicitacao=ultima.solicitacao, resultado=res)


def _celulas(user, c, spec, consultas, defs, interrompido, agreg, aviso):
    """Roda cada consulta (evento) em cada período, pelo caminho autorizado. None se alguma falhar."""
    from datetime import date

    from . import andamento
    from .agente import Interrompido, _preparar
    from .decisao import Decisao
    out, escopo = [], None
    for k, (palavra, cm) in enumerate(zip(spec["eventos"], consultas)):
        for ini, fim, rot in spec["periodos"]:
            if interrompido is not None and interrompido():
                raise Interrompido
            andamento.avisar(f'{aviso} {palavra} em {rot}')
            i, f = date.fromisoformat(ini), date.fromisoformat(fim)
            pares = (('filial-analise', 'TODAS'), ('periodo-analise', f'{i:%d/%m/%Y}..{f:%d/%m/%Y}'))
            if agreg:
                pares += (('agregacao', agreg),)
            s = _preparar(user, c, Decisao('', 'preparar_consulta', cm.codigo, pares, '', '', False, ()),
                          None, defs, {}, texto='')
            r = s.resultado
            if r is None or not r.ok or (agreg and r.truncado):
                return None  # uma célula falhou: o ciclo normal decide, sem tabela pela metade
            if escopo is not None and r.escopo.get('filiais') != escopo.get('filiais'):
                return None
            escopo = r.escopo
            out.append((k, rot, s))
    return out


def _contagens(user, c, spec, consultas, defs, interrompido):
    from iativa.query.servico import Resultado, _fmt

    from .comparacao import _valores
    agreg = 'contagem:* por filial' if spec["por_filial"] else 'contagem:*'
    celulas = _celulas(user, c, spec, consultas, defs, interrompido, agreg, 'Contando')
    if celulas is None:
        return None
    colunas, valores = [], []
    for k, rot, s in celulas:
        vals, _, _, _ = _valores(s.resultado)
        colunas.append(_coluna(spec, k, rot))
        valores.append(vals)
    grupos = sorted({g for v in valores for g in v if g != 'Total'} or {'Total'})
    linhas = [[g, *[_fmt(v.get(g) or 0, 'inteiro') for v in valores]] for g in grupos]
    res = Resultado(True, 'Contagens feitas no banco com as consultas aprovadas de cada evento, pelos mesmos '
                          'critérios em cada período.',
                    colunas=['Filial' if spec["por_filial"] else 'Indicador', *colunas],
                    tipos=['texto', *['inteiro'] * len(colunas)], linhas=linhas,
                    escopo=celulas[-1][2].resultado.escopo, medida_calculada='contagem')
    res.agregado = True
    res.tabela_pedida = True
    if spec["por_filial"] and len(grupos) > 1:
        res.rodape = ['Total', *[_fmt(sum((x or 0) for g, x in v.items() if g != 'Total'), 'inteiro')
                                 for v in valores]]
    res.premissas = [f'{p.capitalize()}: consulta “{cm.nome}”, data {cm.spec["campo_data"]}.'
                     for p, cm in zip(spec["eventos"], consultas)]
    return res, celulas[-1][2]


def _quer(rotulo: str, campos: list) -> bool:
    r = _norm(rotulo)
    return any(_norm(w)[:4] in r for c in campos for w in str(c).split() if len(w) >= 3)


def _lista(user, c, spec, consultas, defs, interrompido):
    """Uma lista só com quem atende cada evento em cada período. Colunas: as pedidas (campos) ou as comuns às
    consultas; Evento/Período entram quando há mais de um e a pessoa não limitou as colunas."""
    from iativa.query.servico import Resultado
    celulas = _celulas(user, c, spec, consultas, defs, interrompido, '', 'Listando')
    if celulas is None:
        return None
    campos = (spec.get("lista") or {}).get("campos") or []
    primeiro = celulas[0][2].resultado
    base = [col for col in primeiro.colunas if all(col in s.resultado.colunas for _, _, s in celulas)]
    if campos:
        base = [col for col in base if _quer(col, campos)] or base
    extra = [] if campos else ((['Evento'] if len(spec["eventos"]) > 1 else [])
                               + (['Período'] if len(spec["periodos"]) > 1 else []))
    linhas, cortada = [], False
    for k, rot, s in celulas:
        r = s.resultado
        cortada = cortada or bool(r.truncado)
        idx = [r.colunas.index(col) for col in base]
        for ln in r.linhas:
            linhas.append(([spec["rotulos"][k]] if 'Evento' in extra else []) + ([rot] if 'Período' in extra else [])
                          + [ln[i] for i in idx])
    if campos and len(base) == 1:
        linhas = [list(x) for x in dict.fromkeys(tuple(ln) for ln in linhas)]  # "apenas os nomes": sem repetir
    tipos = ['texto'] * len(extra) + [primeiro.tipos[primeiro.colunas.index(col)] for col in base]
    res = Resultado(True, f'{len(linhas)} registro(s) das consultas aprovadas de cada evento.',
                    colunas=extra + base, tipos=tipos, linhas=linhas, escopo=celulas[-1][2].resultado.escopo)
    res.tabela_pedida = True
    if cortada:
        res.premissas = ['Alguma consulta trouxe mais registros que o limite de exibição; a lista mostra os primeiros.']
    return res, celulas[-1][2]


def com_tabela(ac):
    """Especificação da tabela do contrato (dict), True para contrato antigo sem especificação, ou None."""
    return next((e['tabela'] for e in ac.contrato or [] if e.get('tabela')), None)

