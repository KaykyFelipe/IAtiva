"""Divergência não termina na primeira soma; evidências sem backfitting ou persistência."""
import json
import sqlite3
import pytest
from django.http import QueryDict
from iativa.agent import aprendizado, diagnostico, prazo
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from iativa.metadata.models import RestricaoMetadado
from .test_ciclo_agente import base, _d, Espiao

pytestmark = pytest.mark.django_db

@pytest.fixture
def faturamento(base):
    with sqlite3.connect(base['erp']) as c:
        for alias, campos, modo in (
            ('SF2', [('F2_FILIAL','C',2),('F2_EMISSAO','D',8),('F2_TIPO','C',1),('F2_SERIE','C',3),
                     ('F2_STATUS','C',1),('F2_MOEDA','N',2),('F2_DUPL','C',6),
                     ('F2_VALBRUT','N',16),('F2_VALMERC','N',16),('F2_VALFAT','N',16)],'E'),
            ('SD2', [('D2_FILIAL','C',2),('D2_EMISSAO','D',8),('D2_TES','C',3),('D2_CF','C',4),
                     ('D2_TOTAL','N',16),('D2_MOEDA','N',2)],'E'),
            ('SF4', [('F4_FILIAL','C',2),('F4_CODIGO','C',3),('F4_DUPLIC','C',1)],'C')):
            fisica=alias+'010'
            c.execute(f"CREATE TABLE {fisica} ("+', '.join(f'{n} '+('NUMERIC' if tp=='N' else f'CHAR({tam})') for n,tp,tam in campos)+", D_E_L_E_T_ CHAR(1) DEFAULT ' ')")
            rec=c.execute('SELECT MAX(R_E_C_N_O_) FROM SX2010').fetchone()[0]+1
            c.execute('INSERT INTO SX2010 (X2_CHAVE,X2_ARQUIVO,X2_NOME,X2_MODO,X2_MODOUN,X2_MODOEMP,D_E_L_E_T_,R_E_C_N_O_) VALUES (?,?,?,?,?,?,?,?)', (alias,fisica,alias,modo,modo,modo,' ',rec))
            for n,tp,tam in campos:
                rec=c.execute('SELECT MAX(R_E_C_N_O_) FROM SX3010').fetchone()[0]+1
                c.execute('INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TIPO,X3_TAMANHO,X3_DECIMAL,X3_TITULO,X3_CONTEXT,D_E_L_E_T_,R_E_C_N_O_) VALUES (?,?,?,?,?,?,?,?,?)', (alias,n,tp,str(tam),'2' if tp=='N' else '0',n,'R',' ',rec))
        for fil,data,tipo,moeda,dupl,v,excl in [
            ('01','20260908','N',1,'000123',2431.27,' '),('01','20260908','D',1,'      ',6000,' '),
            ('01','20260908','N',2,'000124',77,' '),('02','20260908','N',1,'000125',100,' '),
            ('03','20260908','N',1,'000126',900000,' '),('01','20260908','N',1,'000127',800000,'*'),
            ('01','20250908','N',1,'000128',700000,' ')]:
            c.execute('INSERT INTO SF2010 VALUES (?,?,?,?,?,?,?,?,?,?,?)',(fil,data,tipo,'001','A',moeda,dupl,v,v,v,excl))
        for tes,cf,v in [('501','5102',1000),('501','5102',1431.27),('502','5915',6000)]:
            c.execute('INSERT INTO SD2010 VALUES (?,?,?,?,?,?,?)',('01','20260908',tes,cf,v,1,' '))
        for tes,dupl in [('501','S'),('502','N')]:
            c.execute('INSERT INTO SF4010 VALUES (?,?,?,?)',('  ',tes,dupl,' '))
    q=QueryDict(mutable=True)
    q.update({'nome':'Faturamento sintético','campo_data':'F2_EMISSAO'})
    q.setlist('colunas',['F2_VALBRUT','F2_VALMERC','F2_EMISSAO'])
    q.setlist('setores',[str(base['cob'].pk)])
    base['fat']=aprendizado.aprovar(base['admin'],aprendizado.salvar_proposta(base['admin'],'SF2',q))
    return base

def consulta(b):
    return _d(acao='preparar_consulta',objetivo=b['fat'].codigo,parametros=[
        {'codigo':'filial-analise','valor':'TODAS'},
        {'codigo':'periodo-analise','valor':'01/09/2026..30/09/2026'},
        {'codigo':'agregacao','valor':'soma:F2_VALBRUT,soma:F2_VALMERC,contagem:*'}])

def executar(b, texto='Investigue a diferença do faturamento de setembro, teste até dar o valor correto'):
    c=conversas.nova_individual(b['ana'])
    p=Espiao([consulta(b)])
    m,r=conversas.enviar(b['ana'],c,texto,p)
    return m,r,p,c

def test_soma_bem_sucedida_e_apenas_primeira_etapa(faturamento):
    m,r,p,c=executar(faturamento)
    assert m.ficha['tipo']=='diagnostico',m.texto
    titulos=[b.titulo for b in r.blocos]
    assert any('TES' in t for t in titulos) and any('CFOP' in t for t in titulos)
    assert any('F2_TIPO' in t for t in titulos) and any('Datas' in t for t in titulos)
    assert len(r.blocos)>=9 and len(p.entradas)==1
    assert 'pendente' in r.resumo and 'Não alterei critérios' in r.resumo
    dupl=next(b.resultado for b in r.blocos if b.titulo.startswith('Notas com título'))
    assert any('2.431,27' in str(l) for l in dupl.linhas)
    assert not any('900.000' in str(l) or '800.000' in str(l) or '700.000' in str(l) for b in r.blocos if b.resultado for l in b.resultado.linhas)
    assert '2.431,27' not in json.dumps(p.entradas,default=str)
    assert not Mensagem.objects.filter(texto__contains='2.431,27').exists()
    assert '2.431,27' not in json.dumps(m.ficha)
    tes=next(b.resultado for b in r.blocos if 'geração de duplicata' in b.titulo)
    assert any('S' in l and '2.431,27' in l for l in tes.linhas)
    assert any('N' in l and '6.000,00' in l for l in tes.linhas)

def test_moedas_separadas_sem_soma_entre_elas(faturamento):
    _,r,_,_=executar(faturamento)
    por_filial=r.blocos[1].resultado
    assert 'F2_MOEDA' in por_filial.colunas
    assert len([l for l in por_filial.linhas if l[0].startswith('01')])==2

def test_consulta_simples_nao_vira_investigacao(faturamento):
    m,r,_,_=executar(faturamento,'Some o faturamento de setembro em todas as filiais')
    assert not getattr(r,'consolidado',False)
    assert m.ficha['tipo']=='executada'

def test_fonte_restrita_e_hipotese_nao_testada(faturamento):
    RestricaoMetadado.objects.create(tabela='SD2',campo='')
    m,r,_,_=executar(faturamento)
    assert not r.completo
    assert any('não autorizada' in b.mensagem for b in r.blocos)
    assert not any('Itens SD2' in b.titulo for b in r.blocos)

def test_nao_aproxima_valor_esperado_nem_salva_regra(faturamento):
    from iativa.agent.models import NotaAgente
    antes=NotaAgente.objects.count()
    _,r,_,_=executar(faturamento,'Investigue o faturamento e teste até dar 1 milhão')
    _,r2,_,_=executar(faturamento,'Investigue o faturamento e teste até dar 500 milhões')
    assert [(b.titulo,getattr(b.resultado,'linhas',None)) for b in r.blocos]==[(b.titulo,getattr(b.resultado,'linhas',None)) for b in r2.blocos]
    assert NotaAgente.objects.count()==antes

def test_recusa_em_texto_reaproveita_consulta_e_investiga(faturamento):
    b=faturamento
    _,_,_,c=executar(b,'Some o faturamento de setembro')
    p=Espiao([_d(resposta='Não consigo investigar, informe os filtros do seu chefe.')])
    m,r=conversas.enviar(b['ana'],c,'Investigue por que o valor do faturamento não bate',p)
    assert m.ficha['tipo']=='diagnostico' and len(r.blocos)>3
    assert 'Não consigo' not in m.texto

def test_limite_de_tempo_nao_conclui_divergencia(faturamento, monkeypatch):
    def excedeu(self, plano):
        raise prazo.PrazoEsgotado()
    monkeypatch.setattr(diagnostico.Auditor,'ler',excedeu)
    m,r,_,_=executar(faturamento)
    assert not r.completo and 'incompletas' in r.resumo
    assert all(b.resultado is None for b in r.blocos[1:])

def test_cancelamento_interrompe_investigacao(faturamento, monkeypatch):
    from iativa.agent.agente import Interrompido
    original=diagnostico.Auditor.ler
    def parar(self, plano):
        self.interrompido=lambda: True
        return original(self, plano)
    monkeypatch.setattr(diagnostico.Auditor,'ler',parar)
    with pytest.raises(Interrompido):
        executar(faturamento)

def test_revogacao_descarta_dados_ja_lidos(faturamento, monkeypatch):
    original=diagnostico.Auditor.ler
    def negar(self, plano):
        from iativa.accounts.authz import PermissaoNegada
        raise PermissaoNegada('revogada')
    monkeypatch.setattr(diagnostico.Auditor,'ler',negar)
    m,r,_,_=executar(faturamento)
    assert r is None and m.ficha['tipo']=='bloqueada'

def test_moeda_restrita_nao_permite_soma_mista(faturamento):
    RestricaoMetadado.objects.create(tabela='SF2',campo='F2_MOEDA')
    m,r,_,_=executar(faturamento)
    assert r is None and m.ficha['tipo']=='bloqueada'

def test_filtros_do_cabecalho_e_moeda_preservados_nos_itens(faturamento):
    b=faturamento
    # Vincular itens a cabeçalhos, pois o dicionário real não oferece D2_MOEDA.
    with sqlite3.connect(b['erp']) as c:
        for alias,prefixo in [('SF2','F2'),('SD2','D2')]:
            for n,tam in [('DOC',6),('CLIENTE',6),('LOJA',2)]+([('SERIE',3)] if alias=='SD2' else []):
                nome=f'{prefixo}_{n}'
                c.execute(f'ALTER TABLE {alias}010 ADD COLUMN {nome} CHAR({tam})')
                rec=c.execute('SELECT MAX(R_E_C_N_O_) FROM SX3010').fetchone()[0]+1
                c.execute('INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TIPO,X3_TAMANHO,X3_DECIMAL,X3_TITULO,X3_CONTEXT,D_E_L_E_T_,R_E_C_N_O_) VALUES (?,?,?,?,?,?,?,?,?)',(alias,nome,'C',str(tam),'0',nome,'R',' ',rec))
            c.execute(f"UPDATE {alias}010 SET {prefixo}_CLIENTE='000001', {prefixo}_LOJA='01'")
        c.execute("UPDATE SF2010 SET F2_DOC=CASE WHEN F2_TIPO='D' THEN '000002' ELSE F2_DUPL END")
        c.execute("UPDATE SD2010 SET D2_DOC=CASE WHEN D2_TES='502' THEN '000002' ELSE '000123' END, D2_SERIE='001'")
        c.execute("UPDATE SX3010 SET D_E_L_E_T_='*' WHERE X3_CAMPO='D2_MOEDA'")
    # Aplica filtro aprovado no cabeçalho: não pode sumir na comparação de itens.
    cm=b['fat'];cm.spec={**cm.spec,'filtros_fixos':[{'campo':'F2_TIPO','valor':'N'}]};cm.save()
    m,r,_,_=executar(b)
    itens=next(x.resultado for x in r.blocos if x.titulo.startswith('Itens SD2 por TES'))
    assert itens is not None
    assert 'F2_MOEDA' in itens.colunas
    assert any('2.431,27' in l for l in itens.linhas)
    assert not any('6.000,00' in l for l in itens.linhas)
    assert not any('700.000' in str(l) for l in itens.linhas)

def test_novo_periodo_nao_reaproveita_consulta_antiga_em_recusa(faturamento):
    b=faturamento
    _,_,_,c=executar(b,'Some o faturamento de setembro')
    p=Espiao([_d(resposta='Qual o ano?')])
    m,r=conversas.enviar(b['ana'],c,'Investigue o faturamento de janeiro de 2025',p)
    assert r is None


def test_acao_semantica_dispara_investigacao_sem_palavras_chave(faturamento):
    b=faturamento
    c=conversas.nova_individual(b['ana'])
    decisao=consulta(b);decisao['acao']='investigar_divergencia'
    p=Espiao([decisao])
    m,r=conversas.enviar(b['ana'],c,'Confira os critérios e explique o que aconteceu nisso',p)
    assert m.ficha['tipo']=='diagnostico' and len(r.blocos)>3


def test_diagnostico_generico_nao_depende_de_sf2(base):
    from .test_ciclo_agente import _consulta
    c=conversas.nova_individual(base['ana'])
    decisao=_consulta(base['cm'],filial='TODAS',agregacao='soma:E1_VALOR')
    decisao['acao']='investigar_divergencia'
    m,r=conversas.enviar(base['ana'],c,'Explique o resultado',Espiao([decisao]))
    assert m.ficha['tipo']=='diagnostico'
    assert any('E1_TIPO' in b.titulo for b in r.blocos)
    assert any(b.titulo == diagnostico.DUPLICIDADE for b in r.blocos)  # M40: duplicidade conferida
    assert 'faturamento' not in r.resumo.lower() and 'TES' not in r.resumo  # resumo fala do assunto certo
    assert 'título repetido' in r.resumo or 'lançados mais de uma vez' in r.resumo

def test_apresentacao_tem_evidencias_e_nao_promessa(faturamento):
    from django.template.loader import render_to_string
    _,r,_,_=executar(faturamento)
    html=render_to_string('chat/_consolidado.html',{'resultado':r})
    assert 'F2_TIPO' in html and 'TES' in html and '2.431,27' in html
    assert 'Há valores positivos em TES' in html

def test_consolidacao_preserva_setor_sem_somar_escopos(faturamento):
    from iativa.query.consolidado import Consolidado,Bloco
    b=faturamento
    _,inicial,_,c=executar(b,'Some o faturamento')
    s=inicial.solicitacao
    r=diagnostico.investigar(b['ana'],s,Consolidado([Bloco('Teste A',inicial),Bloco('Teste B',mensagem='Sem acesso')],''))
    assert len(r.blocos)==2 and not r.completo
    assert r.blocos[0].resultado.consolidado
    from django.template.loader import render_to_string
    assert '2.431,27' in render_to_string('chat/_consolidado.html',{'resultado':r})

def test_por_filial_nao_interrompe_investigacao_por_erro_de_formato(faturamento):
    b=faturamento
    c=conversas.nova_individual(b['ana'])
    d=consulta(b);d['acao']='investigar_divergencia'
    d['parametros'][-1]['valor']+=' por filial'
    p=Espiao([d])
    m,r=conversas.enviar(b['ana'],c,'Investigue a diferença do faturamento em todas as filiais',p)
    assert m.ficha['tipo']=='diagnostico' and len(r.blocos)==12
    assert len(p.entradas)==1
