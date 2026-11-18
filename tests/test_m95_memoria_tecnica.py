"""M95: índice local da memória técnica (versão por hash, busca, contingência)."""
import shutil

import pytest

from iativa.knowledge import memoria_tecnica as mt


@pytest.fixture
def isolado(settings, tmp_path):
    origem = tmp_path / "memoria.md"
    shutil.copy(settings.IATIVA_MEMORIA_TECNICA, origem)
    settings.IATIVA_MEMORIA_TECNICA = origem
    settings.IATIVA_MEMORIA_TECNICA_INDICE = tmp_path / "indice"
    mt._CACHE.update(assinatura=None, sha="", caminho=None)
    return origem


def test_indexa_uma_vez_por_versao_e_busca_por_termos_e_alias(isolado):
    estado, caminho, sha = mt.indice()
    assert estado == mt.OK and caminho.name == f"{sha[:12]}.sqlite"
    assert mt.indice()[1] == caminho  # mesma versão: não reindexa
    r = mt.recuperar("funcionários desligados (demissão)")
    assert r.estado == mt.OK and r.versao == sha[:12]
    assert any(t["alias"] == "SRA" and t["campo"] == "RA_DEMISSA" for t in r.trechos)
    assert all({"alias", "campo", "secao", "fonte", "texto"} <= set(t) for t in r.trechos)
    assert len(r.trechos) <= mt.MAX_TRECHOS
    por_alias = mt.recuperar(aliases=["SE1"])
    assert por_alias.trechos[0]["secao"] == "tabela" and "E1_FILIAL" in por_alias.trechos[0]["texto"]
    assert mt.relacoes("SA1")[0]["cardinalidade"] == "1 para N"


def test_tabela_fora_da_referencia_publica_e_registrada(isolado):
    r = mt.recuperar(aliases=["SE5"])  # existe no dicionário local sintético, não no Markdown
    assert r.trechos[0]["secao"] == "ausente"


def test_arquivo_mudou_gera_nova_versao(isolado):
    v1 = mt.indice()[2]
    with open(isolado, "a", encoding="utf-8") as f:
        f.write("\n## SZ9 - Tabela nova\n")
    v2 = mt.indice()[2]
    assert v1 != v2 and (mt._pasta() / f"{v2[:12]}.sqlite").exists()


def test_sem_arquivo_ou_indice_quebrado_e_contingencia_explicita(isolado, settings, tmp_path):
    settings.IATIVA_MEMORIA_TECNICA = tmp_path / "nao-existe.md"
    mt._CACHE.update(assinatura=None, sha="", caminho=None)
    r = mt.recuperar("clientes")
    assert r.estado == mt.SEM_ARQUIVO and r.trechos == []


def test_escopo_filtra_trechos(isolado):
    r = mt.recuperar("cliente nome titulo", permitidos={"SE1"})
    assert r.trechos and all(t["alias"] == "SE1" for t in r.trechos)


def test_candidatas_priorizam_tabela_que_cobre_mais_termos(isolado):
    assert mt.tabelas_candidatas("funcionários desligados demissão")[0][0] == "SRA"
    assert mt.tabelas_candidatas("clientes bloqueados")[0][0] == "SA1"
