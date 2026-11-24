"""M145: a investigação confere os campos de junção em 2 saltos (SD2 → SF2 → SA3) na tabela de origem da junção."""
from types import SimpleNamespace

import pytest

from iativa.agent import diagnostico
from iativa.query.plano import Campo, Coluna, Filtro, Juncao, Plano


class _Pare(Exception):
    pass


def test_juncao_em_dois_saltos_confere_campo_na_intermediaria(monkeypatch):
    campos = {"SD2": {"D2_FILIAL", "D2_TOTAL", "D2_DOC", "D2_SERIE"},
              "SF2": {"F2_DOC", "F2_SERIE", "F2_VEND1"},
              "SA3": {"A3_COD", "A3_NOME"}}
    monkeypatch.setattr(diagnostico.prazo, "exigir", lambda: None)
    monkeypatch.setattr(diagnostico, "objetivos_para", lambda u: [SimpleNamespace(codigo="vendas")])
    monkeypatch.setattr(diagnostico, "tem_permissao", lambda *a: True)
    monkeypatch.setattr(diagnostico, "filiais_autorizadas", lambda *a: ["010101"])
    monkeypatch.setattr(diagnostico.Auditor, "campos",
                        lambda self, alias: (SimpleNamespace(campos=(), fisica=alias + "010"), campos[alias]))

    def parar(*a, **k):
        raise _Pare()

    monkeypatch.setattr(diagnostico, "_juncoes_sem_duplicar", parar)
    user = SimpleNamespace(pk=1)
    s = SimpleNamespace(usuario_id=1, objetivo="vendas", setor=None, setor_id=1)
    plano = Plano(objetivo="vendas", base="SD2", filial="010101")
    plano.juncoes = [Juncao("j1", "SF2", (("F2_DOC", "D2_DOC"), ("F2_SERIE", "D2_SERIE"))),
                     Juncao("j2", "SA3", (("A3_COD", "F2_VEND1"),), "j1")]
    plano.filtros = [Filtro(Campo("e1", "D2_FILIAL"), "=", "010101")]
    plano.colunas = [Coluna("Vendedor", Campo("j2", "A3_NOME")), Coluna("Total", Campo("e1", "D2_TOTAL"), "SUM")]
    with pytest.raises(_Pare):  # passou da conferência de campos e chegou à de duplicidade
        diagnostico.Auditor(user, s, None).ler(plano)
