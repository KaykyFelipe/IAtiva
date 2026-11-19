"""Contagem com detalhe (M75, etapa 2): regra geral no lugar do atalho de admissões.

"Quantos X ...?" sobre uma consulta aprovada entrega o número E os registros contados (identificação e a data
do evento), salvo "só o número" ou contagem agrupada ("por filial"). Vale para qualquer consulta aprovada,
sem tabela ou frase fixa no código:
- o evento contado (admissão, demissão, emissão, vencimento...) escolhe a consulta pelo campo de data dela:
  o rótulo do `campo_data` tem a mesma raiz que uma palavra do pedido ("admitidos" ~ "Data de admissão");
- o detalhe só conta como entregue com uma coluna de identificação (texto) e a coluna de data do evento;
- o período da mensagem vale para a consulta (o modelo não troca "esse mês" por outro).
Origem: preferência de Diego ("quando perguntarem quantos admitidos, mostre nome completo e data"), agora
aplicada a toda contagem de registros.
"""
import re
from dataclasses import replace

from .periodo import MESES, _norm, periodo_da_mensagem

_CONTA = re.compile(r'\b(quantos|quantas|quantidade|contagem|numero de)\b')
_SO_NUMERO = re.compile(r'\b(so|somente|apenas)\s+(?:o |a )?(numero|quantidade|total)\b')
# "Quantos por filial?" é um agregado por grupo, não uma lista de registros.
_AGRUPADO = re.compile(r'\bpor\s+(filia\w*|mes|ano|cliente|fornecedor|setor|tipo|cargo|funcao|natureza|cfop|status|situacao|estado|categoria)\b'
                       r'|\bcada\s+(filial|mes|cliente)\b')
_VAZIAS = {'quantos', 'quantas', 'quantidade', 'contagem', 'numero', 'foram', 'tiveram', 'temos', 'tivemos',
           'houve', 'esse', 'este', 'nesse', 'neste', 'passado', 'ultimo', 'mostre', 'liste', 'quais', 'filial',
           'filiais', 'periodo', 'desde', 'entre', 'total', 'agora', 'hoje', 'ainda', 'tambem', 'completo', 'nomes',
           'datas', 'data'} | set(MESES)


_PERIODO = (r'\b(?:n?es[st]e|n?ess[ea])\s+(?:m[eê]s|ano)\b|\b(?:m[eê]s|ano)\s+(?:passado|anterior)\b|\b[uú]ltimo\s+m[eê]s\b'
            r'|\b(?:em|de|no m[eê]s de|desde|a partir de)\s+(?:' + '|'.join(MESES) + r'|mar[cç]o)(?:\s+(?:de\s+)?\d{4})?\b'
            r'|\b(?:de|desde)\s+\d{2}/\d{2}/\d{4}(?:\s+(?:a|at[eé])\s+\d{2}/\d{2}/\d{4})?')


def so_numero(texto):
    return bool(_SO_NUMERO.search(_norm(texto)))


_TEMPO = re.compile(r'\b(quant[oa]s|numero de)\s+(dias?|meses|anos?|horas?|semanas?|minutos?)\b')
_PEDE_DETALHE = re.compile(r'\b(liste|listar|mostre|mostrar|quais|quem|nomes?|detalh\w*|cada um)\b')


def solicitado(texto):
    """Contagem de registros (não de tempo: "quantos dias úteis" é cálculo de datas), sem agrupamento."""
    t = _norm(texto)
    return bool(_CONTA.search(t)) and not _AGRUPADO.search(t) and not _TEMPO.search(t)


def _raizes(texto):
    return {w[:4] for w in re.findall(r'[a-z]{5,}', _norm(texto)) if w not in _VAZIAS}


def assunto_acompanhado(user, texto) -> bool:
    """Contagem de um assunto que a pessoa quer ver em detalhe: pedido no texto ("quais", "nomes", "liste") ou
    preferência dela guardada na memória sobre o mesmo assunto. Vale também com "só o número" (aí o número sai
    da mesma consulta, sem a lista)."""
    if not solicitado(texto):
        return False
    if _PEDE_DETALHE.search(_norm(texto)):
        return True
    if user is None:
        return False
    from .memoria import notas_relevantes
    raizes = _raizes(texto)
    try:
        notas = notas_relevantes(user, texto)
    except Exception:
        return False
    return any(getattr(n, 'tipo', '') == 'preferencia' and _PEDE_DETALHE.search(_norm(n.texto))
               and raizes & _raizes(n.texto) for n in notas)


def quer_detalhe(user, texto) -> bool:
    """A lista dos registros contados só vem quando foi pedida (texto ou preferência na memória) e não foi
    pedido "só o número". Sem isso, só o número: não entrega o que não foi pedido."""
    return assunto_acompanhado(user, texto) and not so_numero(texto)


def _rotulo_data(cm):
    campo = cm.spec.get('campo_data')
    col = next((c for c in cm.spec.get('colunas', []) if c.get('campo') == campo), None)
    return (col or {}).get('rotulo', '') if campo else ''


def _evento(cm):
    """"Data de admissão" -> "admissão"."""
    return re.sub(r'^\s*(data|dt)\.?\s+(?:(?:de|da|do)\s+)?', '', _rotulo_data(cm), flags=re.I).strip().lower()


def _detalhavel(cm):
    cols = cm.spec.get('colunas', [])
    return bool(cm.spec.get('campo_data')) and any(c.get('campo') == cm.spec['campo_data'] for c in cols) and any(
        c.get('tipo', 'texto') == 'texto' and c.get('campo') != cm.spec['campo_data'] for c in cols)


def consulta_do_evento(texto, modelos):
    """Consulta cujo evento (rótulo do campo de data) tem a raiz de uma palavra do pedido; None se não houver."""
    raizes = _raizes(texto)
    return next((m for m in modelos if _detalhavel(m) and _norm(_evento(m))[:4] in raizes), None)


def planejar(texto, analise, user=None):
    from .objetivos import pede_intervalo
    if pede_intervalo(_norm(texto)):
        return analise
    if not assunto_acompanhado(user, texto):
        return analise  # contagem simples: o ciclo normal (agregação no banco) responde só o número
    entregas = [dict(e) for e in analise.get('entregas', [])]
    contagem = [e for e in entregas if e['tipo'] == 'calculo' and (len(entregas) == 1 or e.get('metrica') == 'contagem'
                                                                     or _CONTA.search(_norm(e['descricao'])))]
    for e in contagem:
        if e.get('metrica', '') in ('', 'soma', 'contagem'):
            e['metrica'] = 'contagem'
            e['contagem_detalhada'] = True
    if not any(e.get('contagem_detalhada') for e in entregas):
        entregas.append({'descricao': 'Contar os registros do período', 'tipo': 'calculo',
                         'metrica': 'contagem', 'contagem_detalhada': True})
    if not so_numero(texto):
        detalhes = next((e for e in entregas if e['tipo'] == 'consulta'), None)
        if detalhes is None:
            detalhes = {'descricao': 'Mostrar os registros contados (identificação e data)', 'tipo': 'consulta',
                        'metrica': ''}
            entregas.append(detalhes)
        detalhes['detalhe_contagem'] = True
    return {**analise, 'entregas': entregas}


def consulta_do_primeiro_evento(texto, modelos):
    """M108: prazo entre dois eventos ("admitidos ... saíram antes de 90 dias"): a base é a consulta do PRIMEIRO
    evento citado no pedido, na ordem do texto."""
    for w in re.findall(r'[a-z]{5,}', _norm(texto)):
        if w in _VAZIAS:
            continue
        do_evento = [m for m in modelos if _detalhavel(m) and _norm(_evento(m))[:4] == w[:4]]
        if do_evento:
            # Filtro fixo (ex.: só ativos) esconde quem já teve o segundo evento (desligados): prefere a sem filtro.
            return min(do_evento, key=lambda m: bool(m.spec.get('filtros_fixos')))
    return None


def recuperar_filtro_do_ano(texto, decisao, objetivos, *, entrega_pendente=False):
    """Recompõe os intervalos do pedido após erro de parâmetro ou contagem anual incompleta."""
    from .contratos import campos_periodo
    from .models import ConsultaModelo, SituacaoModelo
    from .objetivos import ano_admissoes_e_desligamentos

    ano = ano_admissoes_e_desligamentos(texto)
    if not ano or decisao.acao != 'preparar_consulta':
        return None
    params = dict(decisao.parametros)
    campos = ('RA_ADMISSA', 'RA_DEMISSA')
    partes = [parte.strip().upper() for parte in params.get('filtro-campo', '').split(';') if parte.strip()]
    formato = r'\s*[<>]=?\s*\d{8}'
    if entrega_pendente:
        if (params.get('agregacao', '').lower() != 'contagem:* por filial'
                or params.get('filial-analise') != 'TODAS'):
            return None
        formato = rf'(?:{formato}|@\d{{8}}\.\.\d{{8}})'
    if (not partes and not entrega_pendente) or not all(
            re.fullmatch(rf'(?:RA_ADMISSA|RA_DEMISSA){formato}', p) for p in partes):
        return None
    objetivo = next((o for o in objetivos if o.codigo == decisao.objetivo and o.tabelas == ('SRA',)), None)
    if objetivo is None or not objetivo.modelo_id:
        return None
    modelo = ConsultaModelo.objects.filter(pk=objetivo.modelo_id, situacao=SituacaoModelo.APROVADA).first()
    if modelo is None or not set(campos) <= set(campos_periodo(modelo.spec)):
        return None
    intervalos = '; '.join(f'{campo}@{ano}0101..{ano}1231' for campo in campos)
    params['filtro-campo'] = intervalos
    if entrega_pendente and 'periodo-analise' in objetivo.obrigatorios:
        if modelo.spec.get('campo_data') not in campos:
            return None
        params['periodo-analise'] = f'01/01/{ano}..31/12/{ano}'
    return replace(decisao, parametros=tuple(params.items()))


def orientar(user, texto, decisao, objetivos):
    """Mantém a consulta do evento pedido, sem agregação, com o período da mensagem."""
    if decisao.acao not in ('preparar_consulta', 'criar_consulta'):
        return decisao
    from .objetivos import _norm as _n, pede_intervalo
    intervalo = pede_intervalo(_n(texto))
    if not intervalo and not assunto_acompanhado(user, texto):
        return decisao
    from django.utils import timezone

    from .models import ConsultaModelo
    pares = tuple((k, v) for k, v in decisao.parametros if k != 'agregacao')
    periodo = periodo_da_mensagem(texto, timezone.localdate())
    if periodo:
        pares = tuple((k, v) for k, v in pares if k != 'periodo-analise') + (('periodo-analise', periodo),)
    if decisao.acao == 'criar_consulta':
        return replace(decisao, parametros=pares)
    escolhido = next((o for o in objetivos if o.codigo == decisao.objetivo), None)
    if escolhido is None or not escolhido.modelo_id:
        return decisao  # consulta fixa (não aprendida): o ciclo normal decide
    tabela = escolhido.tabelas[0]
    permitidas = {o.codigo for o in objetivos if o.modelo_id}
    modelos = list(ConsultaModelo.objects.filter(codigo__in=permitidas, tabela=tabela, situacao='aprovada').order_by('pk'))
    atual = next((m for m in modelos if m.codigo == decisao.objetivo), None)
    alvo = (consulta_do_primeiro_evento(texto, modelos) if intervalo else None) or         consulta_do_evento(texto, ([atual] if atual else []) + modelos)
    if alvo is None:
        # O pedido não fala do evento de nenhuma consulta desta tabela (ex.: outra parte de um pedido misto).
        return replace(decisao, parametros=pares) if atual is not None and _detalhavel(atual) else decisao
    return replace(decisao, objetivo=alvo.codigo, parametros=pares)


def _consulta(res):
    s = getattr(res, 'solicitacao', None)
    if s is None:
        return None
    from .models import ConsultaModelo
    return ConsultaModelo.objects.filter(codigo=s.objetivo).first()


def tem_detalhes(res):
    """Detalhe entregue: a lista tem identificação (texto) e a data do evento da consulta."""
    if not getattr(res, 'ok', False) or getattr(res, 'consolidado', False) or getattr(res, 'linhas', None) is None:
        return False
    cm = _consulta(res)
    if res.solicitacao is not None and (cm is None or not _detalhavel(cm)):
        return False
    tipos = list(zip(res.colunas, res.tipos))
    evento = _norm(_evento(cm))[:4] if cm is not None else ''
    data = any(t == 'data' and (not evento or evento in _norm(c)) for c, t in tipos)
    return data and any(t == 'texto' for _, t in tipos)


def completar(texto, saida, user=None):
    from .objetivos import pede_intervalo
    if pede_intervalo(_norm(texto)):
        return False  # a população da consulta ainda precisa passar pelo filtro do prazo
    if not assunto_acompanhado(user, texto) or not tem_detalhes(saida.resultado):
        return False
    from .calculos import contar
    n = contar(saida.resultado)
    if n is None:
        return False
    r = saida.resultado
    cm = _consulta(r)
    raiz = _norm(_evento(cm))[:4] if cm is not None else ''
    # Nome do evento com as palavras da pessoa ("admitidos"), não o rótulo abreviado do dicionário.
    palavra = next((w for w in re.findall(r'[a-zà-ú]{5,}', texto.lower()) if raiz and _norm(w).startswith(raiz)), '')
    r.medida_calculada, r.curta_salva = 'contagem', str(n)
    r.mensagem = (f'Total: {n}' + (f' ({palavra})' if palavra else '') +
                  ' no período consultado. Detalhes na tabela abaixo.')
    if r.truncado:
        r.mensagem += f' A tabela mostra os primeiros {len(r.linhas)} registros; o total considera todos.'
    r.premissas = list(r.premissas) + ['Contagem de registros da consulta; registros distintos não comprovam '
                                       'pessoas ou entidades distintas.']
    saida.texto = ''
    return True


def entregas_atendidas(contrato, res):
    """Entregas de contagem/detalhe que ESTE resultado cumpre: com várias contagens no pedido ("admitidos e
    demitidos"), só a do evento da consulta (raiz do campo de data na descrição da entrega)."""
    cm = _consulta(res)
    raiz = _norm(_evento(cm))[:4] if cm is not None else ''
    marcadas = [i for i, e in enumerate(contrato) if e.get('contagem_detalhada') or e.get('detalhe_contagem')]
    if len([i for i in marcadas if contrato[i].get('contagem_detalhada')]) <= 1 or not raiz:
        return marcadas
    return [i for i in marcadas if raiz in _raizes(contrato[i].get('descricao', ''))
            or (contrato[i].get('detalhe_contagem') and not contrato[i].get('contagem_detalhada'))]


def _trocar_assunto(conversa, texto):
    """M76: continuação curta ("e demitidos?", "e os desligados?") de uma contagem: refaz a pergunta anterior
    trocando a palavra do mesmo tipo (mesma terminação: admiti-DOS -> desliga-DOS). Geral: vale para qualquer
    assunto contado ("e as canceladas?" depois de "quantas notas foram emitidas?")."""
    t = _norm(texto).strip(' ?!.')
    m = re.fullmatch(r'e\s+(?:(?:os|as|o|a|quant[oa]s)\s+)?([a-z]{5,})(?:\s+\w+)?', t)
    if not m or solicitado(texto):
        return ''
    nova = m.group(1)
    anterior = next((x.texto for x in conversa.mensagens.filter(papel='usuario').order_by('-pk')[:8]
                     if solicitado(x.texto)), None)
    if anterior is None:
        return ''
    palavras = re.findall(r'\w+', anterior)
    alvo = next((w for w in palavras if len(w) >= 5 and _norm(w) != nova and _norm(w)[-3:] == nova[-3:]
                 and not _CONTA.search(_norm(w))), None)
    if alvo is None:
        return ''
    original = next(w for w in re.findall(r'\w+', texto) if _norm(w) == nova)
    return re.sub(rf'\b{re.escape(alvo)}\b', original, anterior, count=1)


def contextualizar(user, conversa, texto):
    """"E no mês passado?" depois de uma contagem: a mesma contagem, no novo período.
    "E demitidos?" depois de "Quantos funcionários foram admitidos em agosto?": a mesma pergunta, com o
    assunto trocado (mesmo período, mesmos detalhes)."""
    from django.utils import timezone
    trocada = _trocar_assunto(conversa, texto)
    if trocada:
        return trocada
    palavras = set(re.findall(r'[a-z]+', _norm(texto)))
    if not palavras <= set(MESES) | {'e', 'no', 'na', 'em', 'desde', 'de', 'do', 'mes', 'ano', 'passado', 'ultimo',
                                     'este', 'esse', 'neste', 'nesse', 'a', 'partir'}:
        return texto
    if solicitado(texto) or len(texto.split()) > 8 or not periodo_da_mensagem(texto, timezone.localdate()):
        return texto
    if not re.match(r'^(e\b|no\b|em\b|desde\b|nesse\b|neste\b)', _norm(texto)):
        return texto
    from .models import ConsultaModelo
    ultima = conversa.solicitacoes.filter(usuario=user).order_by('-pk').first()
    cm = ConsultaModelo.objects.filter(codigo=ultima.objetivo).first() if ultima else None
    if cm is None or not _detalhavel(cm):
        return texto
    anterior = next((m.texto for m in conversa.mensagens.filter(papel='usuario').order_by('-pk')[:8]
                     if solicitado(m.texto)), None)
    if anterior is None:
        return texto
    # A pergunta anterior diz o que contar; o período dela sai, para valer só o novo.
    sem_periodo = re.sub(_PERIODO, ' ', anterior, flags=re.I)
    return f'{" ".join(sem_periodo.split())} {texto.strip()}'
