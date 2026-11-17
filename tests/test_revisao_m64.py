import sqlite3
import pytest
from django.http import QueryDict
from iativa.agent import entregas,diagnostico,planejamento,aprendizado,comparacao,estrutura
from iativa.agent.agente import Saida
from iativa.agent.decisao import validar
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.query.servico import Resultado
from iativa.manutencao.models import Incidente
from .test_ciclo_agente import base,_d
from .test_diagnostico import faturamento

pytestmark=pytest.mark.django_db


def test_lista_com_campos_nao_e_cumprida_por_contagem():
    t='Quais funcionários estão ativos, por filial? Mostre nome completo, função e data de admissão.'
    a=entregas.planejar(t,{'entregas':[{'descricao':'Contar funcionários','tipo':'calculo','metrica':'contagem'}]})
    r=Resultado(True,'',colunas=['Filial','Quantidade'],tipos=['texto','inteiro'],linhas=[['01','4']])
    assert planejamento.conferir(a,validar(_d(),[],[]),Saida('',resultado=r))
    assert 'funcao' in a['entregas'][0]['campos_pedidos']
    assert not entregas.somente_lista('Mostre o faturamento por filial')
    assert not entregas.somente_lista('Mostre o saldo por cliente')
    assert not entregas.somente_lista('Mostre o histórico de função dos funcionários')


def test_financeiro_entrega_clientes_vencimentos_saldos_sem_agrupar(base):
    b=base;c=conversas.nova_individual(b['ana'])
    p=FakeProvider([_d(acao='preparar_consulta',objetivo='titulos-a-vencer',entrega='calculo',parametros=[
        {'codigo':'filial-analise','valor':'TODAS'},{'codigo':'periodo-analise','valor':'01/01/2026..31/12/2026'},
        {'codigo':'tratamento-baixa-parcial','valor':'considerar-saldo'},{'codigo':'agregacao','valor':'por filial'}])])
    m,r=conversas.enviar(b['ana'],c,'Quais títulos vencem este ano? Mostre cliente, vencimento e saldo por filial.',p)
    assert r is not None and r.ok,m.texto
    assert not entregas.faltam(r.colunas,['cliente','vencimento','saldo','filial'])
    assert p.chamadas==1 and not Incidente.objects.exists()


@pytest.mark.parametrize('acao',['preparar_consulta','historico_cargo','criar_consulta'])
def test_rh_preserva_lista_de_ativos_e_datas(base,acao):
    b=base;q=QueryDict(mutable=True);q.update({'nome':'Ativos sintéticos','campo_data':'RA_ADMISSA'})
    q.setlist('colunas',['RA_MAT','RA_NOME','RA_ADMISSA']);q.setlist('setores',[str(b['cob'].pk)])
    cm=aprendizado.aprovar(b['admin'],aprendizado.salvar_proposta(b['admin'],'SRA',q))
    cm.spec['filtros_fixos']=[{'campo':'RA_SITFOLH','op':'<>','valor':'D'}];cm.save(update_fields=['spec'])
    extras={'consulta':{'tabela':'SRA','nome':'Desnecessária','colunas':['RA_NOME','RA_ADMISSA']}} if acao=='criar_consulta' else {}
    p=FakeProvider([_d(acao=acao,objetivo=cm.codigo,entrega='calculo',**extras,parametros=[
        {'codigo':'filial-analise','valor':'TODAS'},{'codigo':'periodo-analise','valor':'01/01/1900..31/12/2026'},
        {'codigo':'agregacao','valor':'contagem:RA_MAT por filial'}])])
    m,r=conversas.enviar(b['ana'],conversas.nova_individual(b['ana']),
        'Quais funcionários estão ativos por filial? Mostre nome completo e data de admissão.',p)
    assert r is not None and r.ok and not entregas.faltam(r.colunas,['nome','admissao']),m.texto
    assert p.chamadas==1 and not Incidente.objects.exists()


def test_comparacao_nao_e_investigacao():
    t='Compare o faturamento de julho e agosto de 2026 por filial com diferença e variação percentual'
    assert not diagnostico.solicitado(t)
    assert [r for _,_,r in comparacao.periodos(t)]==['07/2026','08/2026']
    assert diagnostico.solicitado('Investigue por que o faturamento de agosto não bate')


def test_lista_de_uma_filial_identifica_a_filial_sem_agrupar():
    r=Resultado(True,'',colunas=['Nome'],tipos=['texto'],linhas=[['Pessoa sintética']],escopo={'filiais':['01']})
    entregas.completar('Quais funcionários ativos por filial? Mostre nome completo.',r)
    assert r.colunas==['Filial','Nome'] and r.linhas==[['01','Pessoa sintética']]


@pytest.mark.parametrize('primeiro,valor_base,diferenca,percentual',[
    ('agosto','R$ 1.000,00','R$ 7.431,27','743,13%'),
    ('julho','R$ 0,00','R$ 8.431,27','Indefinido (base zero)')])
def test_compara_dois_meses_mesma_consulta_com_diferenca_e_percentual(faturamento,primeiro,valor_base,diferenca,percentual):
    b=faturamento;q=QueryDict(mutable=True);q.update({'nome':'Faturamento de itens sintético','campo_data':'D2_EMISSAO'})
    q.setlist('colunas',['D2_EMISSAO','D2_TOTAL']);q.setlist('setores',[str(b['cob'].pk)])
    cm=aprendizado.aprovar(b['admin'],aprendizado.salvar_proposta(b['admin'],'SD2',q))
    with sqlite3.connect(b['erp']) as conn:
        conn.execute("INSERT INTO SD2010 VALUES ('01','20260808','501','5102',1000,1,' ')")
    p=FakeProvider([_d(acao='preparar_consulta',objetivo=cm.codigo,entrega='investigacao',
        parametros=[{'codigo':'filial-analise','valor':'01'}])])
    m,r=conversas.enviar(b['ana'],conversas.nova_individual(b['ana']),
        f'Compare faturamento de {primeiro} e setembro de 2026 por filial com diferença e percentual',p)
    assert r is not None and r.ok and r.medida_calculada=='comparacao',m.texto
    assert r.colunas==['Filial','08/2026' if primeiro=='agosto' else '07/2026','09/2026','Diferença','Variação percentual']
    assert r.linhas[0][1:]==[valor_base,'R$ 8.431,27',diferenca,percentual]
    assert p.chamadas==1 and not Incidente.objects.exists()


def test_filiais_somente_autorizadas_na_plataforma(base):
    b=base;m,r=conversas.enviar(b['ana'],conversas.nova_individual(b['ana']),
        'TI: quais filiais da empresa estão cadastradas? Mostre o código e o nome de cada filial.',FakeProvider([]))
    assert r is not None and r.ok and {l[0] for l in r.linhas}=={'01','02'}
    assert 'cadastro de acessos' in r.premissas[0]
    assert not estrutura.solicitado('Quais filiais cadastradas têm clientes inadimplentes?')
    from iativa.chat.resultados import empacotar, restaurar
    snapshot=empacotar(r)
    assert restaurar(b['ana'],snapshot) is not None
    assert restaurar(b['caio'],snapshot) is None  # outro setor, sem acesso ao escopo salvo
    snapshot['escopo'].pop('cadastro_filiais')
    assert restaurar(b['ana'],snapshot) is None  # não libera resultados ERP sem campos
