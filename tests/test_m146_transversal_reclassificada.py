"""M146: receita de leitura transversal (quem lê todos os setores) continua executando quando a fonte é
reclassificada depois para outro setor (ex.: DA3 veículos → só "operacoes", pedido aberto na TI)."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.http import QueryDict

from iativa.accounts.models import Department
from iativa.agent import aprendizado, planejamento
from iativa.agent import orquestrador as orq
from iativa.agent.provedores import FakeProvider
from iativa.query import servico

pytestmark = pytest.mark.django_db


@pytest.fixture
def ti_total(erp_sintetico, settings, novo_usuario, politica):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    call_command("seed_exemplos", stdout=StringIO())
    ti = Department.objects.get(codigo="ti")
    u = novo_usuario("diego", ti)
    for s in Department.objects.filter(ativo=True):
        politica(u, s, consultar=True, administrar=s == ti, filiais=["01"])
    return u


def _receita(u):
    q = QueryDict(mutable=True)
    q.update({"nome": "Títulos emitidos no período", "campo_data": "E1_EMISSAO"})
    q.setlist("colunas", ["E1_NUM", "E1_CLIENTE", "E1_EMISSAO", "E1_VALOR"])
    q.setlist("setores", [str(d.pk) for d in Department.objects.filter(ativo=True)])
    cm = aprendizado.salvar_proposta(u, "SE1", q)
    aprendizado.aprovar(u, cm)
    cm.refresh_from_db()
    cm.spec = {**cm.spec, "leitura_transversal": True}
    cm.save(update_fields=["spec"])
    return cm


@pytest.mark.parametrize("transversal", [True, False])
def test_fonte_reclassificada_nao_barra_leitura_transversal(ti_total, monkeypatch, transversal):
    cm = _receita(ti_total)
    if not transversal:
        cm.spec = {k: v for k, v in cm.spec.items() if k != "leitura_transversal"}
        cm.save(update_fields=["spec"])
    r = orq.processar_mensagem(ti_total, "títulos emitidos de 01/09/2025 a 31/12/2025 filial 01", provedor=FakeProvider())
    assert r.tipo == "pronta", (r.tipo, r.mensagem)
    outro = "operacoes" if r.solicitacao.setor.codigo != "operacoes" else "ti"
    monkeypatch.setattr(planejamento, "autorizar_fonte", lambda *a, **k: {outro})  # fonte agora é de outro setor
    if not transversal:  # M147: sem acesso total (falta um setor), a regra do setor da fonte continua valendo
        from iativa.accounts.models import AccessPolicy
        AccessPolicy.objects.filter(user=ti_total).exclude(
            department__codigo__in=[r.solicitacao.setor.codigo, outro]).exclude(department__isnull=True).first().delete()
    res = servico.executar_solicitacao(ti_total, r.solicitacao)
    if transversal:
        assert res.ok, res.mensagem
    else:  # receita comum: o setor do pedido continua precisando ser o da fonte
        assert not res.ok and "não é autorizada para o setor" in res.mensagem
