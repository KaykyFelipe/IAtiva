"""M147: quem tem o cargo T.I com acesso a todos os setores não fica preso ao setor em que a pergunta foi
classificada (ex.: veículos → "operacoes" com o pedido aberto na TI). Os demais continuam limitados."""
import pytest

from iativa.accounts.authz import Permissao, acesso_total, filiais_autorizadas, limitar_consulta, tem_permissao
from iativa.accounts.models import AccessPolicy, Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def ti_total(setores, novo_usuario, politica):
    u = novo_usuario("ti-total", setores["ti"])
    for s in Department.objects.filter(ativo=True):
        politica(u, s, consultar=True, filiais=["01", "02"])
    return u


def test_ti_total_nao_e_limitado_pelo_setor_da_pergunta(ti_total, setores):
    assert acesso_total(ti_total)
    with limitar_consulta(ti_total, [setores["vendas"].pk]):
        assert tem_permissao(ti_total, Permissao.CONSULTAR, setores["ti"])
        assert filiais_autorizadas(ti_total, setores["ti"]) == {"01", "02"}


def test_sem_acesso_total_continua_limitado(ti_total, setores):
    AccessPolicy.objects.filter(user=ti_total, department=setores["vendas"]).delete()
    assert not acesso_total(ti_total)
    with limitar_consulta(ti_total, [setores["cobranca"].pk]):
        assert not tem_permissao(ti_total, Permissao.CONSULTAR, setores["ti"])
        assert tem_permissao(ti_total, Permissao.CONSULTAR, setores["cobranca"])
