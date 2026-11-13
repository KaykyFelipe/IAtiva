"""Cálculos determinísticos sobre resultados autorizados; nunca envia linhas ao modelo."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.core.exceptions import ValidationError

from iativa.accounts.authz import PermissaoNegada
from iativa.chat.resultados import restaurar, empacotar
from iativa.query.servico import Resultado

OPERACOES = ('dias_uteis', 'dias_corridos', 'meses', 'soma', 'media', 'diferenca', 'percentual')


def dias_uteis(inicio, fim):
    """Segunda a sexta, intervalo [início, fim), como a diferença de datas em dias corridos."""
    if fim < inicio:
        raise ValidationError('A data final deve ser igual ou posterior à inicial.')
    semanas, resto = divmod((fim - inicio).days, 7)
    return semanas * 5 + sum((inicio.weekday() + i) % 7 < 5 for i in range(resto))


def _origem(user, conversa, mensagem=0):
    from iativa.chat.conversas import pode_ver
    if not pode_ver(user, conversa):
        raise PermissaoNegada('Conversa indisponível.')
    qs = conversa.mensagens.filter(papel='ia').exclude(resultado={}).exclude(resultado=None)
    m = qs.filter(pk=mensagem).first() if mensagem else qs.order_by('-id').first()
    if m is None:
        raise ValidationError('Primeiro execute a consulta que traz os dados necessários para o cálculo.')
    res = restaurar(user, m.resultado)
    if res is None:
        raise PermissaoNegada('Você não tem mais acesso ao resultado usado neste cálculo.')
    if getattr(res, 'consolidado', False):
        raise ValidationError('Escolha uma consulta simples para calcular; não misture blocos de setores diferentes.')
    return m, res


def contexto(user, conversa):
    try:
        m, res = _origem(user, conversa)
    except (ValidationError, PermissaoNegada):
        return None
    return metadados(res, m.pk)


def metadados(res, mensagem=0):
    return {'mensagem': mensagem, 'colunas': res.colunas, 'tipos': res.tipos, 'registros': len(res.linhas),
            'periodos_estagio': bool(res.periodos), 'operacoes': list(OPERACOES),
            'instrucao': 'Use calcular_resultado para operar sobre esses dados. As linhas ficam no backend. '
                         'Para novos dados, consulte ou descubra a tabela com aprender_tabela/criar_consulta.'}


def _data(valor):
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(str(valor).strip(), fmt).date()
        except ValueError:
            pass
    raise ValidationError('Data ausente ou inválida no resultado; consulte uma fonte com as duas datas.')


def _numero(valor):
    t = re.sub(r'R\$|\s', '', str(valor))
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        n = Decimal(t)
        if not n.is_finite():
            raise InvalidOperation
        return n
    except InvalidOperation:
        raise ValidationError('O campo escolhido não contém um número válido.') from None


def executar(user, conversa, plano, resultado_atual=None):
    if resultado_atual is not None and not plano.get('mensagem'):
        from iativa.chat.conversas import pode_ver
        dados = empacotar(resultado_atual)
        res = restaurar(user, dados) if pode_ver(user, conversa) else None
        if res is None:
            raise PermissaoNegada('O resultado atual não está autorizado para o cálculo.')
        m = SimpleNamespace(pk=0, resultado=dados)
        if getattr(res, 'consolidado', False):
            raise ValidationError('Não misture blocos de setores diferentes no cálculo.')
    else:
        m, res = _origem(user, conversa, plano.get('mensagem', 0))
    if res.truncado:
        raise ValidationError('O resultado anterior está cortado. Consulte todos os registros necessários antes de calcular.')
    op = plano['operacao']
    linhas = []
    premissas = list(res.premissas) + ['Cálculo sobre a consulta executada nesta mensagem.' if not m.pk else
                                      'Cálculo sobre o resultado salvo da consulta anterior; os dados não foram atualizados.']
    if op in ('dias_uteis', 'dias_corridos', 'meses'):
        from .historico_funcional import _duracao
        if res.periodos and not plano.get('campo'):
            pares = [(p['rotulo'], _data(p['inicio']), _data(p['fim']), p.get('estimativa', False)) for p in res.periodos]
        else:
            try:
                a, b = res.colunas.index(plano.get('campo')), res.colunas.index(plano.get('campo_final'))
            except ValueError:
                raise ValidationError('Escolha as colunas inicial e final dentre as colunas do resultado anterior.') from None
            pares = [(f'Registro {i}', _data(l[a]), _data(l[b]), False) for i, l in enumerate(res.linhas, 1)]
        for rotulo, inicio, fim, estimado in pares:
            if fim < inicio:
                raise ValidationError('Há uma data final anterior à inicial; confira os critérios.')
            valor = dias_uteis(inicio, fim) if op == 'dias_uteis' else (fim - inicio).days if op == 'dias_corridos' else _duracao(inicio, fim)
            linhas.append([rotulo, f'{inicio:%d/%m/%Y}', f'{fim:%d/%m/%Y}', str(valor), 'Estimativa pelo cadastro' if estimado else 'Período registrado'])
        unidade = {'dias_uteis': 'dias úteis', 'dias_corridos': 'dias corridos', 'meses': 'meses de calendário'}[op]
        texto = ' '.join(f'{l[0]}: {l[3]} {unidade if op != "meses" else ""}'
                         + (' (estimativa pelo cadastro).' if l[4].startswith('Estimativa') else '.') for l in linhas)
        if op == 'dias_uteis':
            premissas.append('Dias úteis: segunda a sexta; sem descontar feriados. Inclui a data inicial e exclui a final.')
            texto += ' Contagem de segunda a sexta, sem descontar feriados; data inicial incluída e final excluída.'
        colunas = ['Período', 'Início', 'Fim', unidade.capitalize(), 'Base do cálculo']
        tipos = ['texto', 'data', 'data', 'texto' if op == 'meses' else 'inteiro', 'texto']
    else:
        if 'truncado' not in m.resultado:
            raise ValidationError('Este resultado antigo não tem informação de cobertura. Reexecute a consulta antes de somar.')
        if len(m.resultado.get('moedas', [])) > 1:
            raise ValidationError('Não misture moedas diferentes neste cálculo; consulte cada moeda separadamente.')
        try:
            a = res.colunas.index(plano.get('campo'))
            b = res.colunas.index(plano.get('campo_final')) if op in ('diferenca', 'percentual') else None
        except ValueError:
            raise ValidationError('Escolha campos dentre as colunas do resultado anterior.') from None
        if any(res.tipos[i] not in ('dinheiro', 'inteiro', 'valor', 'numero') for i in [a] + ([] if b is None else [b])):
            raise ValidationError('Escolha medidas numéricas, não códigos ou nomes do resultado.')
        valores = [_numero(l[a]) for l in res.linhas]
        if op in ('soma', 'media'):
            valor = sum(valores, Decimal(0)) / (len(valores) if op == 'media' and valores else 1)
            linhas = [[op.capitalize(), format(valor, 'f') if valores or op == 'soma' else 'Sem valores']]
        else:
            for i, l in enumerate(res.linhas, 1):
                x, y = _numero(l[a]), _numero(l[b])
                valor = x - y if op == 'diferenca' else x / y * 100 if y else None
                linhas.append([f'Registro {i}', format(valor, 'f') if valor is not None else 'Indefinido (base zero)'])
        colunas, tipos = ['Cálculo', 'Resultado'], ['texto', 'numero']
        texto = ' '.join(f'{l[0]}: {l[1]}{"%" if op == "percentual" and not l[1].startswith("Indefinido") else ""}.' for l in linhas)
    return Resultado(True, texto or 'Nenhum registro para calcular.', colunas=colunas, tipos=tipos, linhas=linhas,
                     premissas=premissas, escopo=res.escopo, medida_calculada=op, periodos=res.periodos)
