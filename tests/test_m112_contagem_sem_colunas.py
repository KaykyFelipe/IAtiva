"""Incidente agente.objetivo.calculo.revisao_reprovada.269f98db2d: pedido de prazo entre dois eventos ("admitidos
que saíram antes de 90 dias"); o modelo pediu só 'contagem' (sem as colunas de data) e o total contou a base inteira,
sem a condição do prazo. O backend deve aplicar intervalo_entre_datas com as duas datas do resultado. Dados sintéticos."""
import pytest

from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from .test_contagem_intervalo_datas import PERGUNTA, datas  # noqa: F401
from .test_historico_funcional import funcional  # noqa: F401
from .test_m109_revisao import REPROVA
from .test_tarefas import base, _d  # noqa: F401

pytestmark = pytest.mark.django_db


def test_contagem_sem_colunas_aplica_o_prazo_do_pedido(datas):  # noqa: F811
    b, c = datas
    p = FakeProvider([_d(acao='preparar_consulta', objetivo=b['cm'].codigo, entrega='calculo'),
                      _d(acao='calcular_resultado', calculo={'operacao': 'contagem'}),
                      _d(acao='calcular_resultado', calculo={'operacao': 'contagem'})], revisoes=[])
    # Sem a correção, a contagem da base inteira (sem o prazo) era o que o revisor reprovava (REPROVA).
    assert REPROVA['entregas']
    msg, res = conversas.enviar(b['u'], c, PERGUNTA, p)
    assert res is not None and res.medida_calculada == 'intervalo_entre_datas', msg.texto
    assert res.curta_salva == '2' and len(res.linhas) == 2, msg.texto
    assert any('menos de 90 dias' in str(x) for x in res.premissas)
