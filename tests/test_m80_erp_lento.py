"""M80: ERP lento (tempo esgotado) e ação repetida: continua sozinho em segundo plano, não desiste."""
import pytest

from iativa.agent import agente, objetivos as coord
from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.chat import conversas
from iativa.chat.models import Mensagem

from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def test_tempo_esgotado_repetido_continua_em_segundo_plano(base):
    c = conversas.nova_individual(base['u'])
    m = Mensagem.objects.create(conversa=c, papel='usuario', autor=base['u'], texto='quantos admitidos em agosto?')
    ac = coord.Acompanhamento('quantos admitidos em agosto?', exig=coord.CALCULO)
    ac.anotar('preparar_consulta', agente.TEMPO_ESGOTADO)
    ac.anotar('preparar_consulta', 'acao_repetida')
    s = agente._encerrar_sem_cumprir(ac, base['u'], c, m.pk, agente.Saida(''), None, 'acao_repetida', [])
    assert Objetivo.objects.get().situacao == SO.CONTINUAR
    assert 'segundo plano' in s.texto and 'Tente de novo' not in s.texto


def test_prazo_esgotado_dentro_da_acao_nao_vira_erro_500(base, monkeypatch):
    """M96: tempo total esgotado durante a ação (ex.: aprendendo tabela) segue o caminho do prazo, com resposta."""
    from iativa.agent import prazo
    from iativa.agent.provedores import FakeProvider

    def estoura(*a, **kw):
        raise prazo.PrazoEsgotado()
    monkeypatch.setattr(agente, '_agir', estoura)
    c = conversas.nova_individual(base['u'])
    p = FakeProvider([{"resposta": "", "acao": "preparar_consulta", "objetivo": base['cm'].codigo, "parametros": [],
                       "alvo": "", "nota": "", "confirmado": False, "opcoes": []}])
    m, r = conversas.enviar(base['u'], c, 'qual a taxa de ocupação dos ativos?', p)
    assert m.papel == 'ia' and m.texto  # respondeu (não 500) e a mensagem da pessoa ficou
    assert Mensagem.objects.filter(conversa=c, papel='usuario').exists()


def test_opcoes_de_filial_mostram_todas():
    """M96: 8 filiais + "Todas as filiais" cabem nas opções (antes cortava em 6)."""
    import inspect
    fonte = inspect.getsource(agente)
    assert "opcoes[:12]" in fonte and "r.perguntas[0].opcoes[:6]" not in fonte
