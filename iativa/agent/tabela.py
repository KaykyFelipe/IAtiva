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
        if not _CONTA.search(t) or eventos_intersecao(texto):
            return None
        ev, per = eventos(texto), _periodos(texto)
        if len(per) < 2:
            return None  # reserva sem o modelo: só a grade evento x período (status listados não são eventos)
        spec = {"eventos": ev, "rotulos": [e.capitalize() for e in ev],
                "periodos": [(i.isoformat(), f.isoformat(), r) for i, f, r in per], "por_filial": bool(_POR.search(t))}
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
    return {"eventos": [" ".join(str(m['evento']).split())[:60] for m in medidas],
            "rotulos": [" ".join(str(m.get('rotulo') or m['evento']).split())[:40] for m in medidas],
            "periodos": periodos, "por_filial": t.get('agrupar_por') == 'filial'}


def _coluna(spec, k, rot):
    return rot if len(spec["eventos"]) == 1 else f'{spec["rotulos"][k]} - {rot}'


def planejar(texto: str, analise: dict) -> dict:
    """O contrato vira UMA entrega: a tabela com todos os eventos e períodos pedidos (a especificação vai junto)."""
    spec = pedido(texto, analise)
    if spec is None:
        return analise
    cols = ', '.join(_coluna(spec, k, r) for k in range(len(spec["eventos"])) for _, _, r in spec["periodos"])
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


def executar(user, c, texto, defs, objetivos, interrompido=None, spec=None):
    """Saida com a tabela montada, ou None se algum evento não tiver consulta que a pessoa possa usar."""
    from datetime import date

    from iativa.query.servico import Resultado, _fmt

    from . import andamento
    from .agente import Interrompido, Saida, _preparar
    from .comparacao import _valores
    from .decisao import Decisao
    from .models import ConsultaModelo
    spec = spec if isinstance(spec, dict) else pedido(texto)
    if spec is None:
        return None
    permitidas = {o.codigo for o in objetivos if getattr(o, 'modelo_id', None)}
    modelos = list(ConsultaModelo.objects.filter(codigo__in=permitidas, situacao='aprovada').order_by('pk'))
    consultas = [_consulta_do_evento(e, modelos) for e in spec["eventos"]]
    if any(cm is None for cm in consultas) or len({(cm.tabela, cm.spec['campo_data']) for cm in consultas}) != len(consultas):
        return None
    agreg = 'contagem:* por filial' if spec["por_filial"] else 'contagem:*'
    colunas, valores, ultima, escopo = [], [], None, None
    for k, (palavra, cm) in enumerate(zip(spec["eventos"], consultas)):
        for ini, fim, rot in spec["periodos"]:
            if interrompido is not None and interrompido():
                raise Interrompido
            andamento.avisar(f'Contando {palavra} em {rot}')
            i, f = date.fromisoformat(ini), date.fromisoformat(fim)
            d = Decisao('', 'preparar_consulta', cm.codigo, (('filial-analise', 'TODAS'),
                        ('periodo-analise', f'{i:%d/%m/%Y}..{f:%d/%m/%Y}'), ('agregacao', agreg)), '', '', False, ())
            s = _preparar(user, c, d, None, defs, {}, texto='')
            r = s.resultado
            if r is None or not r.ok or r.truncado:
                return None  # uma célula falhou: o ciclo normal decide, sem tabela pela metade
            if escopo is not None and r.escopo.get('filiais') != escopo.get('filiais'):
                return None
            escopo, ultima = r.escopo, s
            vals, _, _, _ = _valores(r)
            colunas.append(_coluna(spec, k, rot))
            valores.append(vals)
    grupos = sorted({g for v in valores for g in v if g != 'Total'} or {'Total'})
    linhas = [[g, *[_fmt(v.get(g) or 0, 'inteiro') for v in valores]] for g in grupos]
    res = Resultado(True, 'Contagens feitas no banco com as consultas aprovadas de cada evento, pelos mesmos '
                          'critérios em cada período.',
                    colunas=['Filial' if spec["por_filial"] else 'Indicador', *colunas],
                    tipos=['texto', *['inteiro'] * len(colunas)], linhas=linhas, escopo=escopo,
                    medida_calculada='contagem')
    res.agregado = True
    res.tabela_pedida = True  # só esta tabela cumpre a entrega (planejamento.conferir)
    if spec["por_filial"] and len(grupos) > 1:
        res.rodape = ['Total', *[_fmt(sum((x or 0) for g, x in v.items() if g != 'Total'), 'inteiro')
                                 for v in valores]]
    res.premissas = [f'{p.capitalize()}: consulta “{cm.nome}”, data {cm.spec["campo_data"]}.'
                     for p, cm in zip(spec["eventos"], consultas)]
    return Saida('', {'tipo': 'calculo_resultado'}, solicitacao=ultima.solicitacao, resultado=res)


def com_tabela(ac):
    """Especificação da tabela do contrato (dict), True para contrato antigo sem especificação, ou None."""
    return next((e['tabela'] for e in ac.contrato or [] if e.get('tabela')), None)

