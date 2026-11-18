"""M102: fonte de setor inconclusivo (ex.: CN9 contratos) lida por quem tem os MESMOS direitos em todos os setores.

Qualquer classificação daria o mesmo acesso, então ler não amplia nada: só TI/Diretoria, mesmas filiais em todos os
setores ativos, tabela sem restrição de metadado. A receita fica pessoal (outra pessoa não a vê) e o direito é
reconferido a cada execução (revogação vale na hora).
"""
import pytest

from iativa.accounts.authz import leitura_de_todos_os_setores
from iativa.accounts.models import AccessPolicy, Department
from iativa.agent import planejamento
from iativa.agent.models import ConsultaModelo, FonteSetorial
from iativa.agent.contratos import objetivos_para

pytestmark = pytest.mark.django_db


@pytest.fixture
def ti_total(setores, novo_usuario, politica):
    u = novo_usuario("ti-total", setores["ti"])
    for s in Department.objects.filter(ativo=True):
        politica(u, s, consultar=True, filiais=["01", "02"])
    return u


def test_so_quem_le_tudo_igual(ti_total, setores, novo_usuario, politica):
    assert leitura_de_todos_os_setores(ti_total, ["CN9"])
    # Falta um setor: uma classificação ampliaria o acesso -> não.
    AccessPolicy.objects.filter(user=ti_total, department=setores["vendas"]).delete()
    assert not leitura_de_todos_os_setores(ti_total, ["CN9"])
    # Filiais diferentes entre setores -> não.
    outro = novo_usuario("ti-filiais", setores["ti"])
    for s in Department.objects.filter(ativo=True):
        politica(outro, s, consultar=True, filiais=["01"] if s.codigo != "ti" else ["01", "02"])
    assert not leitura_de_todos_os_setores(outro, ["CN9"])
    # Colaborador comum, mesmo com acesso a tudo -> não.
    comum = novo_usuario("comum", setores["cobranca"])
    for s in Department.objects.filter(ativo=True):
        politica(comum, s, consultar=True, filiais=["01", "02"])
    assert not leitura_de_todos_os_setores(comum, ["CN9"])


class _Inconclusivo:
    def planejar(self, entrada):
        return {"natureza": "ambigua", "confianca": 0.6, "setores": []}


def test_classificacao_inconclusiva_vira_leitura_pessoal(ti_total, erp_sintetico, settings):
    settings.IATIVA_METADADOS = {"fonte": "sintetico"}
    from iativa.chat import conversas
    c = conversas.nova_individual(ti_total)
    transversais = set()
    setores = planejamento.autorizar_fonte(ti_total, _Inconclusivo(), "SE5", conversa=c, transversais=transversais)
    assert transversais == {"SE5"} and setores
    assert not FonteSetorial.objects.filter(tabela="SE5").exists()  # não inventa um setor para a fonte
    # Sem a conversa individual da própria pessoa (ex.: chat de setor) -> continua inconclusiva.
    with pytest.raises(planejamento.FonteInconclusiva):
        planejamento.autorizar_fonte(ti_total, _Inconclusivo(), "SE5")


def test_receita_transversal_e_pessoal(ti_total, setores, novo_usuario, politica):
    cm = ConsultaModelo.objects.create(codigo="aprendido-contratos", nome="Contratos", tabela="CN9",
                                       spec={"colunas": [], "leitura_transversal": True}, situacao="aprovada",
                                       criado_por=ti_total)
    cm.setores.add(*Department.objects.filter(ativo=True))
    outro = novo_usuario("outro-ti", setores["ti"])
    for s in Department.objects.filter(ativo=True):
        politica(outro, s, consultar=True, filiais=["01", "02"])
    assert cm.codigo in {o.codigo for o in objetivos_para(ti_total)}
    assert cm.codigo not in {o.codigo for o in objetivos_para(outro)}


def test_pedido_abre_no_setor_da_receita_em_que_a_pessoa_tem_acesso(ti_total, setores):
    """M103: receita só do Comercial (vendas) pedida por alguém da TI com acesso ao Comercial: o pedido abre em vendas."""
    from iativa.agent.contratos import OBJETIVOS
    from iativa.chat.servicos import criar_solicitacao
    cm = ConsultaModelo.objects.create(codigo="aprendido-contratos-x", nome="Contratos X", tabela="SE1",
                                       spec={"colunas": [{"campo": "E1_NUM", "rotulo": "Num", "tipo": "texto"}]},
                                       situacao="aprovada", criado_por=ti_total)
    cm.setores.add(setores["vendas"])
    assert cm.codigo in OBJETIVOS
    s = criar_solicitacao(ti_total, OBJETIVOS[cm.codigo].dominio, cm.codigo)
    assert s.setor == setores["vendas"]
