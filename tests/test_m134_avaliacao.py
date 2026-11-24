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
    assert r.status_code == 200 and "avaliada-nao" in r.content.decode()
    assert AvaliacaoResposta.objects.get().comentario == "filtro geral"


def test_botao_aparece_ao_lado_do_copiar(base, client, monkeypatch):  # noqa: F811
    from iativa.chat import resultados
    monkeypatch.setattr(resultados, "restaurar", lambda user, r: None if not r else {"ok": True, "colunas": [], "linhas": []})
    c, m = _resposta(base)
    client.force_login(base["u"])
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert html.index("data-copiar-resposta") < html.index("data-avaliar") and "icone-avaliar" in html


def _ti(base):  # noqa: F811
    from django.contrib.auth import get_user_model

    from iativa.accounts.models import AccessPolicy, Department
    ti = get_user_model().objects.create_user("teste.ti", password="x")
    dep, _ = Department.objects.get_or_create(codigo="ti", defaults={"nome": "TI"})
    AccessPolicy.objects.create(user=ti, department=dep, pode_administrar=True)
    return ti


def test_m136_nao_chega_ao_ti_em_memoria_e_aprovacoes(base, client):  # noqa: F811
    c, m = _resposta(base)
    conversas.avaliar(base["u"], c.pk, m.pk, False, "era só a filial Cuiabá")
    ti = _ti(base)
    client.force_login(ti)
    html = client.get(reverse("chat:memoria")).content.decode()
    assert "Respostas para verificar" in html and "era só a filial Cuiabá" in html and "Quantos pedidos em aberto?" in html
    a = AvaliacaoResposta.objects.get()
    client.post(reverse("chat:avaliacao_verificar", args=[a.pk]))
    a.refresh_from_db()
    assert a.verificada


def test_m136_quem_nao_e_ti_nao_ve(base, client):  # noqa: F811
    c, m = _resposta(base)
    conversas.avaliar(base["u"], c.pk, m.pk, False, "x")
    from django.contrib.auth import get_user_model
    outro = get_user_model().objects.create_user("sem.ti", password="x")
    assert conversas.avaliacoes_para_ti(outro) == []
    client.force_login(outro)
    client.post(reverse("chat:avaliacao_verificar", args=[AvaliacaoResposta.objects.get().pk]))
    assert not AvaliacaoResposta.objects.get().verificada
