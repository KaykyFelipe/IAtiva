"""Compara dois meses pela mesma consulta aprovada e pelos mesmos filtros."""
import calendar
import re
from dataclasses import replace
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from .periodo import _norm, MESES


def periodos(texto):
    t=_norm(texto)
    if not re.search(r'compar|variacao',t) or not re.search(r'fatur',t): return []
    encontrados=list(re.finditer(r'\b('+'|'.join(MESES)+r')\b(?:\s+de)?(?:\s+(20\d\d))?',t))
    if len(encontrados)!=2: return []
    ano_padrao=next((int(m.group(2)) for m in encontrados if m.group(2)),timezone.localdate().year)
    return [(int(m.group(2) or ano_padrao),MESES[m.group(1)]) for m in encontrados]


def planejar(texto,analise):
    if not periodos(texto): return analise
    # M63: troca só a entrega de cálculo/comparação de faturamento; as outras perguntas da mensagem continuam.
    outras=[dict(e) for e in analise.get('entregas',[])
            if not (e.get('tipo')=='calculo' and re.search(r'fatur|compar|variac',_norm(e.get('descricao',''))))]
    return {**analise,'entregas':[{'descricao':'Comparar os dois meses por filial, com diferença e variação percentual',
        'tipo':'calculo','metrica':'comparacao','comparacao':True}]+outras}


def executar(user,c,d,defs,objetivos,texto):
    from .models import ConsultaModelo
    from .agente import _preparar, Saida
    from iativa.query.servico import Resultado, _fmt
    from .calculos import _numero
    cm=ConsultaModelo.objects.filter(codigo=d.objetivo,tabela='SD2',situacao='aprovada').first()
    if cm is None or cm.codigo not in {o.codigo for o in objetivos} or 'D2_TOTAL' not in {v['campo'] for v in cm.spec['colunas']}:
        return None
    datas=periodos(texto); saidas=[]
    for ano,mes in datas:
        pares=tuple((k,v) for k,v in d.parametros if k not in ('periodo-analise','agregacao'))
        periodo=f'01/{mes:02d}/{ano}..{calendar.monthrange(ano,mes)[1]:02d}/{mes:02d}/{ano}'
        dd=replace(d,acao='preparar_consulta',parametros=pares+(('periodo-analise',periodo),('agregacao','soma:D2_TOTAL por filial')),resposta='')
        s=_preparar(user,c,dd,None,defs,{},texto='')
        if s.resultado is None or not s.resultado.ok or s.resultado.truncado:
            return s
        saidas.append(s)
    a,b=[s.resultado for s in saidas]
    if a.escopo!=b.escopo:
        raise ValidationError('As permissões ou fontes mudaram durante a comparação; não comparei escopos diferentes.')
    if len({m for r in (a,b) if r.totais for m in r.totais.moedas})>1:
        raise ValidationError('Não comparei moedas diferentes.')
    def valores(r):
        if r.totais is None: raise ValidationError('Não consegui confirmar os totais completos da comparação.')
        indice_filial=next((i for i,v in enumerate(r.colunas) if _norm(v)=='filial'),None)
        indice_valor=next((i for i,v in enumerate(r.colunas) if _norm(v).startswith('soma')),None)
        if indice_valor is None or (indice_filial is None and len(r.escopo.get('filiais',[]))!=1):
            raise ValidationError('A fonte não entregou os valores separados por filial.')
        from iativa.accounts.filiais import rotulo
        return {(str(l[indice_filial]) if indice_filial is not None else rotulo(r.escopo['filiais'][0])):
                (_numero(l[indice_valor]) if str(l[indice_valor]).strip() else (Decimal(0) if r.totais.registros==0 else None))
                for l in r.linhas}
    va,vb=valores(a),valores(b);linhas=[]
    for filial in sorted(set(va)|set(vb)):
        x,y=va.get(filial,Decimal(0) if a.totais.registros==0 else None),vb.get(filial,Decimal(0) if b.totais.registros==0 else None)
        if x is None or y is None:
            linhas.append([filial,_fmt(x,'dinheiro') if x is not None else 'Sem valor disponível',_fmt(y,'dinheiro') if y is not None else 'Sem valor disponível','Não calculável','Não calculável'])
            continue
        percentual=_fmt((y-x)/x*100,'valor')+'%' if x else 'Indefinido (base zero)'
        linhas.append([filial,_fmt(x,'dinheiro'),_fmt(y,'dinheiro'),_fmt(y-x,'dinheiro'),percentual])
    nomes=[f'{m:02d}/{a}' for a,m in datas]
    r=Resultado(True,'Comparação dos períodos pela mesma consulta e pelos mesmos critérios. Diferença = segundo período menos primeiro.',
        colunas=['Filial',nomes[0],nomes[1],'Diferença','Variação percentual'],tipos=['texto','dinheiro','dinheiro','dinheiro','texto'],
        linhas=linhas,escopo=a.escopo,medida_calculada='comparacao',premissas=list(dict.fromkeys(a.premissas+b.premissas)))
    return Saida('',{'tipo':'calculo_resultado'},solicitacao=saidas[-1].solicitacao,resultado=r)
