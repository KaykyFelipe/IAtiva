"""M148: recusa/falha antiga do sistema no histórico não leva a IA a responder "sem permissão" sem executar.

Ex.: "quantos veículos tem a empresa?" falhou duas vezes ("A fonte não é autorizada...", "Sem permissão de consulta...");
depois da correção do acesso, a IA lia essas recusas no histórico e respondia sem consultar."""
from types import SimpleNamespace

from iativa.agent import agente


def _m(texto, papel="ia", ficha=None, resultado=None):
    return SimpleNamespace(texto=texto, papel=papel, ficha=ficha, resultado=resultado)


def test_falhas_do_sistema_viram_aviso_neutro():
    for texto, ficha in [("A fonte não é autorizada para o setor desta solicitação.", {"tipo": "falha_execucao"}),
                         ("Sem permissão de consulta ou sem filiais autorizadas neste setor.", None),
                         ("Não terminei esta resposta. Tentei: consultar.", None),
                         ("Qualquer texto", {"tipo": "bloqueada"})]:
        assert agente._falha_anterior(_m(texto, ficha=ficha))


def test_respostas_reais_e_perguntas_ficam():
    assert not agente._falha_anterior(_m("Sem permissão de consulta", papel="usuario"))
    assert not agente._falha_anterior(_m("Consulta executada às 17:02.", ficha={"tipo": "executada"}, resultado={"x": 1}))
    assert not agente._falha_anterior(_m("Não informo aniversários.", ficha={"tipo": "bloqueada", "dado_protegido": "nasc"}))
    assert not agente._falha_anterior(_m("Qual filial você quer?"))


def test_historico_substitui_so_as_falhas(monkeypatch):
    msgs = [_m("quantos veiculos tem a empresa?", papel="usuario"),
            _m("Sem permissão de consulta ou sem filiais autorizadas neste setor."),
            _m("quantos veiculos tem a empresa?", papel="usuario")]

    class _Qs(list):
        def all(self):
            return self

        def order_by(self, *a):
            return _Qs(reversed(self))

    c = SimpleNamespace(mensagens=_Qs(msgs))
    h = agente._historico(c)
    assert [x["texto"] for x in h] == ["quantos veiculos tem a empresa?", agente.FALHA_NO_HISTORICO,
                                       "quantos veiculos tem a empresa?"]
