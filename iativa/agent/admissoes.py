"""Entrega de admissões: contagem e cadastro detalhado, pelas ferramentas já existentes."""
import re
from dataclasses import replace

from .periodo import _norm, periodo_da_mensagem


def solicitado(texto):
    t = _norm(texto)
    return bool(re.search(r'\b(quantos|quantas|quantidade|contagem)\b', t)
                and (re.search(r'\b(admitid\w*|admissao|admissoes)\b', t)
                     or (re.search(r'\bcontratad\w*\b', t)
                         and re.search(r'\b(funcionari\w*|colaborador\w*|pessoas)\b', t))))


def so_numero(texto):
    return bool(re.search(r'\b(so|somente|apenas)\s+(?:o |a )?(numero|quantidade|total)\b', _norm(texto)))


def contextualizar(user, conversa, texto):
    from django.utils import timezone
    from .periodo import MESES
    palavras = set(re.findall(r'[a-z]+', _norm(texto)))
    if not palavras <= set(MESES) | {'e','no','na','em','desde','de','do','mes','ano','passado','ultimo','este','esse','neste','nesse','a','partir'}:
        return texto
    if solicitado(texto) or len(texto.split()) > 8 or not periodo_da_mensagem(texto, timezone.localdate()):
        return texto
    if not re.match(r'^(e\b|no\b|em\b|desde\b|nesse\b|neste\b)', _norm(texto)):
        return texto
    from .models import ConsultaModelo
    ultima = conversa.solicitacoes.filter(usuario=user).order_by('-pk').first()
    cm = ConsultaModelo.objects.filter(codigo=ultima.objetivo, tabela='SRA').first() if ultima else None
    if cm and cm.spec.get('campo_data') == 'RA_ADMISSA':
        anteriores = conversa.mensagens.filter(papel='usuario').order_by('-pk')[:8]
        if any(solicitado(m.texto) for m in anteriores):
            return 'Quantos funcionários foram admitidos? Mostre nomes completos e datas de admissão. ' + texto
    return texto


def planejar(texto, analise):
    if not solicitado(texto):
        return analise
    entregas = [dict(e) for e in analise.get('entregas', [])]
    contagem = [e for e in entregas if e['tipo'] == 'calculo' and (
        len(entregas) == 1 or re.search(r'admiss|admit|contrat', _norm(e['descricao'])))]
    for e in contagem:
        if e.get('metrica', '') in ('', 'soma', 'contagem'):
            e['metrica'] = 'contagem'
            e['contagem_admissoes'] = True
    if not any(e.get('contagem_admissoes') for e in entregas):
        entregas.append({'descricao': 'Contar as admissões do período', 'tipo': 'calculo',
                         'metrica': 'contagem', 'contagem_admissoes': True})
    if not so_numero(texto):
        detalhes = next((e for e in entregas if e['tipo'] == 'consulta' and re.search(
            r'admiss|admit|funcion|nome', _norm(e['descricao']))), None)
        if detalhes is None:
            detalhes = {'descricao': 'Mostrar nomes completos e datas de admissão', 'tipo': 'consulta', 'metrica': ''}
            entregas.append(detalhes)
        detalhes['detalhe_admissoes'] = True
    return {**analise, 'entregas': entregas}


def orientar(user, texto, decisao, objetivos):
    if not solicitado(texto) or decisao.acao not in ('preparar_consulta', 'criar_consulta'):
        return decisao
    from .models import ConsultaModelo
    from django.utils import timezone
    pares = tuple((k, v) for k, v in decisao.parametros if k != 'agregacao')
    periodo = periodo_da_mensagem(texto, timezone.localdate())
    if periodo:
        pares = tuple((k, v) for k, v in pares if k != 'periodo-analise') + (('periodo-analise', periodo),)
    if decisao.acao == 'criar_consulta' and decisao.consulta and decisao.consulta['tabela'] == 'SRA':
        cols = list(dict.fromkeys(['RA_NOME', 'RA_ADMISSA'] + decisao.consulta['colunas']))[:20]
        return replace(decisao, parametros=pares, consulta={**decisao.consulta, 'colunas': cols, 'campo_data': 'RA_ADMISSA'})
    permitidas = {o.codigo for o in objetivos if o.modelo_id}
    escolhido = next((o for o in objetivos if o.codigo == decisao.objetivo), None)
    if escolhido and escolhido.tabelas[0] != 'SRA':
        return decisao  # Outra entrega de um pedido misto, por exemplo contas a pagar.
    if re.search(r'demitid|demiss|desligad', _norm(texto)):
        return decisao  # Não trocar a consulta da outra parte de um pedido misto.
    modelos = ConsultaModelo.objects.filter(codigo__in=permitidas, tabela='SRA', situacao='aprovada').order_by('pk')
    adequados = [m for m in modelos if m.spec.get('campo_data') == 'RA_ADMISSA'
                 and 'RA_ADMISSA' in {c['campo'] for c in m.spec.get('colunas', [])} and {'RA_NOME','RA_NOMECMP'} & {c['campo'] for c in m.spec.get('colunas', [])}]
    cm = next((m for m in adequados if m.codigo == decisao.objetivo), adequados[0] if adequados else None)
    return replace(decisao, objetivo=cm.codigo, parametros=pares) if cm else decisao


def tem_detalhes(res):
    if not getattr(res, 'ok', False) or getattr(res, 'consolidado', False):
        return False
    campos = res.escopo.get('campos', {}).get('SRA', [])
    if res.solicitacao is not None:
        from .models import ConsultaModelo
        cm = ConsultaModelo.objects.filter(codigo=res.solicitacao.objetivo).first()
        if cm is None or cm.spec.get('campo_data') != 'RA_ADMISSA':
            return False
    return ('RA_ADMISSA' in campos and bool({'RA_NOME','RA_NOMECMP'} & set(campos))
            and any('nome' in _norm(c) for c in res.colunas)
            and any('admi' in _norm(c) and t == 'data' for c, t in zip(res.colunas, res.tipos)))


def completar(texto, saida):
    if not solicitado(texto) or not tem_detalhes(saida.resultado):
        return False
    from .calculos import contar
    n = contar(saida.resultado)
    if n is None:
        return False
    r = saida.resultado
    r.medida_calculada, r.curta_salva = 'contagem', str(n)
    unidade = 'admissão' if n == 1 else 'admissões'
    r.mensagem = (f'{n} {unidade} no período consultado. '
                  'Nomes completos e datas de admissão na tabela abaixo.')
    if r.truncado:
        r.mensagem += f' A tabela mostra os primeiros {len(r.linhas)} registros; o total considera todos.'
    r.premissas = list(r.premissas) + ['Contagem de vínculos admitidos; matrículas distintas não comprovam pessoas distintas.']
    saida.texto = ''
    return True
