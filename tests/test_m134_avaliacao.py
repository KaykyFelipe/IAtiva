"""M134: "Esta informação está correta?" ao lado do copiar: Sim/Não; "Não" fica para a TI verificar."""
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from iativa.chat import conversas
from iativa.chat.models import AvaliacaoResposta, Mensagem, Papel

from .test_tarefas import base  # noqa: F401

pytestmark = pytest.mark.django_db


def _resposta(base, resultado=True):  # noqa: F811
    c = conversas.nova_individual(base["u"])
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=base["u"], texto="Quantos pedidos em aberto?")
    m = Mensagem.objects.create(conversa=c, papel=Papel.IA, texto="Contei.",
                                resultado={"ok": True} if resultado else None)
    return c, m


def test_nao_vai_para_a_ti_com_o_comentario(base):  # noqa: F811
    c, m = _resposta(base)
    a = conversas.avaliar(base["u"], c.pk, m.pk, False, "  era só   a filial 01 ")
    assert not a.correta and a.comentario == "era só a filial 01" and not a.verificada
    a = conversas.avaliar(base["u"], c.pk, m.pk, True, "ignorado")
    assert AvaliacaoResposta.objects.count() == 1 and a.correta and a.comentario == ""


def test_so_resposta_com_resultado(base):  # noqa: F811
    c, m = _resposta(base, resultado=False)
    with pytest.raises(ValidationError):
        conversas.avaliar(base["u"], c.pk, m.pk, True)


def test_view_devolve_o_estado(base, client):  # noqa: F811
    c, m = _resposta(base)
    client.force_login(base["u"])
    r = client.post(reverse("chat:avaliar", args=[c.pk, m.pk]), {"correta": "0", "comentario": "filtro geral"})
    assert r.status_code == 200 and "Enviado para a TI verificar" in r.content.decode()
    assert AvaliacaoResposta.objects.get().comentario == "filtro geral"
