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
                 'nome': r'_NOME$|_NOMECMP$|_NOMCLI$|_NOMFOR$|_NOMVEND$', 'admissao': r'_ADMISSA$'}
# M144: no dicionário Protheus, tabelas auxiliares usam título genérico ("Descricao" em SRJ.RJ_DESC/SQ3.Q3_DESCSUM).
# Quando o campo técnico comprova a função/cliente/vencimento/filial, vale mesmo com título genérico; campos
# personalizados continuam podendo casar pelo rótulo (abaixo).
CAMPOS_POSITIVOS = {'funcao': r'^RJ_DESC$|^Q3_DESC\w*$|^R7_DESCFUN$|_DESCFUN$|_DESCCAR$',
                    'cliente': r'^A1_NOME$|_NOMCLI$|_CLIENTE$', 'vencimento': r'_VENCTO$|_VENCREA$',
                    'filial': r'_FILIAL$'}
CAMPOS_CODIGO = {'funcao': r'^RJ_FUNCAO$|^RA_CODFUNC$|^Q3_CARGO$|^RA_CARGO$'}
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
    if campo and exigido in CAMPOS_CODIGO and re.search(CAMPOS_CODIGO[exigido], campo):
        return False
    if campo and exigido in CAMPOS_POSITIVOS and re.search(CAMPOS_POSITIVOS[exigido], campo):
        return True
    if exigido in NAO_E and re.search(NAO_E[exigido], rotulo):
        return False
    if exigido == 'funcao' and re.search(r'cod|codigo', rotulo):
        return False
    return bool(re.search(PADROES[exigido], rotulo))


def faltam(colunas, exigidos):
    """Exigidos que nenhuma coluna atende. `colunas`: rótulos ou {"campo", "rotulo"} (o campo decide)."""
    return [c for c in exigidos if not any(_atende(col, c) for col in colunas)]


def _nao_pedida(texto, e):
    """M68: contagem que a pessoa não pediu ("Total de funcionários admitidos" quando ela só pediu a lista)."""
    if e.get('metrica')!='contagem' or any(k in e for k in ('contagem_detalhada','detalhe_contagem','comparacao')):
        return False
    return not re.search(r'\b(quant[oa]s?|quantidade|total|totais|numero|contagem|conte|contar|soma|some)\b',_norm(texto))


def planejar(texto, analise):
    requeridos=campos(texto)
    if not somente_lista(texto):
        # M68: pedido misto ("compare ... e liste os admitidos com nome e função"): cada lista exige os campos
        # da sua própria descrição; outra tabela (ex.: faturamento por filial) não a cumpre.
        itens=[dict(e) for e in analise.get('entregas',[]) if not _nao_pedida(texto,e)]
        for e in itens:
            # A descrição de uma consulta já é uma lista ("Lista dos admitidos com nome e função").
            pedidos=campos('liste '+e.get('descricao','')) if e.get('tipo')=='consulta' else []
            if pedidos and not e.get('campos_pedidos') and set(pedidos)&{'nome','funcao','admissao','cliente','vencimento','saldo'}:
                e['campos_pedidos']=pedidos
        return {**analise,'entregas':itens}
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
        if getattr(res, 'colunas_meta', None):
            res.colunas_meta.insert(0, {'campo': '_FILIAL', 'rotulo': 'Filial'})
        res.linhas=[[rotulo(filiais[0])]+list(l) for l in res.linhas]


def garantir_campos(user, cm, exigidos) -> bool:
    """M144: se uma consulta aprendida ainda não traz uma coluna pedida (ex.: função em SRJ.RJ_DESC ou nome do cliente
    em SA1.A1_NOME), inclui automaticamente o campo da própria tabela ou da tabela ligada pelo SX9 (1 ou 2 saltos)."""
    pendentes = [e for e in dict.fromkeys(exigidos or []) if e != 'filial' and faltam(cm.spec.get('colunas', []), [e])]
    if not pendentes:
        return False
    from .aprendizado import (_testar_compilacao, _tipo, ampliar_para_campos, campos_da_tabela, juncoes_possiveis,
                              sensivel)
    mudou = False
    try:
        base_info = campos_da_tabela(user, cm.tabela, automatico=True)
    except Exception:
        base_info = None
    tem = {c['campo'] for c in cm.spec.get('colunas', [])}
    locais = []
    ainda = []
    for exig in pendentes:
        achado = None
        if base_info is not None:
            achado = next((c for c in base_info['campos'] if c.nome not in tem and not sensivel(c)
                           and _atende({'campo': c.nome, 'rotulo': c.titulo or ''}, exig)), None)
        if achado is not None:
            locais.append({'campo': achado.nome, 'rotulo': (achado.titulo or achado.nome).strip(),
                           'tipo': _tipo(achado)})
            tem.add(achado.nome)
        else:
            ainda.append(exig)
    if locais:
        antigo = cm.spec
        cm.spec = {**cm.spec, 'colunas': list(cm.spec.get('colunas', [])) + locais}
        try:
            _testar_compilacao(cm)
            cm.save(update_fields=['spec'])
            mudou = True
        except Exception:
            cm.spec = antigo
    if ainda:
        try:
            possiveis = juncoes_possiveis(user, cm.tabela, profundidade=2)
        except Exception:
            possiveis = []
        campos_juncao = []
        for exig in ainda:
            for j in possiveis:
                chaves_j = {a for a, _ in j['pares']}
                c = next((x for x in j['campos'] if x.nome not in tem and x.nome not in chaves_j
                          and _atende({'campo': x.nome, 'rotulo': x.titulo or ''}, exig)), None)
                if c is not None:
                    campos_juncao.append(c.nome)
                    tem.add(c.nome)
                    break
        if campos_juncao and ampliar_para_campos(user, cm, campos_juncao):
            mudou = True
    return mudou


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
