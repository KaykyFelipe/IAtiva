"""Comparação de dois períodos pela mesma consulta aprovada, com os mesmos critérios (M66).

Geral: qualquer consulta aprendida com campo de data e uma medida (soma, contagem, média...), não só
faturamento. O agrupamento é o que a pessoa pediu ("por filial", "por cliente", total) e a mesma
definição roda nos dois períodos. Estados distintos, nunca trocados por zero:
- período sem registros (zero de verdade para soma/contagem; sem valor para média);
- grupo ausente em um dos períodos ("sem registro no período");
- base zero (variação indefinida);
- período em andamento (marcado como parcial);
- fonte que não entregou valor (não calculável).
"""
import calendar
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .periodo import MESES, _norm

_COMPARA = re.compile(r'\b(compar\w*|variac\w*|versus|vs\.?|em relacao a|frente a|contra)\b')


def _mes(a, m):
    return (date(a, m, 1), date(a, m, calendar.monthrange(a, m)[1]), f'{m:02d}/{a}')


def _oracao(texto):
    """M67: só a oração da comparação; "... e liste os admitidos em agosto" é outra entrega."""
    t = _norm(texto)
    m = _COMPARA.search(t)
    if not m:
        return ''
    return re.split(r'\s+e\s+(?=(?:me\s+)?(?:liste|listar|mostre|mostrar|quais|quem|quantos|quantas|traga|diga)\b)'
                    r'|[.;?]', t[m.start():])[0]


_NAO_ASSUNTO = re.compile(r'^(compar\w*|variac\w*|versus|filia\w*|cada|entre|com|mes|ano|anos|meses|passado|'
                          r'anterior|este|esse|neste|nesse|diferenc\w*|percent\w*|relacao|frente|contra|'
                          + '|'.join(MESES) + r'|\d+)$')


def assunto(texto):
    """Palavras do que é comparado ("faturamento"), sem períodos, agrupamento ou o verbo."""
    return {w for w in re.findall(r'[a-z]{5,}', _oracao(texto)) if not _NAO_ASSUNTO.match(w)}


def periodos(texto):
    """Dois períodos comparados: [(inicio, fim, rotulo), (inicio, fim, rotulo)] ou []."""
    t = _oracao(texto)
    if not t:
        return []
    hoje = timezone.localdate()
    meses = list(re.finditer(r'\b(' + '|'.join(MESES) + r')\b(?:\s+de)?(?:\s+(20\d\d))?', t))
    if len(meses) == 2:
        ano_padrao = next((int(m.group(2)) for m in meses if m.group(2)), hoje.year)
        return [_mes(int(m.group(2) or ano_padrao), MESES[m.group(1)]) for m in meses]
    if re.search(r'\b(este|esse|neste|nesse) mes\b', t) and re.search(r'\bmes passado\b|\bmes anterior\b', t):
        a, m = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
        return [_mes(a, m), _mes(hoje.year, hoje.month)]
    anos = re.findall(r'\b(20\d\d)\b', t)
    if len(anos) == 2 and not meses:
        return [(date(int(x), 1, 1), date(int(x), 12, 31), x) for x in anos]
    if re.search(r'\b(este|esse) ano\b', t) and re.search(r'\bano passado\b|\bano anterior\b', t):
        return [(date(hoje.year - 1, 1, 1), date(hoje.year - 1, 12, 31), str(hoje.year - 1)),
                (date(hoje.year, 1, 1), date(hoje.year, 12, 31), str(hoje.year))]
    return []


def planejar(texto, analise):
    if not periodos(texto):
        return analise
    # Troca só a entrega de cálculo/comparação; as outras perguntas da mensagem continuam (M63).
    # M68: "Faturamento de julho por filial" e "... de agosto" são partes da própria comparação, não entregas
    # a mais (a pessoa não pediu cada período separado). Listas com campos pedidos ficam.
    alvo = assunto(texto)
    meses = {_norm(r) for _, _, r in periodos(texto)} | {m for m in MESES if re.search(rf'\b{m}\b', _oracao(texto))}

    def parte(e):
        d = _norm(e.get('descricao', ''))
        if e.get('campos_pedidos'):
            return False
        if e.get('tipo') == 'calculo' and re.search(r'compar|variac|diferen', d):
            return True
        return bool(alvo & set(re.findall(r'[a-z]{5,}', d))) and any(re.search(rf'\b{re.escape(m)}\b', d) for m in meses)

    outras = [dict(e) for e in analise.get('entregas', []) if not parte(e)]
    return {**analise, 'entregas': [{'descricao': 'Comparar os dois períodos pela mesma definição, com diferença e '
                                                  'variação percentual', 'tipo': 'calculo', 'metrica': 'comparacao',
                                     'comparacao': True}] + outras}


_MEDIDA = re.compile(r'^(soma|media|contagem|maximo|minimo)\s*:', re.I)


def _agregacao(d, texto):
    """A medida e o agrupamento que o modelo escolheu; "por filial" só se pedido ou já escolhido."""
    agreg = next((v for k, v in d.parametros if k == 'agregacao'), '')
    if not _MEDIDA.match(agreg or ''):
        return None
    agreg = re.sub(r'\s+top\s+\d+', '', agreg)  # ranking não se compara: a lista muda de um período para o outro
    # Mês/ano já são os próprios períodos comparados; fica no máximo um agrupamento (o primeiro pedido).
    medida, *grupos = re.split(r'\s+por\s+', agreg.strip(), flags=re.I)
    grupos = [g for g in grupos if g.strip().lower() not in ('mes', 'mês', 'ano')]
    agreg = medida + (f' por {grupos[0].strip()}' if grupos else '')
    if ' por ' not in agreg and re.search(r'\bpor filia(l|is)\b|\bcada filial\b', _norm(texto)):
        agreg += ' por filial'
    return agreg


def _valores(r):
    """{grupo: valor} da primeira medida. Grupo = colunas de texto/data; medida = primeira coluna numérica."""
    numericas = [i for i, t in enumerate(r.tipos) if t in ('dinheiro', 'valor', 'inteiro', 'numero')]
    if not numericas:
        raise ValidationError('A fonte não entregou uma medida numérica para comparar.')
    i = numericas[0]
    chaves = [k for k, t in enumerate(r.tipos) if k not in numericas]
    from .calculos import _numero
    filiais = (r.escopo or {}).get('filiais', [])
    unico = 'Total'
    if not chaves and len(filiais) == 1:
        from iativa.accounts.filiais import rotulo
        unico = rotulo(filiais[0])  # uma filial só: a linha é dela
    out = {}
    for linha in r.linhas:
        grupo = ' · '.join(str(linha[k]) for k in chaves) or unico
        bruto = str(linha[i]).strip()
        out[grupo] = _numero(bruto) if bruto else None
    titulo = (r.colunas[chaves[0]] if len(chaves) == 1 else 'Grupo' if chaves
              else 'Filial' if len(filiais) == 1 else 'Período')
    return out, r.colunas[i], r.tipos[i], titulo


def deste_passo(ac, d):
    """M68: a comparação só roda no passo que é dela. Antes, a consulta de admissões (outra entrega do mesmo
    pedido) também virava "comparação julho x agosto", com números que ninguém pediu."""
    alvo = {i for i, e in enumerate(ac.contrato or []) if e.get('comparacao')}
    if not ac.contrato:
        return True
    if not alvo or alvo <= set(ac.cumpridas):
        return False
    if d.atende:
        return bool(set(d.atende) & alvo)
    # Sem "atende": consulta que tem os campos de uma lista ainda pendente é o passo dessa lista.
    from .entregas import faltam
    from .models import ConsultaModelo
    cm = ConsultaModelo.objects.filter(codigo=d.objetivo, situacao='aprovada').first()
    colunas = cm.spec.get('colunas', []) if cm is not None else []
    return not any(e.get('campos_pedidos') and not faltam(colunas, e['campos_pedidos'])
                   for i, e in enumerate(ac.contrato) if i not in ac.cumpridas)


def executar(user, c, d, defs, objetivos, texto, interrompido=None):
    from .agente import Interrompido, Saida, _preparar
    from .contratos import OBJETIVOS
    from iativa.query.servico import Resultado, _fmt

    datas = periodos(texto)
    obj = OBJETIVOS[d.objetivo] if d.objetivo in OBJETIVOS else None
    if len(datas) != 2 or obj is None or not obj.modelo_id or d.objetivo not in {o.codigo for o in objetivos}:
        return None
    from .models import ConsultaModelo
    cm = ConsultaModelo.objects.filter(codigo=d.objetivo, situacao='aprovada').first()
    agreg = _agregacao(d, texto)
    if agreg is None and cm is not None:
        # Sem medida escolhida: só quando a consulta tem UMA medida numérica não há o que adivinhar.
        medidas = [c['campo'] for c in cm.spec.get('colunas', []) if c.get('tipo') in ('numero', 'dinheiro', 'valor')]
        if len(medidas) == 1:
            agreg = _agregacao(replace(d, parametros=d.parametros + (('agregacao', f'soma:{medidas[0]}'),)), texto)
    if cm is None or not cm.spec.get('campo_data') or agreg is None:
        return None  # sem período ou sem medida: o ciclo normal decide (não inventa uma definição)
    saidas = []
    from . import andamento
    for inicio, fim, rot in datas:
        if interrompido is not None and interrompido():
            raise Interrompido
        andamento.avisar(f'Calculando {rot} em “{cm.nome}” ({cm.tabela})')
        pares = tuple((k, v) for k, v in d.parametros if k not in ('periodo-analise', 'agregacao'))
        dd = replace(d, acao='preparar_consulta', resposta='',
                     parametros=pares + (('periodo-analise', f'{inicio:%d/%m/%Y}..{fim:%d/%m/%Y}'), ('agregacao', agreg)))
        s = _preparar(user, c, dd, None, defs, {}, texto='')
        if s.resultado is None or not s.resultado.ok:
            return s  # falha parcial: não compara um período com o vazio do outro
        if s.resultado.truncado:
            raise ValidationError('Um dos períodos veio cortado; não comparei totais incompletos.')
        saidas.append(s)
    a, b = [s.resultado for s in saidas]
    if a.escopo != b.escopo:
        raise ValidationError('As permissões ou fontes mudaram durante a comparação; não comparei escopos diferentes.')
    if len({m for r in (a, b) if r.totais for m in r.totais.moedas}) > 1:
        raise ValidationError('Não comparei moedas diferentes.')
    va, rotulo, tipo, titulo = _valores(a)
    vb, _, _, _ = _valores(b)
    media = agreg.lower().startswith('media')
    vazio = [r.totais is not None and r.totais.registros == 0 for r in (a, b)]

    def valor(v, sem_registros):
        if v is not None:
            return v
        return Decimal(0) if (sem_registros and not media) else None

    hoje = timezone.localdate()
    nomes = [rot + (' (parcial, até hoje)' if ini <= hoje < fim else '') for ini, fim, rot in datas]
    linhas = []
    for grupo in sorted(set(va) | set(vb)):
        x = valor(va.get(grupo), vazio[0] or grupo not in va)
        y = valor(vb.get(grupo), vazio[1] or grupo not in vb)
        cx = _fmt(x, tipo) if x is not None else 'Sem valor'
        cy = _fmt(y, tipo) if y is not None else 'Sem valor'
        if x is None or y is None:
            linhas.append([grupo, cx, cy, 'Não calculável', 'Não calculável'])
            continue
        pct = _fmt((y - x) / x * 100, 'valor') + '%' if x else 'Indefinido (base zero)'
        linhas.append([grupo, cx, cy, _fmt(y - x, tipo), pct])
    premissas = list(dict.fromkeys(a.premissas + b.premissas)) + [
        f'Mesma consulta ({cm.nome}) e mesmos critérios nos dois períodos; medida: {rotulo}.',
        'Grupo sem registro em um período conta como zero em soma/contagem e fica sem valor em média.']
    if any('parcial' in n for n in nomes):
        premissas.append('Há período em andamento: a comparação com um período fechado não é equivalente.')
    r = Resultado(True, 'Comparação dos períodos pela mesma consulta e pelos mesmos critérios. '
                        'Diferença = segundo período menos o primeiro.',
                  colunas=[titulo, nomes[0], nomes[1], 'Diferença', 'Variação percentual'],
                  tipos=['texto', tipo, tipo, tipo, 'texto'], linhas=linhas, escopo=a.escopo,
                  medida_calculada='comparacao', premissas=premissas)
    return Saida('', {'tipo': 'calculo_resultado'}, solicitacao=saidas[-1].solicitacao, resultado=r)
