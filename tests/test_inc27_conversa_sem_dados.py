"""Inc. 27: mensagem sem pedido de dados ("teste de chat ...") não pode ficar presa numa exigência de consulta
que o modelo declarou por engano enquanto responde conversando."""
import pytest

from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.manutencao.models import Incidente

from .test_objetivos import _enviar, _listar
from .test_tarefas import _d, base  # noqa: F401

pytestmark = pytest.mark.django_db

OK = "Recebi sua mensagem de teste: o chat está funcionando."


def test_mensagem_de_teste_sem_pedido_de_dados_e_entregue_conversando(base):
    msg, res, p, _ = _enviar(base, "teste de chat ocupado",
                             [_d(resposta=OK, entrega="consulta")] * 3)
    assert msg.texto == OK and res is None
    assert not Incidente.objects.exists()
    assert not Objetivo.objects.filter(situacao=SO.MANUTENCAO).exists()


def test_pedido_de_dados_declarado_como_consulta_continua_exigindo_consulta(base):
    msg, res, p, _ = _enviar(base, "me traga os demitidos",
                             [_d(resposta=OK, entrega="consulta"), _listar(base)])
    assert p.chamadas == 2 and res is not None and res.ok


@pytest.mark.parametrize("texto,esperado", [
    ("teste de chat ocupado", "resposta"), ("oi, tudo bem?", "resposta"), ("obrigado!", "resposta"),
    ("teste: quantos funcionarios demitidos em 2026?", "consulta"), ("oi, me traga os titulos", "consulta"),
    ("me traga os demitidos", "consulta"),
])
def test_limites_da_conversa_sem_dados(texto, esperado):
    from types import SimpleNamespace
    from iativa.agent.objetivos import exigencia
    d = SimpleNamespace(acao="conversar", entrega="consulta", parametros=())
    assert exigencia(texto, d)[0] == esperado
