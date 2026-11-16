"""Regressões: admissões com contagem+detalhes e sem falsa falta de capacidade."""
from datetime import date

import pytest
from django.utils import timezone

from iativa.agent import admissoes, calculos, planejamento
from iativa.agent.agente import Saida, _juntar
from iativa.agent.decisao import validar, esquema
from iativa.agent.models import Objetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.resultados import empacotar, restaurar
from iativa.manutencao.models import Incidente
from iativa.query.servico import Resultado, Totais
from .test_tarefas import base, _d  # noqa: F401
from .test_evolucao_m60 import analise

pytestmark = pytest.mark.django_db
PEDIDO = 'Quantos funcionários foram admitidos esse mês?'


@pytest.fixture
def adm(base, monkeypatch):
    monkeypatch.setattr(timezone, 'localdate', lambda *a, **kw: date(2026, 10, 16))
    cm=base['cm'];cm.spec['campo_data']='RA_ADMISSA';cm.nome='Admissões sintéticas'
    cm.save(update_fields=['spec','nome'])
    return base


def provedor(b, texto=PEDIDO, **kw):
    a=analise(b,entregas=[{'descricao':'Contar funcionários admitidos no mês','tipo':'calculo','metrica':'soma'}])
    return FakeProvider([_d(acao='preparar_consulta',objetivo=b['cm'].codigo,entrega='calculo',atende=[0],
        parametros=[{'codigo':'filial-analise','valor':'TODAS'},
                    {'codigo':'periodo-analise','valor':'01/11/2026..30/11/2026'},
                    {'codigo':'agregacao','valor':'por filial;contagem:RA_MAT'}],**kw),
        _d(acao='falta_capacidade',capacidade='Contagem de admissões')],analises=[a])


def test_contagem_e_lista_concluem_em_uma_consulta_sem_manutencao(adm):
    b=adm;c=conversas.nova_individual(b['u']);p=provedor(b)
    m,r=conversas.enviar(b['u'],c,PEDIDO,p)
    assert p.chamadas==1 and r is not None and r.ok,m.texto
    assert admissoes.tem_detalhes(r) and r.medida_calculada=='contagem'
    assert len(r.linhas)>0 and 'admissão' in r.mensagem
    assert 'Soma:' not in m.texto and m.texto.count('Consulta executada')<=1
    assert not Incidente.objects.exists()
    assert not Objetivo.objects.exclude(situacao='cumprido').exists()
    assert not m.ficha.get('corrigindo')
    assert 'Nome' in r.colunas
    # A lista e o total são reabertos, sem chamar o ERP novamente.
    salvo=restaurar(b['u'],m.resultado)
    assert salvo is not None and salvo.linhas==r.linhas and salvo.curta==r.curta


def test_apenas_numero_respeita_formato_direto(adm):
    b=adm;c=conversas.nova_individual(b['u']);texto=PEDIDO+' Apenas o número.'
    p=provedor(b,formato='direto')
    m,r=conversas.enviar(b['u'],c,texto,p)
    assert p.chamadas==1 and m.ficha['formato']=='direto' and r.curta.isdigit()
    assert not Objetivo.objects.exclude(situacao='cumprido').exists()


def test_planejamento_corrige_soma_e_exige_nomes_datas(adm):
    a=admissoes.planejar(PEDIDO,analise(adm,entregas=[{'descricao':'Contagem de admitidos','tipo':'calculo','metrica':'soma'}]))
    assert a['entregas'][0]['metrica']=='contagem' and a['entregas'][1]['detalhe_admissoes']
    r=Resultado(True,'ok',colunas=['Filial','Quantidade'],tipos=['texto','inteiro'],linhas=[['01','3']],
                medida_calculada='contagem')
    d=validar(_d(acao='conversar'),[],[])
    assert planejamento.conferir({'entregas':[a['entregas'][1]]},d,Saida('',resultado=r))


def test_contagem_e_tipo_do_schema_e_nao_soma():
    s=esquema([],[])
    assert 'contagem' in s['properties']['analise']['properties']['entregas']['items']['properties']['metrica']['enum']
    assert 'contagem' in s['properties']['calculo']['properties']['operacao']['enum']
    assert validar(_d(acao='calcular_resultado',calculo={'operacao':'contagem'}),[],[]).calculo['operacao']=='contagem'


def test_total_completo_sobrevive_snapshot_truncado(adm):
    b=adm;r=Resultado(True,'ok',colunas=['Nome'],tipos=['texto'],linhas=[['Exemplo']],truncado=True,
        totais=Totais(1234),escopo={'setor':b['cob'].pk,'filiais':['01'],'campos':{'SRA':['RA_NOME']}})
    salvo=restaurar(b['u'],empacotar(r))
    assert salvo is not None and calculos.contar(salvo)==1234
    c=conversas.nova_individual(b['u'])
    calculado=calculos.executar(b['u'],c,{'operacao':'contagem'},r)
    assert calculado.curta=='1234'


@pytest.mark.parametrize('r',[
    Resultado(True,'',colunas=['Nome'],linhas=[['Exemplo']],truncado=True),
    Resultado(True,'',colunas=['Filial','Quantidade'],linhas=[['01','3']]),
    Resultado(False,'',colunas=['Nome'],linhas=[['Exemplo']]),
])
def test_nao_conta_amostra_cortada_ou_grupos_como_pessoas(r):
    assert calculos.contar(r) is None


def test_zero_e_contagem_valida():
    assert calculos.contar(Resultado(True,'',colunas=['Nome'],linhas=[],totais=Totais(0)))==0


def test_mes_nao_muda_entre_tentativas(adm):
    from iativa.agent.contratos import objetivos_para
    d=validar(_d(acao='preparar_consulta',objetivo=adm['cm'].codigo,parametros=[
        {'codigo':'periodo-analise','valor':'01/11/2026..30/11/2026'}]),[adm['cm'].codigo],['periodo-analise'])
    ajustada=admissoes.orientar(adm['u'],PEDIDO,d,objetivos_para(adm['u']))
    assert dict(ajustada.parametros)['periodo-analise']=='01/10/2026..16/10/2026'


def test_tentativa_repetida_nao_e_defeito_de_codigo_em_contagem():
    from iativa.agent.objetivos import Acompanhamento,encaminhar_manutencao
    a=Acompanhamento(PEDIDO,exig='calculo',capacidade='Contar admissões')
    a.anotar('preparar_consulta','acao_repetida')
    assert encaminhar_manutencao(a,'acao_repetida','SRA') is None
    assert not Incidente.objects.exists()


def test_juntar_nao_duplica_paragrafos_identicos():
    a=Saida('Consulta executada.\n\nEtapa concluída.')
    b=Saida('Consulta executada.\n\nResultado final.')
    r=_juntar(a,b)
    assert r.texto.count('Consulta executada.')==1 and 'Resultado final.' in r.texto


def test_continuacao_corrige_contrato_antigo_de_soma(adm):
    from iativa.agent import agente
    from iativa.agent.objetivos import Acompanhamento
    b=adm;c=conversas.nova_individual(b['u']);p=provedor(b)
    ac=Acompanhamento(PEDIDO,exig='calculo',contrato=[
        {'descricao':'Contar funcionários admitidos no mês','tipo':'calculo','metrica':'soma'}])
    s=agente.conversar(b['u'],c,PEDIDO,p,acomp=ac)
    assert s.resultado is not None and s.resultado.ok and not ac.pendentes()
    assert ac.contrato[0]['metrica']=='contagem' and p.chamadas==1


def test_contratacao_de_equipamentos_nao_e_admissao():
    assert not admissoes.solicitado('Quantos containers foram contratados este mês?')
    assert admissoes.solicitado('Quantos funcionários foram contratados este mês?')


def test_contar_admissoes_nao_cumpre_contagem_de_demitidos(adm):
    b=adm;c=conversas.nova_individual(b['u'])
    a=analise(b,entregas=[
        {'descricao':'Contar admissões','tipo':'calculo','metrica':'contagem'},
        {'descricao':'Contar demissões','tipo':'calculo','metrica':'contagem'}])
    p=FakeProvider([
        _d(acao='preparar_consulta',objetivo=b['cm'].codigo,entrega='calculo',atende=[0],parametros=[
            {'codigo':'filial-analise','valor':'TODAS'},
            {'codigo':'periodo-analise','valor':'01/10/2026..16/10/2026'}]),
        _d(resposta='De qual período você deseja consultar as demissões?')],analises=[a])
    conversas.enviar(b['u'],c,'Quantos funcionários foram admitidos e quantos foram demitidos esse mês?',p)
    o=Objetivo.objects.get()
    assert p.chamadas==2 and o.situacao=='aguardando_usuario'
    assert 0 in o.contrato['cumpridas'] and 1 not in o.contrato['cumpridas']


@pytest.mark.django_db
def test_m61_pedidos_diferentes_viram_incidentes_diferentes():
    from iativa.agent import objetivos as coord

    a = coord.Acompanhamento("quantos dias faltam para o natal?", exig=coord.CALCULO)
    a.anotar("calcular_resultado", "acao_repetida")
    b = coord.Acompanhamento("qual a data daqui a 30 dias úteis?", exig=coord.CALCULO)
    b.anotar("calcular_resultado", "acao_repetida")
    assert coord.encaminhar_manutencao(a, "acao_repetida").pk != coord.encaminhar_manutencao(b, "acao_repetida").pk


@pytest.mark.django_db
def test_m61_incidente_bloqueado_nao_deixa_pedido_esperando():
    from iativa.manutencao.incidentes import elegivel
    from iativa.manutencao.models import Categoria, Incidente

    inc = Incidente.objects.create(assinatura="x" * 64, categoria=Categoria.CAPACIDADE, codigo="c", componente="a",
                                   operacao="o", situacao="bloqueado_admin", condicoes={"trilha": ["a:b:c"]})
    assert not elegivel(inc)


def test_m62_pergunta_sobre_a_lista_nao_vira_historico_de_uma_pessoa():
    from iativa.agent.agente import _PLURAL_DA_LISTA
    assert _PLURAL_DA_LISTA.search("qual a função deles?")
    assert _PLURAL_DA_LISTA.search("e o cargo de cada um?")
    assert not _PLURAL_DA_LISTA.search("qual o histórico de cargos do joão?")
