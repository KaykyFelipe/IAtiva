"""Cálculos posteriores à consulta: continuar com a unidade e o intervalo certos."""
from datetime import date
import json
import sqlite3

import pytest
from django.core.exceptions import ValidationError

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import AccessPolicy
from iativa.agent import agente, calculos, historico_funcional as hf, objetivos as coord
from iativa.agent.decisao import DecisaoInvalida, validar
from iativa.agent.models import Objetivo, SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from iativa.chat.resultados import guardar
from iativa.query.servico import Resultado
from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def estagio(funcional, erp_sintetico):
    with sqlite3.connect(erp_sintetico) as con:
        con.execute("UPDATE SRA010 SET RA_ADMISSA='20260101', RA_DEMISSA='20261001' WHERE RA_MAT='000102' AND RA_FILIAL='01'")
        con.execute('DELETE FROM SR7010')
        con.execute("INSERT INTO SR7010 VALUES ('01','000102','20260101','01','00004','',' ')")
    c = conversas.nova_individual(funcional['u'])
    msg, _ = conversas.enviar(funcional['u'], c, 'quanto tempo de estágio do BRUNO SINTETICO LIMA?',
        FakeProvider([_d(acao='historico_cargo', alvo='BRUNO SINTETICO LIMA')]))
    return funcional, c, msg


@pytest.mark.parametrize('inicio,fim,n', [(date(2026, 2, 2), date(2026, 2, 9), 5),
    (date(2026, 2, 7), date(2026, 2, 9), 0), (date(2026, 2, 9), date(2026, 2, 9), 0),
    (date(2026, 2, 6), date(2026, 2, 10), 2)])
def test_dias_uteis_de_calendario(inicio, fim, n):
    assert calculos.dias_uteis(inicio, fim) == n


def test_e_dias_uteis_nao_para_na_consulta_de_admissao(estagio):
    b, c, _ = estagio
    msg, res = conversas.enviar(b['u'], c, 'e dias úteis?', FakeProvider([
        _d(acao='preparar_consulta', objetivo=b['cm'].codigo, resposta='Vou contar e trago depois.',
           parametros=[{'codigo': 'texto-busca', 'valor': 'BRUNO SINTETICO LIMA'}])]))
    assert res and res.medida_calculada == 'dias_uteis', msg.texto
    assert '195 dias úteis' in res.mensagem and 'sem descontar feriados' in res.mensagem
    assert len(res.periodos) == 1 and '000102' in res.periodos[0]['rotulo']
    assert res.periodos[0]['fim'] == '2026-10-01'  # não conta o vínculo novo de analista até hoje
    assert msg.resultado['medida_calculada'] == 'dias_uteis'
    assert '<table>' not in msg.texto and res.linhas


def test_continuacao_recupera_m44_sem_periodos_salvos(estagio):
    b, c, msg = estagio
    msg.ficha.pop('tempo_estagio', None)
    msg.resultado.pop('periodos', None)
    msg.save(update_fields=['ficha', 'resultado'])
    # Emula uma resposta antiga que já perdeu o contexto e mostrou só a SRA (imagem do usuário).
    sol = c.solicitacoes.first()
    from iativa.chat.servicos import criar_solicitacao
    s = sol or criar_solicitacao(b['u'], 'aprendido', b['cm'].codigo, conversa=c)
    Mensagem.objects.create(conversa=c, papel='usuario', autor=b['u'], texto='e dias uteis?')
    Mensagem.objects.create(conversa=c, papel='ia', texto='Consultei as admissões.', solicitacao=s)
    _, res = conversas.enviar(b['u'], c, 'e dias úteis?', FakeProvider([_d(resposta='Vou consultar.')]))
    assert res and '195 dias úteis' in res.mensagem


def test_ferramenta_calcula_sobre_resultado_persistido(estagio):
    b, c, origem = estagio
    msg, res = conversas.enviar(b['u'], c, 'e dias úteis?', FakeProvider([
        _d(acao='calcular_resultado', calculo={'operacao': 'dias_uteis', 'mensagem': origem.pk})]))
    assert res and '195 dias úteis' in res.mensagem
    assert res.colunas[3] == 'Dias úteis' and len(res.linhas) == 1
    assert msg.resultado['linhas']


def test_continuacao_preserva_pessoa_e_filial_sem_aceitar_desvio_do_modelo(estagio):
    b, _, _ = estagio
    c = conversas.nova_individual(b['u'])
    conversas.enviar(b['u'], c, 'tempo de estágio do BRUNO SINTETICO LIMA na filial 01', FakeProvider([
        _d(acao='historico_cargo', alvo='BRUNO SINTETICO LIMA',
           parametros=[{'codigo':'filial-analise','valor':'01'}])]))
    _, res = conversas.enviar(b['u'], c, 'e dias úteis?', FakeProvider([
        _d(acao='preparar_consulta', objetivo=b['cm'].codigo, parametros=[
            {'codigo':'filial-analise','valor':'TODAS'}, {'codigo':'texto-busca','valor':'MARIA'}])]))
    assert res and '195 dias úteis' in res.mensagem
    assert res.escopo['filiais'] == ['01']
    assert all('BRUNO' in p['rotulo'] for p in res.periodos)


def test_resultado_para_modelo_so_contem_metadados(estagio):
    b, c, _ = estagio
    ctx = calculos.contexto(b['u'], c)
    assert ctx['periodos_estagio'] is True
    assert 'linhas' not in ctx and 'periodos' not in ctx
    assert '2026-10-01' not in json.dumps(ctx) and '000102' not in json.dumps(ctx)


def test_revogacao_de_acesso_impede_calculo(estagio):
    b, c, origem = estagio
    AccessPolicy.objects.filter(user=b['u']).update(pode_consultar=False)
    with pytest.raises(PermissaoNegada):
        calculos.executar(b['u'], c, {'operacao': 'dias_uteis', 'mensagem': origem.pk})


def test_nao_usa_mensagem_de_outra_conversa(estagio):
    b, c, origem = estagio
    outra = conversas.nova_individual(b['u'])
    with pytest.raises(ValidationError):
        calculos.executar(b['u'], outra, {'operacao': 'dias_uteis', 'mensagem': origem.pk})


def test_contagem_de_registros_nao_comprova_dias_uteis():
    from types import SimpleNamespace
    res = Resultado(True, '2 registros', medida_calculada='soma')
    saida = agente.Saida('', {'tipo': 'executada'}, resultado=res)
    assert coord.evidencia(SimpleNamespace(parametros=(('agregacao', 'contagem:*'),)), saida,
                           'e dias úteis?') == coord.CONSULTA


def test_solicita_calculo_mas_so_lista_dados_nao_fica_cumprido(estagio):
    b, c, _ = estagio
    # Uma consulta genérica sem contexto de estágio não pode provar dias úteis só ao listar datas.
    Mensagem.objects.create(conversa=c, papel='ia', texto='Outro assunto.')
    msg, _ = conversas.enviar(b['u'], c, 'calcule os dias úteis destes registros', FakeProvider([
        _d(acao='preparar_consulta', objetivo=b['cm'].codigo, parametros=[
            {'codigo': 'filial-analise', 'valor': '01'},
            {'codigo': 'periodo-analise', 'valor': '01/01/2026..31/12/2026'}]),
        _d(resposta='Já trouxe os dados.')]))
    assert not Objetivo.objects.filter(mensagem__texto='calcule os dias úteis destes registros',
                                       situacao=SituacaoObjetivo.CUMPRIDO).exists()


def test_calculo_do_resultado_transitorio_no_mesmo_ciclo(estagio, monkeypatch):
    b, c, _ = estagio
    original = agente._agir
    escopo = {'setor': b['cob'].pk, 'filiais': ['01'], 'campos': {'SRA': ['RA_ADMISSA', 'RA_DEMISSA']}}
    def agir(*a, **kw):
        if a[2].acao == 'preparar_consulta':
            return agente.Saida('Datas consultadas.', {}, resultado=Resultado(True, 'Datas',
                colunas=['Início', 'Fim'], tipos=['data','data'], linhas=[['02/02/2026','09/02/2026']], escopo=escopo))
        return original(*a, **kw)
    monkeypatch.setattr(agente, '_agir', agir)
    Mensagem.objects.create(conversa=c, papel='ia', texto='Outro assunto.')
    msg, res = conversas.enviar(b['u'], c, 'calcule os dias úteis', FakeProvider([
        _d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
        _d(acao='calcular_resultado', calculo={'operacao':'dias_uteis','campo':'Início','campo_final':'Fim'})]))
    assert res and res.linhas[0][3] == '5', msg.texto


def test_supervisor_de_estagio_nao_e_classificado_como_estagiario():
    assert not hf._e_estagio('SUPERVISOR DE ESTÁGIO', '')
    assert hf._e_estagio('ESTAGIÁRIO DE TI', '')


def _numeros(b, c, truncado=False, moedas=()):
    r = Resultado(True, 'Valores sintéticos', colunas=['Valor', 'Base'], tipos=['numero','numero'],
        linhas=[['25,00','100,00'],['50,00','0,00']], truncado=truncado,
        escopo={'setor':b['cob'].pk,'filiais':['01'],'campos':{'SRA':['RA_MAT']}})
    m = Mensagem.objects.create(conversa=c, papel='ia', texto='Consulta concluída.')
    guardar(m, r)
    m.resultado['moedas'] = list(moedas)
    m.save(update_fields=['resultado'])
    return m


def test_ferramenta_numerica_soma_e_percentual_sem_dividir_por_zero(estagio):
    b, c, _ = estagio
    m = _numeros(b, c)
    soma = calculos.executar(b['u'], c, {'operacao':'soma','mensagem':m.pk,'campo':'Valor'})
    assert soma.linhas[0][1] == '75.00'
    pct = calculos.executar(b['u'], c, {'operacao':'percentual','mensagem':m.pk,'campo':'Valor','campo_final':'Base'})
    assert pct.linhas[0][1] == '25.00' and pct.linhas[1][1] == 'Indefinido (base zero)'


@pytest.mark.parametrize('truncado,moedas', [(True,()), (False,('1','2'))])
@pytest.mark.parametrize('op', ['soma', 'media', 'diferenca', 'percentual'])
def test_nao_calcula_parte_exibida_nem_moedas_distintas(estagio, truncado, moedas, op):
    b, c, _ = estagio
    m = _numeros(b, c, truncado, moedas)
    with pytest.raises(ValidationError):
        calculos.executar(b['u'], c, {'operacao':op,'mensagem':m.pk,'campo':'Valor','campo_final':'Base'})


def test_sem_data_final_nao_assume_hoje(estagio):
    b, c, _ = estagio
    m = _numeros(b, c)
    m.resultado.update(colunas=['Início','Fim'], tipos=['data','data'], linhas=[['01/01/2026','']])
    m.save(update_fields=['resultado'])
    with pytest.raises(ValidationError, match='Data ausente'):
        calculos.executar(b['u'], c, {'operacao':'dias_uteis','mensagem':m.pk,'campo':'Início','campo_final':'Fim'})


def test_campos_opcionais_nulos_do_schema_estrito_sao_aceitos():
    d = validar(_d(acao='calcular_resultado', calculo={'operacao':'dias_uteis', 'mensagem':None,
                                                    'campo':None, 'campo_final':None}), [], [])
    assert d.calculo == {'operacao':'dias_uteis','mensagem':0,'campo':'','campo_final':''}


@pytest.mark.parametrize('calculo', [{}, {'operacao':'eval'}, {'operacao':'soma','mensagem':True},
    {'operacao':'soma','sql':'SELECT 1'}, {'operacao':'dias_uteis','campo':500}])
def test_schema_recusa_calculo_invalido(calculo):
    with pytest.raises(DecisaoInvalida):
        validar(_d(acao='calcular_resultado', calculo=calculo), [], [])
