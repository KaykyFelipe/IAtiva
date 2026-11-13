"""Histórico funcional: nome → vínculos → histórico → descrições → conclusão.

Resultados nunca vão ao modelo; a camada do chat salva a apresentação autorizada na M44. Cada leitura reconfere
setor, filial e restrições de tabela/campo e usa o compilador e validador existentes.
"""
import re
import unicodedata
from calendar import monthrange
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, filiais_autorizadas, setores_autorizados, tem_permissao
from iativa.accounts.models import Department
from iativa.audit.servicos import registrar
from iativa.metadata.catalogo import abrir_catalogo
from iativa.metadata.descoberta import _Visibilidade
from iativa.query.compilador import compilar
from iativa.query.executor import executar
from iativa.query.plano import Campo, Coluna, Filtro, Plano, padrao_busca
from iativa.query.servico import Resultado
from iativa.query.validador import validar

from . import andamento, prazo
from .aprendizado import criar_automatico, sensivel
from .contratos import OBJETIVOS, objetivos_para
from .models import ConsultaModelo


def norm(texto):
    return unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().lower()


def solicitado(texto):
    t = norm(texto)
    return pede_tempo_estagio(texto) or bool(re.search(r'carg|funcao|funcoes', t) and re.search(
        r'histor|mud|anterior|recente|atual|qual|quais|linha do tempo|cargo e funcao', t))


def pede_tempo_estagio(texto):
    t = norm(texto)
    return bool(re.search(r'estagi', t) and re.search(r'tempo|quanto|durac|meses|anos|dias|ficou|permane', t))


def nome_contexto(user, conversa, texto, decisao):
    # O nome vem do pedido/critério do usuário, nunca de linhas devolvidas pelo ERP.
    alvo = (decisao.alvo or '').strip()
    busca = next((v for k, v in decisao.parametros if k == 'texto-busca'), '')
    if busca:
        return busca.strip()
    if pede_tempo_estagio(texto):
        m = re.search(r'est[aá]gi\w*.*?\b(?:do|da|de)\s+(?:(?:funcion[aá]rio|colaborador|usu[aá]rio)\s+)?(.+)', texto, re.I)
        if m and norm(m.group(1).strip(' .?!')) not in ('ele', 'ela', 'estagio'):
            return m.group(1).strip(' .?!')
    m = re.search(r'(?:(?:hist[oó]rico|qual|quais).*?(?:cargo|fun[cç][aã]o|fun[cç][oõ]es).*?)\s+(?:do|da|de)\s+(.+)', texto, re.I)
    if m:
        return re.split(r'\s+(?:desde|a partir de|em todas as filiais|nas filiais|na filial|nos [uú]ltimos|no per[ií]odo)\b',
                        m.group(1), maxsplit=1, flags=re.I)[0].strip(' .?!')
    if decisao.acao == 'historico_cargo' and alvo:
        return alvo
    for msg in conversa.mensagens.filter(papel='ia').order_by('-id')[:6]:
        if msg.ficha.get('tipo') == 'historico_cargo' and msg.ficha.get('busca'):
            return msg.ficha['busca']
        if msg.solicitacao_id and msg.solicitacao.usuario_id == user.pk:
            from iativa.query.servico import _valores
            try:
                if 'SRA' in OBJETIVOS[msg.solicitacao.objetivo].tabelas:
                    nome = _valores(user, msg.solicitacao).get('texto-busca')
                    if nome:
                        return nome
            except (KeyError, ValidationError):
                continue
    return ''


def _data(valor):
    try:
        t = str(valor).strip()
        return date.fromisoformat(t) if '-' in t else datetime.strptime(t, '%Y%m%d').date()
    except (ValueError, TypeError):
        return None


def _fmt(valor):
    d = _data(valor)
    return d.strftime('%d/%m/%Y') if d else 'não informada'


def _setor(user, cat):
    # Mesma capacidade de aprendizado automático já autorizada, sem ampliar filiais ou campos.
    disponiveis = objetivos_para(user)
    for alias, campos in (('SRA', ['RA_MAT', 'RA_NOME', 'RA_ADMISSA', 'RA_DEMISSA', 'RA_CODFUNC']),
                          ('SR7', ['R7_MAT', 'R7_DATA', 'R7_FUNCAO', 'R7_DESCFUN'])):
        if not any(o.modelo_id and o.tabelas[0] == alias for o in disponiveis):
            criar_automatico(user, {'tabela': alias, 'nome': f'Histórico funcional — {alias}', 'colunas': campos})
    modelos = ConsultaModelo.objects.filter(codigo__in=[o.codigo for o in objetivos_para(user) if o.modelo_id])
    autorizados = setores_autorizados(user, Permissao.CONSULTAR)
    candidatos = autorizados.copy()
    for alias in ('SRA', 'SR7'):
        candidatos &= set(modelos.filter(tabela=alias).values_list('setores__pk', flat=True))
    if not candidatos:
        raise PermissaoNegada('Não há um setor autorizado para consultar os vínculos e seu histórico juntos.')
    return Department.objects.filter(pk__in=candidatos, ativo=True).order_by('pk').first()


class Leitura:
    def __init__(self, user, setor, cat, interrompido):
        self.user, self.setor, self.cat = user, setor, cat
        self.interrompido = interrompido
        self.n = 0
        self.campos = {}
        self.filiais = set()

    def consultar(self, alias, filial, campos, filtros=(), obrigatorios=(), limite=500):
        from .agente import Interrompido
        prazo.exigir()
        if self.interrompido and self.interrompido():
            raise Interrompido()
        if not tem_permissao(self.user, Permissao.CONSULTAR, self.setor) or filial not in filiais_autorizadas(self.user, self.setor):
            raise PermissaoNegada('A permissão ou filial mudou durante a consulta do histórico.')
        vis = _Visibilidade({self.setor.pk}, [alias])
        if not vis.tabela(alias):
            raise PermissaoNegada('Tabela do histórico não autorizada para este setor.')
        tabela = self.cat.tabelas([alias], com_campos=True).get(alias)
        if tabela is None or not tabela.campo_filial:
            raise ValidationError(f'A fonte {alias} não está disponível no dicionário.')
        # No dicionário real, R7_DATA é descrita como "Data Alteracao Salarial". Ela é a data
        # do evento, não seu valor financeiro; não selecionar campos de salário. Uma restrição
        # explícita continua prevalecendo, inclusive para essa data.
        disponiveis = {c.nome for c in tabela.campos if c.consultavel and vis.campo(alias, c.nome)
                       and (not sensivel(c) or (alias == 'SR7' and c.nome == 'R7_DATA' and c.tipo == 'D'))}
        if not set(obrigatorios) <= disponiveis:
            raise ValidationError(f'Faltam campos autorizados para consultar {alias}.')
        campos = [c for c in campos if c in disponiveis]
        self.campos.setdefault(alias, set()).update(campos + [f.campo.nome for f in filtros])
        self.filiais.add(filial)
        if not {f.campo.nome for f in filtros} <= disponiveis:
            raise PermissaoNegada('Um campo necessário ao filtro não está autorizado.')
        plano = Plano('historico-funcional', base=alias, filial=filial,
                      colunas=[Coluna(c, Campo('e1', c)) for c in campos],
                      filtros=[Filtro(Campo('e1', tabela.campo_filial), '=', filial), *filtros])
        comp = compilar(plano, self.cat, limite)
        permitidas = {tabela.fisica}
        validar(comp.sql, permitidas, limite + 1)
        self.n += 1
        if self.n > 80:
            raise ValidationError('Limite de consultas atingido; restrinja o nome ou as filiais.')
        linhas, cortou = executar(self.cat, comp.sql, comp.params, limite,
                                  prazo.limitar(settings.IATIVA_CONSULTA['timeout_s']),
                                  validar_final=lambda sql: validar(sql, permitidas, limite + 1))
        if cortou:
            raise ValidationError('O histórico excede o limite de registros; refine o pedido. Não apresentei uma conclusão parcial como completa.')
        return [dict(zip(campos, (str(v or '').strip() for v in linha))) for linha in linhas]


def consultar(user, conversa, nome, periodo=None, interrompido=None, filial=None, tempo_estagio=False):
    if len(nome.strip()) < 3 or not padrao_busca(nome):
        raise ValidationError('Informe o nome do funcionário para consultar o histórico de cargos e funções.')
    if isinstance(periodo, str) and periodo:
        from .conversao import _data as data_parametro
        partes = periodo.split('..')
        if len(partes) != 2:
            raise ValidationError('Período inválido para o histórico.')
        periodo = dict(zip(('inicio', 'fim'), map(data_parametro, partes)))
    if periodo and periodo['inicio'] > periodo['fim']:
        raise ValidationError('O início do período precisa ser anterior ao fim.')
    andamento.avisar('Localizando as matrículas para consultar o histórico de cargos e funções')
    with abrir_catalogo() as cat:
        setor = _setor(user, cat)
        leitor = Leitura(user, setor, cat, interrompido)
        autorizadas = filiais_autorizadas(user, setor)
        if filial and filial != 'TODAS' and filial not in autorizadas:
            raise PermissaoNegada('A filial solicitada não está autorizada.')
        filiais = [filial] if filial and filial != 'TODAS' else sorted(autorizadas)
        if not filiais:
            raise PermissaoNegada('Nenhuma filial autorizada para o histórico.')
        vinculos = []
        for filial in filiais:
            rows = leitor.consultar('SRA', filial,
                ['RA_MAT', 'RA_NOME', 'RA_ADMISSA', 'RA_DEMISSA', 'RA_CODFUNC', 'RA_CARGO', 'RA_CC'],
                [Filtro(Campo('e1', 'RA_NOME'), 'LIKE', padrao_busca(nome))],
                ['RA_MAT', 'RA_NOME', 'RA_ADMISSA', 'RA_DEMISSA', 'RA_CODFUNC'], limite=50)
            vinculos += [(filial, r) for r in rows]
        nomes = {norm(r['RA_NOME']).strip() for _, r in vinculos}
        escopo_inicial = {"setor": setor.pk, "filiais": sorted(leitor.filiais),
                          "campos": {a: sorted(cs) for a, cs in leitor.campos.items()}}
        if len(nomes) > 1:
            opcoes = sorted({r['RA_NOME'] for _, r in vinculos})
            return Resultado(True, 'Encontrei nomes diferentes. Informe o nome completo de qual pessoa deseja o histórico.',
                             colunas=['Nome encontrado'], tipos=['texto'], linhas=[[n] for n in opcoes], escopo=escopo_inicial)
        if not vinculos:
            return Resultado(True, 'Nenhum vínculo encontrado para esse nome nas filiais autorizadas.', escopo=escopo_inicial)
        if len(vinculos) > 30:
            raise ValidationError('Há muitos vínculos com esse nome; informe um nome mais específico.')
        avisos, cache = [], {}

        def descricao(alias, filial, codigo, chave, campo, cc=''):
            if not codigo:
                return 'não informado'
            k = (alias, filial, codigo, cc)
            if k not in cache:
                try:
                    filtros = [Filtro(Campo('e1', chave), '=', codigo)]
                    tabela = cat.tabelas([alias], com_campos=True).get(alias)
                    if alias == 'SQ3' and cc and tabela and any(c.nome == 'Q3_CC' and c.consultavel for c in tabela.campos):
                        filtros.append(Filtro(Campo('e1', 'Q3_CC'), '=', cc))
                    rs = leitor.consultar(alias, filial, [campo], filtros, [chave, campo])
                    ds = {r[campo] for r in rs if r[campo]}
                    cache[k] = next(iter(ds)) if len(ds) == 1 else f'código {codigo} (descrição não confirmada)'
                except (ValidationError, PermissaoNegada):
                    cache[k] = f'código {codigo} (descrição indisponível)'
            return cache[k]

        eventos, cadastros, historicos = [], [], {}
        hoje = timezone.localdate()
        for filial, r in vinculos:
            andamento.avisar('Consultando alterações de função/cargo das matrículas encontradas')
            filtros = [Filtro(Campo('e1', 'R7_MAT'), '=', r['RA_MAT'])]
            if periodo:
                filtros += [Filtro(Campo('e1', 'R7_DATA'), '>=', periodo['inicio'].replace('-', '')),
                            Filtro(Campo('e1', 'R7_DATA'), '<=', periodo['fim'].replace('-', ''))]
            hs = leitor.consultar('SR7', filial,
                ['R7_MAT', 'R7_DATA', 'R7_SEQ', 'R7_FUNCAO', 'R7_DESCFUN', 'R7_CARGO', 'R7_DESCCAR', 'R7_CC'],
                filtros, ['R7_MAT', 'R7_DATA'])
            historicos[(filial, r['RA_MAT'])] = []
            for h in hs:
                func = h.get('R7_DESCFUN') or descricao('SRJ', filial, h.get('R7_FUNCAO'), 'RJ_FUNCAO', 'RJ_DESC')
                cargo = h.get('R7_DESCCAR') or descricao('SQ3', filial, h.get('R7_CARGO'), 'Q3_CARGO', 'Q3_DESCSUM', h.get('R7_CC', ''))
                d = _data(h['R7_DATA'])
                historicos[(filial, r['RA_MAT'])].append((d, h.get('R7_SEQ', ''), func, cargo))
                if not d:
                    avisos.append('Há registros históricos com data inválida; não foram usados para ordenar alterações.')
                eventos.append((d or date.min, h.get('R7_SEQ', ''), [filial, r['RA_MAT'], _fmt(h['R7_DATA']),
                    func, cargo, 'SR7: registro histórico' + (' previsto' if d and d > hoje else '')]))
            func = descricao('SRJ', filial, r['RA_CODFUNC'], 'RJ_FUNCAO', 'RJ_DESC')
            cargo = descricao('SQ3', filial, r.get('RA_CARGO'), 'Q3_CARGO', 'Q3_DESCSUM', r.get('RA_CC', ''))
            cadastros.append((filial, r, func, cargo))
            eventos.append((date.max, '', [filial, r['RA_MAT'], _fmt(r['RA_ADMISSA']), func, cargo,
                'SRA: cadastro do vínculo; data de admissão, não data da mudança de função']))
        eventos.sort(key=lambda x: (x[0], x[1], x[2][0], x[2][1]))
        ativos = [v for v in cadastros if _data(v[1]['RA_ADMISSA']) and _data(v[1]['RA_ADMISSA']) <= hoje
                  and (not v[1]['RA_DEMISSA'] or (_data(v[1]['RA_DEMISSA']) and _data(v[1]['RA_DEMISSA']) > hoje))]
        ultimos = ativos or [v for v in cadastros if _data(v[1]['RA_ADMISSA']) and _data(v[1]['RA_ADMISSA']) <= hoje]
        recentes = sorted(ultimos, key=lambda v: _data(v[1]['RA_ADMISSA']), reverse=True)
        resumo = 'Histórico de cargos e funções consultado nas filiais autorizadas.'
        anteriores = list(dict.fromkeys(e[2][3] for e in eventos if e[2][-1].startswith('SR7')
                                        and date.min < e[0] <= hoje and e[2][3] != 'não informado'))
        if anteriores:
            resumo += ' Funções registradas na SR7, em ordem temporal: ' + ' → '.join(anteriores) + '.'
        if recentes:
            f, r, func, cargo = recentes[0]
            resumo += f" No vínculo de admissão mais recente encontrado (matrícula {r['RA_MAT']}, filial {f}), a função cadastrada é {func}; cargo: {cargo}."
            outros = [v for v in cadastros if (v[0], v[1]['RA_MAT']) != (f, r['RA_MAT'])
                      and _data(v[1]['RA_ADMISSA']) and _data(v[1]['RA_ADMISSA']) <= _data(r['RA_ADMISSA'])]
            if outros:
                resumo += ' Nos vínculos anteriores encontrados com esse nome, consta: ' + '; '.join(
                    f"{v[2]} (matrícula {v[1]['RA_MAT']}, filial {v[0]})" for v in outros) + '.'
        if len(ativos) > 1:
            resumo += ' Há mais de um vínculo sem encerramento efetivo; consulte cada matrícula, sem presumir uma única função atual.'
        if not any(e[2][-1].startswith('SR7') for e in eventos):
            resumo += ' Não há registros na SR7 com esses critérios; os cadastros não comprovam quando a função mudou.'
        premissas = ['Consulta encadeada: nome → matrículas por filial → SR7 → descrições de função/cargo.',
                     'O histórico abrange todo o período disponível, sem assumir os últimos 12 meses.' if not periodo else f"Período da SR7: {periodo['inicio']} a {periodo['fim']}.",
                     'Matrículas com o mesmo nome são apresentadas separadamente: nome não comprova identidade entre vínculos.',
                     'Registros da SR7 podem incluir alterações salariais sem troca de função. Não se presume promoção ou efetivação.',
                     'Os cadastros mostram a função atual/última de cada vínculo; sua admissão não é a data comprovada da mudança.',
                     f'Consultado em {timezone.localtime():%d/%m/%Y %H:%M}.', *sorted(set(avisos))]
        if tempo_estagio:
            resumo = _resumir_estagio(cadastros, historicos, hoje) + ' ' + resumo
        registrar('consulta_executada', 'agente', usuario=user, setor=setor, estado_novo='historico_funcional',
                  resultado='ok', linhas=len(eventos))
        return Resultado(True, resumo, colunas=['Filial', 'Matrícula', 'Data', 'Função', 'Cargo', 'Origem / interpretação'],
                         tipos=['texto'] * 6, linhas=[e[2] for e in eventos], premissas=premissas,
                         escopo={"setor": setor.pk, "filiais": sorted(leitor.filiais),
                                 "campos": {a: sorted(cs) for a, cs in leitor.campos.items()}})


def _duracao(inicio, fim):
    meses = (fim.year - inicio.year) * 12 + fim.month - inicio.month
    def avancar(n):
        ano, mes = divmod(inicio.year * 12 + inicio.month - 1 + n, 12)
        return date(ano, mes + 1, min(inicio.day, monthrange(ano, mes + 1)[1]))
    if avancar(meses) > fim:
        meses -= 1
    dias = (fim - avancar(meses)).days
    partes = ([f"{meses} {'mês' if meses == 1 else 'meses'}"] if meses else [])
    if dias or not partes:
        partes.append(f"{dias} {'dia' if dias == 1 else 'dias'}")
    return ' e '.join(partes) + f" ({(fim - inicio).days} dias corridos)"


def _resumir_estagio(cadastros, historicos, hoje):
    frases = []
    for filial, r, func, cargo in cadastros:
        inicio, demissao = _data(r['RA_ADMISSA']), _data(r['RA_DEMISSA'])
        fim = min(hoje, demissao) if demissao else hoje
        if not inicio or inicio > fim or (r['RA_DEMISSA'] and not demissao):
            continue
        hs = sorted([h for h in historicos[(filial, r['RA_MAT'])] if h[0] and h[0] <= fim], key=lambda h: (h[0], h[1]))
        intervalos = []
        for i, h in enumerate(hs):
            a, b = max(inicio, h[0]), min(fim, hs[i + 1][0]) if i + 1 < len(hs) else fim
            if i + 1 == len(hs) and 'estagi' not in norm(func + ' ' + cargo):
                continue  # O cadastro mudou sem evento de saída do estágio: não inventar sua data.
            if 'estagi' in norm(h[2] + ' ' + h[3]) and a < b:
                if intervalos and intervalos[-1][1] == a:
                    intervalos[-1] = (intervalos[-1][0], b)
                else:
                    intervalos.append((a, b))
        estimado = False
        if not hs and 'estagi' in norm(func + ' ' + cargo):
            intervalos, estimado = [(inicio, fim)], True
        for a, b in intervalos:
            base = 'Duração do vínculo cadastrado como estágio (estimativa, sem histórico que comprove toda a permanência)' if estimado else 'Tempo de estágio registrado no histórico'
            frases.append(f"{r['RA_NOME']} — {base}: {_duracao(a, b)}, de {a:%d/%m/%Y} a {b:%d/%m/%Y}, "
                          f"matrícula {r['RA_MAT']}, filial {filial}.")
    return ' '.join(frases) or 'Não há um intervalo de estágio comprovável nas funções/descrições disponíveis; as matrículas e mudanças encontradas estão na tabela.'
