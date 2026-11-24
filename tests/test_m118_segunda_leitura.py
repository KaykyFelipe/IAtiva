"""M118: o pedido é lido duas vezes; a segunda leitura completa o contrato (cada evento e quem foi contado)."""
import pytest

from iativa.agent import planejamento
from iativa.agent.provedores import FakeProvider
from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db
PEDIDO = "Quantos funcionários foram admitidos e desligados no mesmo ano, por filial, em 2026?"
TIPOS = {'resposta', 'consulta', 'calculo', 'verificacao', 'investigacao', 'historico'}
METRICAS = {'', 'contagem'}
PRIMEIRA = {'natureza': 'dados', 'entregas': [{'descricao': 'Contagem por filial dos que tiveram os dois eventos',
                                               'tipo': 'calculo', 'metrica': 'contagem'}]}
SEGUNDA = {'natureza': 'dados', 'entregas': [
    {'descricao': 'Admitidos em 2026 por filial', 'tipo': 'calculo', 'metrica': 'contagem'},
    {'descricao': 'Desligados em 2026 por filial', 'tipo': 'calculo', 'metrica': 'contagem'},
    {'descricao': 'Admitidos e desligados em 2026 (os dois eventos) por filial', 'tipo': 'calculo', 'metrica': 'contagem'},
    {'descricao': 'Lista de quem foi contado, com admissão e demissão', 'tipo': 'consulta', 'metrica': ''}]}


def test_segunda_leitura_completa_o_contrato():
    a = planejamento._segunda_leitura(FakeProvider(releituras=[SEGUNDA]), PEDIDO, [], PRIMEIRA, TIPOS, METRICAS)
    assert [e['descricao'] for e in a['entregas']] == [e['descricao'] for e in SEGUNDA['entregas']]
    assert a['primeira_leitura'] == PRIMEIRA['entregas']


@pytest.mark.parametrize("ruim", [None, {}, {'entregas': []}, {'entregas': [{'tipo': 'x'}]}])
def test_segunda_leitura_invalida_mantem_a_primeira(ruim):
    class P(FakeProvider):
        def planejar(self, entrada):
            return ruim
    assert planejamento._segunda_leitura(P(), PEDIDO, [], PRIMEIRA, TIPOS, METRICAS) is PRIMEIRA


def test_falha_do_modelo_na_segunda_leitura_mantem_a_primeira():
    class P(FakeProvider):
        def planejar(self, entrada):
            raise RuntimeError("x")
    assert planejamento._segunda_leitura(P(), PEDIDO, [], PRIMEIRA, TIPOS, METRICAS) is PRIMEIRA


def test_m121_andamento_em_lista_e_passos_proporcionais(base):  # noqa: F811
    import inspect
    from iativa.agent import agente
    from iativa.agent.objetivos import Acompanhamento
    from iativa.chat import conversas
    ac = Acompanhamento(PEDIDO, exig="calculo")
    ac.contrato = [{"descricao": d, "tipo": "calculo"} for d in ("Admitidos por filial", "Desligados por filial", "Lista")]
    ac.cumpridas = [2]
    ac.anotar("preparar_consulta", "entrega_pendente", "consulta")
    c = conversas.nova_individual(base["u"])
    m = c.mensagens.create(papel="usuario", texto=PEDIDO, autor=base["u"])
    s = agente._encerrar_sem_cumprir(ac, base["u"], c, m.pk, None, None, "passos", [])
    # M142: sem a lista de partes ("Seu pedido tem N partes / Ainda falta").
    assert "Seu pedido tem" not in s.texto and "partes do seu pedido" not in s.texto, s.texto
    assert "Ainda falta" not in s.texto, s.texto
    assert "passos = min(14, max(int(cfg[\"passos\"]), 2 * len(ac.contrato) + 1))" in inspect.getsource(agente)
