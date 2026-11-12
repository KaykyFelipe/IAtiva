"""Investigação de divergências: hipóteses independentes do valor esperado, só leitura.

O modelo escolhe a consulta, mas sucesso técnico não conclui uma investigação. Este executor
reutiliza seus critérios, consulta decomposições e fontes comparáveis e entrega evidências
transitórias. Não procura combinações que aproximem um número informado pela pessoa.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
import re
import unicodedata

from django.core.exceptions import ValidationError

from iativa.accounts.authz import Permissao, PermissaoNegada, filiais_autorizadas, tem_permissao
from iativa.accounts.filiais import rotulo
from iativa.metadata.catalogo import abrir_catalogo
from iativa.metadata.descoberta import _Visibilidade
from iativa.query.compilador import compilar
from iativa.query.executor import executar
from iativa.query.plano import Campo, Coluna, Filtro, Juncao, Plano, montar, REGRAS
from iativa.query.servico import Resultado, _fmt, _valores, _juncoes_sem_duplicar, _cfg
from iativa.query.validador import validar

from . import andamento, memoria, prazo
from .aprendizado import sensivel
from .contratos import OBJETIVOS, objetivos_para


def solicitado(texto):
    t = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().lower()
    return bool(re.search(r'fatur|total|soma|valor|milh', t) and re.search(
        r'investig|divergen|diferenc|inflad|valor correto|motivo|por que|porque|test.*valor|corrig|nao bate', t))


def reutilizavel(texto):
    # Uma continuação pode reutilizar os critérios; um pedido com escopo novo precisa interpretá-los.
    t = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().lower()
    return not re.search(r'\d{1,4}[/.-]\d|\b20\d\d\b|janeiro|fevereiro|marco|abril|maio|junho|julho|'
                         r'agosto|setembro|outubro|novembro|dezembro|filia|empresa|periodo', t)


@dataclass
class Bloco:
    titulo: str
    resultado: object = None
    mensagem: str = ''


@dataclass
class Diagnostico:
    blocos: list = field(default_factory=list)
    resumo: str = ''
    consolidado: bool = True
    completo: bool = True


class CatalogoDaInvestigacao:
    """Snapshot de metadados desta requisição; não guarda SQL ou dados do ERP."""
    def __init__(self, origem):
        self.origem, self.cache = origem, {}

    def __getattr__(self, nome):
        return getattr(self.origem, nome)

    def tabelas(self, aliases, com_campos=False):
        faltam = [a for a in aliases if a not in self.cache]
        if faltam:
            ts = self.origem.tabelas(faltam, com_campos=True)
            self.cache.update({a: ts.get(a) for a in faltam})
        return {a: self.cache[a] for a in aliases if self.cache[a] is not None}


class Auditor:
    def __init__(self, user, s, cat, interrompido=None):
        self.user, self.s, self.cat, self.interrompido = user, s, cat, interrompido
        self.n = 0

    def campos(self, alias):
        vis = _Visibilidade({self.s.setor_id}, [alias])
        if not vis.tabela(alias):
            raise PermissaoNegada('Fonte não autorizada para a investigação.')
        t = self.cat.tabelas([alias], com_campos=True).get(alias)
        if not t or not t.campo_filial:
            raise ValidationError(f'{alias}: fonte ou campo de filial indisponível.')
        return t, {c.nome: c for c in t.campos if c.consultavel and vis.campo(alias, c.nome) and not sensivel(c)}

    def ler(self, plano):
        from .agente import Interrompido
        prazo.exigir()
        if self.interrompido and self.interrompido():
            raise Interrompido()
        if (self.s.usuario_id != self.user.pk or
                self.s.objetivo not in {o.codigo for o in objetivos_para(self.user)} or
                not tem_permissao(self.user, Permissao.CONSULTAR, self.s.setor) or
                plano.filial not in filiais_autorizadas(self.user, self.s.setor)):
            raise PermissaoNegada('A autorização mudou durante a investigação.')
        tabelas = {'e1': plano.base, **{j.alias: j.tabela for j in plano.juncoes}}
        permitidas = set()
        campos = {}
        for apelido, alias in tabelas.items():
            t, cs = self.campos(alias)
            if (any(c.agregacao in ('SUM', 'AVG') for c in plano.colunas) and
                    any(c.consultavel and c.nome.endswith('_MOEDA') and c.nome not in cs for c in t.campos)):
                raise PermissaoNegada('A moeda necessária à conferência não está autorizada.')
            permitidas.add(t.fisica)
            campos[apelido] = set(cs)
        usados = [c.campo for c in plano.colunas] + [f.campo for f in plano.filtros]
        usados += [f.valor for f in plano.filtros if isinstance(f.valor, Campo)]
        for j in plano.juncoes:
            usados += [Campo(j.alias, a) for a, _ in j.pares] + [Campo('e1', b) for _, b in j.pares]
        if any(c.nome not in campos.get(c.alias, ()) for c in usados):
            raise PermissaoNegada('Um campo da investigação não está autorizado.')
        self.n += 1
        if self.n > 96:
            raise ValidationError('Limite de consultas da investigação atingido.')
        cfg = _cfg()
        cfg['timeout_s'] = prazo.limitar(cfg['timeout_s'])
        if plano.juncoes and not _juncoes_sem_duplicar(self.cat, plano, cfg, permitidas):
            raise ValidationError('Junção com chave repetida: não agreguei valores duplicados.')
        comp = compilar(plano, self.cat, 200)
        validar(comp.sql, permitidas, 201)
        rows, corte = executar(self.cat, comp.sql, comp.params, 200,
            prazo.limitar(cfg['timeout_s']), validar_final=lambda sql: validar(sql, permitidas, 201))
        if corte:
            raise ValidationError('Decomposição excede o limite de grupos; não foi tratada como completa.')
        return comp, rows


def _agregado(plano, numericos, campos, grupos=(), filtros=()):
    p = deepcopy(plano)
    p.top, p.ordenar, p.chave_unica = 0, [], ()
    grupos = list(grupos)
    # Nunca misturar moedas, mesmo quando o valor é classificado como número no SX3.
    moeda = next((n for n in campos if n.endswith('_MOEDA')), None)
    if moeda and not any(g.nome.endswith('_MOEDA') for g in grupos):
        grupos.append(Campo('e1', moeda))
    p.colunas = [Coluna(c.nome, c) for c in grupos]
    filial = next(f.campo for f in p.filtros if f.campo.nome.endswith('_FILIAL'))
    p.colunas += [Coluna('Quantidade', filial, 'COUNT', 'inteiro')]
    p.colunas += [Coluna('Soma ' + n, Campo('e1', n), 'SUM', 'valor') for n in numericos]
    p.agrupar = bool(grupos)
    p.filtros += list(filtros)
    return p


def investigar(user, s, inicial, interrompido=None):
    if getattr(inicial, 'consolidado', False):
        out = Diagnostico(resumo='Investigação por setor, sem somar escopos sobrepostos ou critérios diferentes.')
        for bloco in inicial.blocos:
            if bloco.resultado is not None and getattr(bloco.resultado, 'ok', False):
                sub = investigar(user, bloco.resultado.solicitacao, bloco.resultado, interrompido)
                out.completo = out.completo and sub.completo
                out.blocos.append(Bloco('Setor ' + bloco.setor, sub))
            else:
                out.completo = False
                out.blocos.append(Bloco('Setor ' + bloco.setor, mensagem=bloco.mensagem))
        return out
    obj = OBJETIVOS[s.objetivo]
    if not obj.modelo_id:
        return Diagnostico([Bloco('Consulta inicial', inicial)],
            'A divergência ainda não foi explicada. Esta fonte não oferece decomposições estruturadas para investigação.',
            completo=False)
    vals = _valores(user, s)
    vals[REGRAS] = memoria.regras_aplicaveis(user, s.setor, obj.tabelas[0])
    filiais = sorted(filiais_autorizadas(user, s.setor)) if vals.get('filial-analise') == 'TODAS' else [vals.get('filial-analise')]
    if not filiais:
        raise PermissaoNegada('Nenhuma filial autorizada para a investigação.')
    out = Diagnostico([Bloco('Cálculo original — ainda não validado como regra de negócio', inicial)])
    with abrir_catalogo() as origem:
        cat = CatalogoDaInvestigacao(origem)
        a = Auditor(user, s, cat, interrompido)
        base = montar(s.objetivo, {**vals, 'filial-analise': filiais[0]})
        tabela, cs = a.campos(base.base)
        nums = list(dict.fromkeys(c.campo.nome for c in base.colunas
                    if c.campo.alias == 'e1' and c.campo.nome in cs and cs[c.campo.nome].tipo == 'N'))[:3]
        if not nums:
            out.completo = False
            out.resumo = 'Não há medida numérica autorizada para decompor; a divergência continua pendente.'
            return out
        evidencias = {}
        def etapa(titulo, criar):
            andamento.avisar('Investigando divergência — ' + titulo)
            linhas, colunas, tipos, brutas = [], [], [], []
            try:
                for filial in filiais:
                    p = criar(filial)
                    comp, rows = a.ler(p)
                    colunas, tipos = ['Filial'] + list(comp.rotulos), ['texto'] + list(comp.tipos)
                    linhas += [[rotulo(filial)] + [_fmt(v, t) for v, t in zip(row, comp.tipos)] for row in rows]
                    brutas += [dict(zip(comp.rotulos, row)) for row in rows]
                evidencias[titulo] = brutas
                out.blocos.append(Bloco(titulo, Resultado(True, titulo, colunas, tipos, linhas,
                    premissas=list(base.premissas) + ['Critérios originais preservados; hipótese diagnóstica, sem aprovação automática.'])))
            except PermissaoNegada:
                raise
            except Exception as exc:
                from .agente import Interrompido
                if isinstance(exc, Interrompido):
                    raise
                out.completo = False
                motivo = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
                out.blocos.append(Bloco(titulo, mensagem='Não concluído: ' + motivo))

        def original(filial):
            return montar(s.objetivo, {**vals, 'filial-analise': filial})

        etapa('Conferência por filial e moeda, sem ranking',
              lambda f: _agregado(original(f), nums, cs))
        prefixo = base.base[1:] + '_'
        for nome in (prefixo + 'TIPO', prefixo + 'SERIE', prefixo + 'STATUS'):
            if nome in cs:
                etapa('Decomposição por ' + nome,
                      lambda f, n=nome: _agregado(original(f), nums, cs, [Campo('e1', n)]))
        datas = [c.campo.nome for c in base.colunas if c.campo.nome in cs and cs[c.campo.nome].tipo == 'D']
        if not datas:
            datas = [f.campo.nome for f in base.filtros if f.campo.nome in cs and cs[f.campo.nome].tipo == 'D']
        if datas:
            def intervalo(f):
                p = _agregado(original(f), nums, cs)
                p.colunas += [Coluna('Primeira data', Campo('e1', datas[0]), 'MIN', 'data'),
                              Coluna('Última data', Campo('e1', datas[0]), 'MAX', 'data')]
                return p
            etapa('Datas efetivamente encontradas no período', intervalo)
        if base.base == 'SF2':
            if 'F2_DUPL' in cs:
                for op, titulo in (('=', 'Notas sem título associado'), ('<>', 'Notas com título associado')):
                    etapa(titulo + ' — hipótese financeira', lambda f, op=op: _agregado(
                        original(f), nums, cs, filtros=[Filtro(Campo('e1', 'F2_DUPL'), op, '')]))
            if 'F2_VALFAT' in cs and cs['F2_VALFAT'].tipo == 'N' and 'F2_VALFAT' not in nums:
                etapa('Comparação com valor faturado F2_VALFAT — base de cálculo distinta',
                      lambda f: _agregado(original(f), ['F2_VALFAT'], cs))
            _itens(a, out, etapa, base)
    achados = _achados(evidencias)
    out.resumo = (achados + ' Investigação executada com critérios independentes do valor esperado. ' +
        ('Há etapas incompletas; ' if not out.completo else '') +
        'as decomposições abaixo mostram onde os valores se concentram. Soma de notas não comprova, sozinha, '
        'faturamento de vendas. Título associado e TES são hipóteses, não a definição aprovada. '
        'Não alterei critérios para aproximar o número informado e não gravei regra nova. '
        'A conciliação permanece pendente enquanto não houver evidência que determine quais operações representam receita.')
    return out


def _itens(a, out, etapa, base):
    # Fonte independente por item. Nunca somar o cabeçalho depois de juntá-lo aos seus itens.
    if base.juncoes:
        out.completo = False
        out.blocos.append(Bloco('Conferência dos itens', mensagem=
            'Não executada: há filtros/junções do cabeçalho sem equivalência comprovada nos itens; não os removi.'))
        return
    try:
        t, cs = a.campos('SD2')
        if not {'D2_EMISSAO', 'D2_TOTAL', 'D2_TES'} <= set(cs):
            raise ValidationError('Campos autorizados de data, total ou TES indisponíveis em SD2.')
    except (ValidationError, PermissaoNegada):
        out.completo = False
        out.blocos.append(Bloco('Conferência dos itens', mensagem='SD2 indisponível ou não autorizada; hipótese não testada.'))
        return

    # Transporta os filtros do cabeçalho sem perdê-los e obtém a moeda dele quando não existe no item.
    _, cab = a.campos('SF2')
    pares = tuple((f'F2_{s}', f'D2_{s}') for s in ('DOC', 'SERIE', 'CLIENTE', 'LOJA'))
    vincular = all(x in cab and y in cs for x, y in pares)
    extras = [x for x in base.filtros if not x.campo.nome.endswith('_FILIAL') and x.campo.nome != 'F2_EMISSAO']
    if not vincular and (extras or 'D2_MOEDA' not in cs):
        out.completo = False
        out.blocos.append(Bloco('Conferência dos itens', mensagem=
            'Chave ou moeda indisponível para preservar os critérios do cabeçalho nos itens; não somei bases incompatíveis.'))
        return

    def itens(f):
        fs = [Filtro(Campo('e1', t.campo_filial), '=', f)]
        if vincular:
            fs += [Filtro(Campo('j1', x.campo.nome), x.op,
                          Campo('j1', x.valor.nome) if isinstance(x.valor, Campo) else x.valor)
                   for x in base.filtros if not x.campo.nome.endswith('_FILIAL')]
            fs.append(Filtro(Campo('j1', 'F2_DOC'), '<>', ''))
        else:
            fs += [Filtro(Campo('e1', 'D2_EMISSAO'), x.op, x.valor) for x in base.filtros if x.campo.nome == 'F2_EMISSAO']
        return Plano('diagnostico-itens', base='SD2', filial=f, filtros=fs,
                     juncoes=[Juncao('j1', 'SF2', pares)] if vincular else [])

    def agrupar(p, grupos):
        if vincular:
            if 'F2_MOEDA' not in cab:
                raise ValidationError('Moeda do cabeçalho não autorizada; não misturei moedas.')
            grupos += [Campo('j1', 'F2_MOEDA')]
        return _agregado(p, ['D2_TOTAL'], cs, grupos)

    etapa('Itens SD2 por TES — somados sem multiplicar cabeçalhos',
          lambda f: agrupar(itens(f), [Campo('e1', 'D2_TES')]))
    if 'D2_CF' in cs:
        etapa('Itens SD2 por CFOP', lambda f: agrupar(itens(f), [Campo('e1', 'D2_CF')]))
    try:
        _, sf4 = a.campos('SF4')
        if not {'F4_CODIGO', 'F4_DUPLIC'} <= set(sf4):
            raise ValidationError('Indicador financeiro da TES indisponível.')
    except (ValidationError, PermissaoNegada):
        out.completo = False
        out.blocos.append(Bloco('Hipótese TES financeira', mensagem='SF4/indicador financeiro indisponível ou não autorizado.'))
        return

    def financeira(f):
        p = itens(f)
        p.juncoes += [Juncao('j2', 'SF4', (('F4_CODIGO', 'D2_TES'),))]
        return agrupar(p, [Campo('j2', 'F4_DUPLIC')])
    etapa('Itens por geração de duplicata na TES atual — configuração histórica não comprovada', financeira)


def _achados(evidencias):
    titulo = 'Itens por geração de duplicata na TES atual — configuração histórica não comprovada'
    totais = {}
    for r in evidencias.get(titulo, []):
        if r.get('Soma D2_TOTAL') is None:
            continue
        moeda = r.get('F2_MOEDA', r.get('D2_MOEDA', 'não identificada'))
        indicador = str(r.get('F4_DUPLIC') or '').strip()
        chave = (str(moeda), indicador)
        totais[chave] = totais.get(chave, Decimal(0)) + Decimal(str(r.get('Soma D2_TOTAL') or 0))
    if not totais:
        return ''
    partes = []
    for (moeda, indicador), valor in sorted(totais.items()):
        nome = {'S': 'TES que gera duplicata', 'N': 'TES que não gera duplicata'}.get(indicador, 'TES não classificada')
        partes.append(f'{nome}: {_fmt(valor, "valor")} (moeda {moeda})')
    nota = ('Há valores positivos em TES que não gera duplicata no recorte. A soma ampla não filtra esse indicador; '
            'isso é uma causa possível da diferença de definição. ' if any(v > 0 and k[1] == 'N' for k, v in totais.items()) else '')
    return (nota + 'Cenários encontrados nos itens, pelo cadastro atual: ' + '; '.join(partes) + '. '
            'São valores de itens, não somas repetidas do cabeçalho. A configuração atual da TES não comprova '
            'sua configuração na emissão, e gerar duplicata não equivale automaticamente a receita.')
