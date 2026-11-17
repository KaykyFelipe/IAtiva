"""Preserva detalhes explicitamente pedidos, sem alterar políticas ou regras das consultas."""
import re
from dataclasses import replace
from .periodo import _norm

PADROES = {'nome': r'nome', 'funcao': r'fun[cç][aã]o|cargo', 'admissao': r'admi',
           'cliente': r'cliente|nome', 'vencimento': r'venc', 'saldo': r'saldo', 'filial': r'filia',
           'nome_completo': r'nome\s*compl|nomecmp'}
# M63 (auditoria 17/11): rótulo parecido não prova o dado. "Valor original" não é saldo em aberto;
# "Nome abreviado"/"Fantasia" não é nome completo. O campo do dicionário, quando conhecido, decide.
CAMPOS_EXATOS = {'saldo': r'_SALDO$', 'nome_completo': r'_NOMECMP$|^A1_NOME$|^A2_NOME$',
                 'nome': r'_NOME$|_NOMECMP$', 'admissao': r'_ADMISSA$'}
NAO_E = {'nome': r'abrev|reduz|fantasia|apelido', 'nome_completo': r'abrev|reduz|fantasia|apelido',
         'saldo': r'original|emitid|bruto'}


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
    return bool(campos(texto)) and not re.search(r'histor|linha do tempo|mudanc|exerceu|anteriores|calcul|diferenc|percent|variac|projec|\b(quantos|quantas|some|soma|total|media|ranking|maiores|menores|compare|comparar)\b',_norm(texto))


def quer_ativos(texto) -> bool:
    """M63: "ativos" restringe aos ativos; "inativos", "desligados", "não ativos" não (antes r'ativ' casava "inativos")."""
    t = _norm(texto)
    if re.search(r'\binativ|\bdesligad|\bdemitid|\b(nao|sem)\s+(estao\s+)?ativ|\bex[- ]?funcionari', t):
        return False
    return bool(re.search(r'\bativ[oa]s?\b', t))


def _atende(coluna, exigido) -> bool:
    if isinstance(coluna, dict):
        rotulo, campo = _norm(coluna.get('rotulo', '')), str(coluna.get('campo', '')).upper()
    else:
        rotulo, campo = _norm(coluna), ''
    if campo and exigido in CAMPOS_EXATOS:
        return bool(re.search(CAMPOS_EXATOS[exigido], campo))
    if exigido in NAO_E and re.search(NAO_E[exigido], rotulo):
        return False
    if exigido == 'funcao' and re.search(r'cod|codigo', rotulo):
        return False
    return bool(re.search(PADROES[exigido], rotulo))


def faltam(colunas, exigidos):
    """Exigidos que nenhuma coluna atende. `colunas`: rótulos ou {"campo", "rotulo"} (o campo decide)."""
    return [c for c in exigidos if not any(_atende(col, c) for col in colunas)]


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
        pedidos=[c for c in campos(texto) if c!='filial']
        bons=[m for m in modelos if not faltam(m.spec.get('colunas',[]),pedidos)]
        if re.search(r'nome\s+complet',_norm(texto)):
            # M63: "nome completo" prefere a consulta com o nome completo do cadastro (RA_NOMECMP); sem ela, o
            # nome do cadastro (nunca abreviado/fantasia, ver NAO_E) é o mais próximo disponível.
            bons.sort(key=lambda m: bool(faltam(m.spec.get('colunas',[]),['nome_completo'])))
        if quer_ativos(texto):
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
