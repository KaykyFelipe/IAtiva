"""M109: a resposta montada passa pelo revisor antes de ir para a pessoa. Reprovada: tenta de outro jeito; esgotado,
vai para a manutenção automática sem mostrar a resposta errada."""
import pytest

from iativa.agent import revisao
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from iativa.manutencao.models import Incidente
from iativa.query.servico import Resultado
from .test_contagem_intervalo_datas import PERGUNTA, datas  # noqa: F401
from .test_historico_funcional import funcional  # noqa: F401
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db

REPROVA = {'natureza': 'dados', 'setores': [], 'confianca': 1, 'memorias': [],
           'resumo': 'Calcule o prazo entre admissão e demissão e liste só quem atende.',
           'entregas': [{'descricao': 'Nomes de quem saiu antes de 90 dias', 'tipo': 'consulta', 'metrica': ''}]}
CALC = {'operacao': 'intervalo_entre_datas', 'campo': 'Admissão', 'campo_final': 'Demissão', 'limite': 90}


def test_reprovada_tenta_de_novo_e_entrega_so_a_aprovada(datas):  # noqa: F811
    b, c = datas
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
                      _d(acao='calcular_resultado', calculo=CALC),
                      _d(acao='calcular_resultado', calculo={**CALC, 'limite': 91})], revisoes=[REPROVA])
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, p)
    assert res is not None and res.medida_calculada == 'intervalo_entre_datas', msg.texto
    assert res.colunas[-3:] == ['Admissão', 'Demissão', 'Dias'] and len(res.linhas) == 2  # quem atende, não a base
    assert not p.revisoes  # o revisor foi consultado


def test_reprovada_sempre_vai_para_a_manutencao_sem_mostrar_a_resposta(datas, settings):  # noqa: F811
    settings.IATIVA_REVISAO_TENTATIVAS = 1
    b, c = datas
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
                      _d(acao='calcular_resultado', calculo=CALC),
                      _d(acao='calcular_resultado', calculo=CALC)], revisoes=[REPROVA, REPROVA])
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, p)
    inc = Incidente.objects.get()
    assert inc.condicoes['causa'] == 'revisao_reprovada' and 'Nomes de quem saiu' in inc.condicoes['capacidade']
    assert 'corrigindo' in msg.texto.lower() and (res is None or not getattr(res, 'linhas', None)), msg.texto


def test_base_sem_data_final_e_reprovada_sem_chamar_o_modelo():
    r = Resultado(True, 'Registros com menos de 90 dias corridos entre A e B: 0 de 0 com B.', colunas=['A', 'B', 'Dias'],
                  tipos=['data', 'data', 'inteiro'], linhas=[], medida_calculada='intervalo_entre_datas')
    saida = type('S', (), {'resultado': r})()
    assert 'segundo evento' in revisao.revisar(None, None, PERGUNTA, [], saida)


def test_revisor_nao_ve_linhas_do_erp():
    r = Resultado(True, 'Lista', colunas=['Nome', 'Admissão'], tipos=['texto', 'data'],
                  linhas=[['FULANO DE TAL', '01/02/2026']])
    texto = str(revisao.descrever(type('S', (), {'resultado': r})()))
    assert 'FULANO' not in texto and "'registros': 1" in texto


def test_revisor_desligado_aprova(settings):
    settings.IATIVA_REVISAO = False
    r = Resultado(True, '0 de 0', colunas=['A'], tipos=['texto'], linhas=[], medida_calculada='intervalo_entre_datas')
    assert revisao.revisar(None, None, PERGUNTA, [], type('S', (), {'resultado': r})()) == ''


def test_m110_calculo_sobre_resposta_com_blocos_usa_o_bloco_certo():
    from iativa.agent.calculos import _bloco
    from iativa.agent.diagnostico import Bloco, Diagnostico
    base = Resultado(True, 'Lista', colunas=['Nome', 'Admissão', 'Demissão'], tipos=['texto', 'data', 'data'], linhas=[])
    calc = Resultado(True, 'x', colunas=['Cálculo', 'Resultado'], tipos=['texto', 'inteiro'], linhas=[],
                     medida_calculada='contagem')
    junto = Diagnostico([Bloco('Contagem', calc), Bloco('Lista', base)], '')
    assert _bloco(junto, {'campo': 'Admissão', 'campo_final': 'Demissão'}) is base


def test_m110_aprovada_depois_de_reprovar_guarda_o_aprendizado(datas):  # noqa: F811
    from iativa.agent.models import NotaAgente
    b, c = datas
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
                      _d(acao='calcular_resultado', calculo=CALC),
                      _d(acao='calcular_resultado', calculo={**CALC, 'limite': 91})], revisoes=[REPROVA])
    conversas.enviar(b['u'], c, PERGUNTA, p)
    nota = NotaAgente.objects.get(tipo='correcao')
    assert 'liste só quem atende' in nota.texto and 'Quantos admitidos' in nota.texto
