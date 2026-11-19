"""Contagens mensais com duas datas de evento no ciclo do agente."""
import sqlite3

import pytest
from django.http import QueryDict

from iativa.accounts.filiais import rotulo
from iativa.agent import aprendizado
from iativa.agent.models import Objetivo, SituacaoObjetivo
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas

from .test_ciclo_agente import base, _d  # noqa: F401
from .test_comparacao_m66 import PEDIDO_EVENTOS

pytestmark = pytest.mark.django_db


def _consulta(base, alias, campo_data, linhas):
    fisica = alias + '010'
    filial = alias + '_FILIAL'
    with sqlite3.connect(base['erp']) as db:
        db.execute(f'CREATE TABLE {fisica} ({filial} CHAR(2), {campo_data} CHAR(8), D_E_L_E_T_ CHAR(1))')
        db.executemany(f'INSERT INTO {fisica} VALUES (?, ?, ?)', linhas)
        db.execute("""INSERT INTO SX2010 (X2_CHAVE, X2_ARQUIVO, X2_NOME, X2_MODO, X2_MODOUN, X2_MODOEMP,
                                         D_E_L_E_T_, R_E_C_N_O_)
                      SELECT ?, ?, ?, 'E', 'E', 'E', ' ', MAX(R_E_C_N_O_) + 1 FROM SX2010""",
                   (alias, fisica, alias))
        for campo, tipo, tamanho in [(filial, 'C', 2), (campo_data, 'D', 8)]:
            db.execute("""INSERT INTO SX3010 (X3_ARQUIVO, X3_CAMPO, X3_TIPO, X3_TAMANHO, X3_DECIMAL,
                                             X3_TITULO, X3_CONTEXT, D_E_L_E_T_, R_E_C_N_O_)
                          SELECT ?, ?, ?, ?, '0', ?, 'R', ' ', MAX(R_E_C_N_O_) + 1 FROM SX3010""",
                       (alias, campo, tipo, tamanho, campo))
    q = QueryDict(mutable=True)
    q.update({'nome': alias, 'campo_data': campo_data})
    q.setlist('colunas', [campo_data])
    q.setlist('setores', [str(base['cob'].pk)])
    return aprendizado.aprovar(base['admin'], aprendizado.salvar_proposta(base['admin'], alias, q))


def test_repetir_consulta_de_inicio_entrega_as_duas_contagens_por_filial(base):
    fora = [('01', '20260831', ' '), ('02', '20261101', ' '),
            ('03', '20261001', ' '), ('02', '20260930', '*')]
    _consulta(base, 'FP0', 'FP0_ZDTASS', fora + [
        ('01', '20260901', ' '), ('01', '20260930', ' '), ('01', '20261001', ' '),
        ('02', '20260901', ' '), ('02', '20261031', ' '),
    ])
    fpa = _consulta(base, 'FPA', 'FPA_DTINI', fora + [
        ('01', '20260930', ' '), ('01', '20261001', ' '), ('01', '20261031', ' '),
        ('02', '20260901', ' '), ('02', '20260930', ' '), ('02', '20261031', ' '),
    ])
    d = _d(acao='preparar_consulta', objetivo=fpa.codigo, entrega='calculo', parametros=[
        {'codigo': 'filial-analise', 'valor': 'TODAS'},
        {'codigo': 'periodo-analise', 'valor': '01/09/2026..31/10/2026'},
        {'codigo': 'agregacao', 'valor': 'contagem:* por mes'},
    ])
    user = base['ana']
    msg, res = conversas.enviar(user, conversas.nova_individual(user), PEDIDO_EVENTOS, FakeProvider([d, d, d]))

    objetivo = Objetivo.objects.get(conversa=msg.conversa)
    assert objetivo.situacao == SituacaoObjetivo.CUMPRIDO, (msg.texto, objetivo.trilha)
    assert res is not None and res.consolidado
    esperadas = {
        'Contratos assinados': ('2', '1', '1', '1'),
        'Locações iniciadas': ('1', '2', '2', '1'),
    }
    blocos = {bloco.titulo.split(' em cada mês')[0]: bloco for bloco in res.blocos}
    assert len(res.blocos) == 2 and blocos.keys() == esperadas.keys()
    for evento, bloco in blocos.items():
        contagens = {(filial, mes): n for filial, mes, n in bloco.resultado.linhas}
        assert contagens == dict(zip(
            [(rotulo('01'), '09/2026'), (rotulo('01'), '10/2026'),
             (rotulo('02'), '09/2026'), (rotulo('02'), '10/2026')],
            esperadas[evento],
        ))
