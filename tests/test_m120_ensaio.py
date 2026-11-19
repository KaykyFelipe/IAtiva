"""M120: a correção só é publicada se o pedido real, refeito na imagem candidata, entregar o que foi pedido."""
import subprocess
from types import SimpleNamespace

import pytest

from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.chat import conversas
from iativa.manutencao import incidentes
from iativa.manutencao.management.commands.manutencao import ensaiar
from iativa.manutencao.models import Incidente
from .test_m114_cancelar_manutencao import mx
from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def _incidente_com_pedido(base):  # noqa: F811
    u = base["u"]
    c = conversas.nova_individual(u)
    m = c.mensagens.create(papel="usuario", texto="quantos admitidos por filial em 2026?", autor=u)
    inc = Incidente.objects.create(assinatura="x" * 64, categoria="capacidade_ausente", codigo="a.b.c",
                                   componente="agente", operacao="op", versao="v", correlacao="c")
    Objetivo.objects.create(usuario=u, conversa=c, mensagem=m, incidente=inc, situacao=SO.MANUTENCAO,
                            exigencia="calculo")
    return inc


@pytest.mark.parametrize("ficha,res_ok,esperado", [
    ({}, True, "ok"), ({"corrigindo": True}, False, "falha"), ({}, False, "falha")])
def test_ensaiar_diz_se_o_pedido_real_saiu(base, monkeypatch, ficha, res_ok, esperado):  # noqa: F811
    inc = _incidente_com_pedido(base)
    antes = conversas.ConversationContext.objects.count() if hasattr(conversas, "ConversationContext") else None

    def enviar(user, c, texto, *a, **k):
        return SimpleNamespace(texto="Não tenho isso ainda, corrigindo…", ficha=ficha), SimpleNamespace(ok=res_ok)
    monkeypatch.setattr(conversas, "enviar", enviar)
    assert ensaiar(inc.pk).startswith(esperado)
    if antes is not None:
        assert conversas.ConversationContext.objects.count() == antes  # conversa temporária apagada


def test_ensaiar_sem_pedido():
    inc = Incidente.objects.create(assinatura="y" * 64, categoria="erro_tecnico", codigo="w.x.y", componente="web",
                                   operacao="op", versao="v", correlacao="c")
    assert ensaiar(inc.pk) == "sem_pedido"


def test_em_ensaio_nao_abre_incidente(monkeypatch):
    monkeypatch.setenv("IATIVA_ENSAIO", "1")
    assert incidentes.registrar("agente", "x", categoria="capacidade_ausente", condicoes={"trilha": ["a:b:c"]}) is None
    assert not Incidente.objects.exists()


@pytest.mark.parametrize("saida,motivo", [("ok\n", ""), ("sem_pedido\n", ""),
                                          ("log\nfalha: faltou a lista\n", "faltou a lista")])
def test_executor_le_o_resultado_do_ensaio(tmp_path, monkeypatch, saida, motivo):
    def rodar(cmd, **k):
        return subprocess.CompletedProcess(cmd, 0, saida if cmd[:2] == ["docker", "run"] else "", "")
    monkeypatch.setattr(mx, "rodar", rodar)
    monkeypatch.setattr(mx, "etapa", lambda *a: None)
    monkeypatch.setattr(mx, "agente_de_teste", lambda: "")
    assert mx.ensaiar_pedido(tmp_path, 5) == motivo


def test_m122_ensaio_roda_as_continuacoes_e_diz_o_que_faltou(base, monkeypatch):  # noqa: F811
    from iativa.agent import agente
    inc = _incidente_com_pedido(base)

    def enviar(user, c, texto, *a, **k):
        m = c.mensagens.create(papel="usuario", texto=texto, autor=user)
        Objetivo.objects.create(usuario=user, conversa=c, mensagem=m, situacao=SO.CONTINUAR, exigencia="calculo",
                                contrato={"entregas": [{"descricao": "Admitidos por filial"},
                                                       {"descricao": "Desligados por filial"}], "cumpridas": [0]})
        return SimpleNamespace(texto="Sigo em segundo plano", ficha={}), None
    chamadas = []

    def continuar(o):
        chamadas.append(o.execucao)
        Objetivo.objects.filter(pk=o.pk).update(situacao=SO.IMPEDIDO)
    monkeypatch.setattr(conversas, "enviar", enviar)
    monkeypatch.setattr(agente, "continuar_objetivo", continuar)
    r = ensaiar(inc.pk)
    assert chamadas and chamadas[0]  # continuação rodou com execução própria
    assert r == "falha: não entregou: Desligados por filial (entregou 1 de 2)"


def test_m122_tentativa_aparece_no_card(base, client):  # noqa: F811
    from django.core.management import call_command
    from django.urls import reverse
    inc = _incidente_com_pedido(base)
    call_command("manutencao", "tentativa", inc.pk, "2", "--motivo", "não entregou: Desligados por filial")
    o = Objetivo.objects.get(incidente=inc)
    client.force_login(base["u"])
    html = client.get(reverse("chat:objetivo", args=[o.pk])).content.decode()
    assert "Tentativa 2 de 3" in html and "Desligados por filial" in html


def test_m123_ensaio_usa_o_usuario_de_teste_da_ti(base, monkeypatch, django_user_model, settings):  # noqa: F811
    inc = _incidente_com_pedido(base)
    settings.IATIVA_ENSAIO_USUARIO = "teste.ensaio"
    from iativa.accounts.models import UserProfile
    ti = django_user_model.objects.create_user("teste.ensaio")
    UserProfile.objects.create(user=ti, department=base["u"].perfil_iativa.department)
    usados = []

    def enviar(user, c, texto, *a, **k):
        usados.append((user, c.usuario_id))
        return SimpleNamespace(texto="ok", ficha={}), SimpleNamespace(ok=True)
    monkeypatch.setattr(conversas, "enviar", enviar)
    assert ensaiar(inc.pk) == "ok"
    assert usados == [(ti, ti.pk)]  # nunca na conta de quem perguntou
