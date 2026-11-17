"""Preserva detalhes explicitamente pedidos, sem alterar políticas ou regras das consultas."""
import re
from dataclasses import replace
from .periodo import _norm

PADROES = {'nome': r'nome', 'funcao': r'fun[cç][aã]o|cargo', 'admissao': r'admi',
           'cliente': r'cliente|nome', 'vencimento': r'venc', 'saldo': r'saldo|valor', 'filial': r'filia'}


def campos(texto):
    t = _norm(texto)
    if not re.search(r'\b(quais|quem|liste|listar|mostre|mostrar|detalh\w*)\b', t):
        return []
    out = []
    for nome, padrao in [('nome',r'nome'),('funcao',r'funcao|funcoes|cargo'),('admissao',r'data de admissao'),
                         ('cliente',r'cliente'),('vencimento',r'vencimento'),('saldo',r'saldo'),('filial',r'filia')]:
        if re.search(padrao,t): out.append(nome)
    return out


def somente_lista(texto):
    t=_norm(texto)
    # "Mostre faturamento por filial" pede um indicador, não uma lista de notas.
    if not set(campos(texto)) & {'nome','funcao','admissao','vencimento'} and not re.search(r'funcionari|colaborador|titulo|documento|notas|pedidos',t):
        return False
    if re.search(r'faturamento|receita|lucro',t) and not re.search(r'notas|documento|itens',t):
        return False
    return bool(campos(texto)) and not re.search(r'histor|linha do tempo|mudanc|exerceu|anteriores|\b(quantos|quantas|some|soma|total|media|ranking|maiores|menores|compare|comparar)\b',_norm(texto))


def faltam(rotulos, exigidos):
    return [c for c in exigidos if not any(re.search(PADROES[c],_norm(r)) and not (
        c=='funcao' and re.search(r'cod|codigo',_norm(r))) for r in rotulos)]


def planejar(texto, analise):
    requeridos=campos(texto)
    if not somente_lista(texto): return analise
    itens=[dict(e) for e in analise.get('entregas',[]) if e['tipo'] not in ('calculo','historico')]
    consultas=[e for e in itens if e['tipo']=='consulta']
    if not consultas:
        consultas=[{'descricao':'Listar os dados e campos explicitamente solicitados','tipo':'consulta','metrica':''}]
        itens.extend(consultas)
    for e in consultas: e['campos_pedidos']=requeridos
    return {**analise,'entregas':itens}


def completar(texto,res):
    if not somente_lista(texto) or 'filial' not in campos(texto) or not getattr(res,'ok',False): return
    filiais=res.escopo.get('filiais',[])
    if len(filiais)==1 and not any(_norm(c)=='filial' for c in res.colunas):
        from iativa.accounts.filiais import rotulo
        res.colunas.insert(0,'Filial');res.tipos.insert(0,'texto')
        res.linhas=[[rotulo(filiais[0])]+list(l) for l in res.linhas]


def orientar(user, texto, d, objetivos):
    if not somente_lista(texto): return d
    if d.acao not in ('preparar_consulta','executar_consulta','historico_cargo','criar_consulta','aprender_tabela'): return d
    pares=tuple((k,v) for k,v in d.parametros if k!='agregacao')
    objetivo=d.objetivo
    reutilizada=False
    if re.search(r'funcionari|colaborador',_norm(texto)):
        from .models import ConsultaModelo
        modelos=ConsultaModelo.objects.filter(codigo__in=[o.codigo for o in objetivos if o.modelo_id],tabela='SRA',situacao='aprovada')
        bons=[m for m in modelos if not faltam([c['rotulo'] for c in m.spec.get('colunas',[])],[c for c in campos(texto) if c!='filial'])]
        if re.search(r'ativ',_norm(texto)):
            bons=[m for m in bons if any(f['campo']=='RA_SITFOLH' and f.get('op')=='<>' and f.get('valor')=='D'
                  for f in m.spec.get('filtros_fixos',[]))]
        cm=next((m for m in bons if m.codigo==objetivo),bons[0] if bons else None)
        if cm:
            reutilizada=True
            objetivo=cm.codigo
            if not any(k=='filial-analise' for k,v in pares):
                from .agente import _filiais
                fs=_filiais(user)
                if fs: pares+=(('filial-analise','TODAS' if 'TODAS' in fs else fs[0]),)
            if cm.spec.get('campo_data') and not any(k=='periodo-analise' for k,v in pares):
                from django.utils import timezone
                pares+=(('periodo-analise',f'01/01/1900..{timezone.localdate():%d/%m/%Y}'),)
    if d.acao=='historico_cargo' and not objetivo: return d
    if d.acao in ('aprender_tabela','criar_consulta') and not reutilizada: return d
    return replace(d,acao='preparar_consulta' if reutilizada or d.acao=='historico_cargo' else d.acao,
                   objetivo=objetivo,parametros=pares,entrega='consulta')
