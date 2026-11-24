"""M140: código acompanhado do nome do cadastro (regra geral, pelo SX9)."""
import pytest

from .test_ranking_filiais import _rodar, ana  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def test_ranking_por_codigo_traz_o_nome_ao_lado(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 3")
    assert res.ok, msg.texto
    i = res.colunas.index("Nome (Cliente)")
    assert res.colunas[i - 1] == "Cliente"
    assert all(l[i].strip() for l in res.linhas)  # todo cliente exibido tem nome
    assert f"{res.linhas[0][i - 1]} - {res.linhas[0][i]}" in res.mensagem  # o destaque diz código e nome
    assert "SA1" in res.escopo["campos"] and "A1_NOME" in res.escopo["campos"]["SA1"]
    assert any("cadastro SA1" in p for p in res.premissas)


def test_por_filial_insere_depois_da_coluna_filial(ana):
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE", filial="01")
    assert res.ok, msg.texto
    i = res.colunas.index("Nome (Cliente)")
    assert res.colunas[i - 1] == "Cliente" and len(res.linhas[0]) == len(res.colunas)


def test_falha_na_busca_do_nome_nao_derruba_a_resposta(ana, monkeypatch):
    from iativa.query import nomes

    def falha(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(nomes, "_buscar", falha)
    msg, res = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 3")
    assert res.ok and "Nome (Cliente)" not in res.colunas


def test_valor_e_total_nao_mudam_com_o_nome(ana, monkeypatch):
    from iativa.query import nomes

    _, com = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 3")
    monkeypatch.setattr(nomes, "acrescentar_nomes", lambda *a, **k: [])
    _, sem = _rodar(ana, "soma:E1_VALOR por E1_CLIENTE top 3")
    assert [l[0] for l in com.linhas] == [l[0] for l in sem.linhas]
    assert [l[-1] for l in com.linhas] == [l[-1] for l in sem.linhas]
    assert com.total == sem.total
