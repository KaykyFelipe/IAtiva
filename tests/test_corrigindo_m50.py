"""M50: incidente elegível acorda a manutenção na hora; o chat fica ocupado ("Corrigindo…") com botão parar."""
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from iativa.agent.models import Objetivo, SituacaoObjetivo as SO
from iativa.chat.models import Mensagem
from .test_objetivos import _enviar
from .test_tarefas import _d, base  # noqa: F401

pytestmark = pytest.mark.django_db

ANUNCIOS = [_d(resposta="Vou consultar os funcionários demitidos agora."), _d(resposta="Verificando os dados para você.")]


@pytest.fixture
def acordar(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    return Path(tmp_path) / "var" / "manutencao-acordar"


def test_incidente_elegivel_acorda_a_manutencao_na_hora(base, acordar):
    msg, *_ = _enviar(base, "quem foi demitido nos últimos meses?", ANUNCIOS)
    assert Objetivo.objects.get().situacao == SO.MANUTENCAO
    assert acordar.exists() and acordar.read_text()  # só um carimbo de hora


def test_chat_em_correcao_fica_ocupado_e_para(base, acordar, client):
    msg, _, _, c = _enviar(base, "quem foi demitido nos últimos meses?", ANUNCIOS)
    o = Objetivo.objects.get()
    client.force_login(base["u"])
    html = client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
    assert "data-objetivo-pendente" in html and "Corrigindo…" in html
    assert reverse("chat:parar_objetivo", args=[o.pk]) in html
    r = client.post(reverse("chat:parar_objetivo", args=[o.pk]), HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.situacao == SO.IMPEDIDO
    assert Mensagem.objects.filter(conversa=c, texto__startswith="Parei este pedido").exists()
    assert "data-objetivo-pendente" not in client.get(reverse("chat:conversa", args=[c.pk])).content.decode()
