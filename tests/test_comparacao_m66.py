"""M66: comparação geral de dois períodos (auditoria 17/11, etapa 2 do prompt de melhorias)."""
import sqlite3

import pytest
from django.http import QueryDict

from iativa.agent import aprendizado, comparacao
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.manutencao.models import Incidente

from .test_ciclo_agente import base, _d  # noqa: F401
from .test_diagnostico import faturamento  # noqa: F401


def _consulta_sd2(b, colunas):
    q = QueryDict(mutable=True)
    q.update({'nome': 'Itens sintéticos', 'campo_data': 'D2_EMISSAO'})
    q.setlist('colunas', colunas)
    q.setlist('setores', [str(b['cob'].pk)])
    return aprendizado.aprovar(b['admin'], aprendizado.salvar_proposta(b['admin'], 'SD2', q))


def _comparar(b, cm, texto, agregacao=None):
    params = [{'codigo': 'filial-analise', 'valor': '01'}]
    if agregacao:
        params.append({'codigo': 'agregacao', 'valor': agregacao})
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=cm.codigo, entrega='calculo', parametros=params)])
    m, r = conversas.enviar(b['ana'], conversas.nova_individual(b['ana']), texto, p)
    return m, r, p


def test_periodos_gerais_sem_exigir_faturamento():
    assert [x for _, _, x in comparacao.periodos('compare as admissões de julho e agosto de 2026')] == ['07/2026', '08/2026']
    assert [x for _, _, x in comparacao.periodos('variação de 2025 versus 2026')] == ['2025', '2026']
    assert comparacao.periodos('quanto faturamos em julho?') == []


def test_compara_por_outro_agrupamento_sem_forcar_filial(faturamento):
    """Agrupamento pedido (por CFOP) é preservado; nada vira "por filial"."""
    b = faturamento
    with sqlite3.connect(b['erp']) as conn:
        conn.execute("INSERT INTO SD2010 VALUES ('01','20260808','501','5102',1000,1,' ')")
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_CF', 'D2_TOTAL'])
    m, r, p = _comparar(b, cm, 'Compare o total de agosto e setembro de 2026 por CFOP', 'soma:D2_TOTAL por D2_CF')
    assert r is not None and r.medida_calculada == 'comparacao', m.texto
    assert r.colunas[0] != 'Filial'
    grupos = {l[0]: l for l in r.linhas}
    assert set(grupos) == {'5102', '5915'}
    # 5915 não existe em agosto: soma conta zero e a variação é indefinida (base zero), não inventada.
    assert grupos['5915'][1] == 'R$ 0,00' and grupos['5915'][4] == 'Indefinido (base zero)'
    assert p.chamadas == 1 and not Incidente.objects.exists()


def test_compara_contagem(faturamento):
    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    m, r, _ = _comparar(b, cm, 'Compare a quantidade de itens de agosto com setembro de 2026', 'contagem:*')
    assert r is not None and r.medida_calculada == 'comparacao', m.texto
    assert r.linhas[0][1:3] == ['0', '3'], r.linhas


def test_media_sem_registros_nao_vira_zero(faturamento):
    b = faturamento
    cm = _consulta_sd2(b, ['D2_EMISSAO', 'D2_TOTAL'])
    m, r, _ = _comparar(b, cm, 'Compare a média de julho e setembro de 2026', 'media:D2_TOTAL')
    assert r is not None, m.texto
    # Julho sem registros: média não existe (não é zero); a variação não é calculável.
    assert r.linhas[0][1] == 'Sem valor' and r.linhas[0][3] == 'Não calculável', r.linhas
