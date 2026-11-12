"""Nome → todas as matrículas autorizadas → SR7 filtrada → descrições, sem linhas no modelo."""
import json
import sqlite3

import pytest
from django.core.exceptions import ValidationError

from iativa.accounts.authz import PermissaoNegada
from iativa.accounts.models import AccessPolicy
from iativa.agent import historico_funcional as hf, prazo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.chat.models import Mensagem
from iativa.metadata.models import RestricaoMetadado
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def funcional(base, erp_sintetico):
    # Dicionário e tabelas fictícios adicionais só deste teste; não alteram a base padrão.
    with sqlite3.connect(erp_sintetico) as c:
        for alias, campos, modo in (
            ('SR7', [('R7_FILIAL', 'C', 2), ('R7_MAT', 'C', 6), ('R7_DATA', 'D', 8),
                     ('R7_SEQ', 'C', 2), ('R7_FUNCAO', 'C', 5), ('R7_DESCFUN', 'C', 40)], 'E'),
            ('SRJ', [('RJ_FILIAL', 'C', 2), ('RJ_FUNCAO', 'C', 5), ('RJ_DESC', 'C', 40)], 'E'),
        ):
            fisica = alias + '010'
            cols = ', '.join(f'{n} CHAR({t})' for n, _, t in campos)
            c.execute(f"CREATE TABLE {fisica} ({cols}, D_E_L_E_T_ CHAR(1) DEFAULT ' ')")
            rec = c.execute('SELECT MAX(R_E_C_N_O_) FROM SX2010').fetchone()[0] + 1
            c.execute('INSERT INTO SX2010 (X2_CHAVE,X2_ARQUIVO,X2_NOME,X2_MODO,X2_MODOUN,X2_MODOEMP,D_E_L_E_T_,R_E_C_N_O_) VALUES (?,?,?,?,?,?,?,?)',
                      (alias, fisica, alias, modo, modo, modo, ' ', rec))
            for n, tipo, tam in campos:
                rec = c.execute('SELECT MAX(R_E_C_N_O_) FROM SX3010').fetchone()[0] + 1
                c.execute('INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TIPO,X3_TAMANHO,X3_DECIMAL,X3_TITULO,X3_CONTEXT,D_E_L_E_T_,R_E_C_N_O_) VALUES (?,?,?,?,?,?,?,?,?)',
                          (alias, n, tipo, str(tam), '0', n, 'R', ' ', rec))
        for fil, mat, data, seq, func, desc, excluido in [
            ('01', '000102', '20260201', '01', '00004', '', ' '),
            ('01', '000150', '20261002', '01', '00175', '', ' '),
            ('01', '000150', '20261002', '02', '00175', '', '*'),
            ('02', '000102', '20260101', '01', '99999', 'NAO VAZAR', ' '),
        ]:
            c.execute('INSERT INTO SR7010 VALUES (?,?,?,?,?,?,?)',
                      (fil, mat, data, seq, func, desc, excluido))
        for func, desc in [('00004', 'Estagiário'), ('00175', 'Analista de sistemas')]:
            c.execute('INSERT INTO SRJ010 VALUES (?,?,?,?)', ('01', func, desc, ' '))
        c.execute("UPDATE SX3010 SET X3_DESCRIC = 'Data Alteracao Salarial' WHERE X3_CAMPO = 'R7_DATA'")
    return base


def test_pedido_nao_para_nas_matriculas_e_responde_cargos(funcional):
    b = funcional
    c = conversas.nova_individual(b['u'])
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo,
                        parametros=[{'codigo':'texto-busca', 'valor':'BRUNO SINTETICO LIMA'}])])
    msg, res = conversas.enviar(b['u'], c, 'Histórico de cargo/função do BRUNO SINTETICO LIMA', p)
    assert res and res.ok, msg.texto
    assert 'Estagiário' in res.mensagem and 'Analista de sistemas' in res.mensagem
    assert {l[1] for l in res.linhas} == {'000102', '000150'}
    assert len([l for l in res.linhas if l[-1].startswith('SR7')]) == 2
    assert 'NAO VAZAR' not in str(res.linhas)
    assert 'Estagiário' not in msg.texto and 'Analista de sistemas' not in msg.texto
    assert not Mensagem.objects.filter(texto__contains='Analista de sistemas').exists()


def test_continuacao_reusa_nome_sem_pedir_matricula_ou_linhas(funcional):
    b = funcional
    c = conversas.nova_individual(b['u'])
    p = FakeProvider([_d(acao='historico_cargo', alvo='BRUNO SINTETICO LIMA'), _d(resposta='Não vejo os resultados.')])
    conversas.enviar(b['u'], c, 'histórico de cargo do BRUNO SINTETICO LIMA', p)
    msg, res = conversas.enviar(b['u'], c, 'sabe me dizer qual o cargo que mudou?', p)
    assert res and 'Analista de sistemas' in res.mensagem, msg.texto
    assert 'Estagiário' in res.mensagem


def test_homonimos_com_nomes_diferentes_exigem_escolha(funcional):
    b = funcional
    res = hf.consultar(b['u'], None, 'SINTETICO')
    assert res.colunas == ['Nome encontrado'] and 'Informe o nome completo' in res.mensagem


def test_sem_historico_nao_inventa_mudanca(funcional, erp_sintetico):
    with sqlite3.connect(erp_sintetico) as c:
        c.execute('DELETE FROM SR7010')
    res = hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA')
    assert 'Não há registros na SR7' in res.mensagem
    assert 'Analista de sistemas' in res.mensagem


def test_periodo_explicito_filtra_historico_sem_descartar_matriculas(funcional):
    res = hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA', '01/10/2026..31/10/2026')
    hs = [l for l in res.linhas if l[-1].startswith('SR7')]
    assert len(hs) == 1 and hs[0][1] == '000150'
    assert len([l for l in res.linhas if l[-1].startswith('SRA')]) == 2


def test_sem_permissao_depois_da_busca_nao_le_historico(funcional, monkeypatch):
    original = hf.Leitura.consultar
    aliases = []
    def ler(self, alias, *args, **kw):
        aliases.append(alias)
        if alias == 'SR7':
            AccessPolicy.objects.filter(user=funcional['u']).update(pode_consultar=False)
        return original(self, alias, *args, **kw)
    monkeypatch.setattr(hf.Leitura, 'consultar', ler)
    with pytest.raises(PermissaoNegada):
        hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA')
    assert 'SR7' in aliases and 'SRJ' not in aliases


@pytest.mark.parametrize('campo', ['R7_MAT', 'R7_DATA'])
def test_campo_restrito_nao_e_usado_como_filtro(funcional, campo):
    RestricaoMetadado.objects.create(tabela='SR7', campo=campo)
    with pytest.raises(ValidationError, match='campos autorizados'):
        hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA')


def test_filial_nao_autorizada_nao_e_substituida(funcional):
    with pytest.raises(PermissaoNegada):
        hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA', filial='99')


def test_prazo_e_cancelamento_valem_entre_etapas(funcional):
    from iativa.agent.agente import Interrompido
    with pytest.raises(Interrompido):
        hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA', interrompido=lambda: True)
    with prazo.definir(0), pytest.raises(prazo.PrazoEsgotado):
        hf.consultar(funcional['u'], None, 'BRUNO SINTETICO LIMA')
