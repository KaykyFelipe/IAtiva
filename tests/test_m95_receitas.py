"""M95: receita aprendida vira Markdown versionado no volume, reconstruível a partir do catálogo."""
import pytest
from django.http import QueryDict

from iativa.agent import aprendizado, receitas

from .test_ciclo_agente import base  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _sra(b, nome="Demissões sintéticas"):
    q = QueryDict(mutable=True)
    q.update({"nome": nome, "campo_data": "RA_DEMISSA", "descricao": "Funcionários desligados no período"})
    q.setlist("colunas", ["RA_MAT", "RA_NOME", "RA_DEMISSA"])
    q.setlist("setores", [str(b["cob"].pk)])
    return aprendizado.aprovar(b["admin"], aprendizado.salvar_proposta(b["admin"], "SRA", q))


def test_markdown_da_receita_gerado_ao_aprovar_e_reconstruivel(base, settings, tmp_path):
    settings.IATIVA_RECEITAS_DIR = tmp_path / "receitas"
    cm = _sra(base)
    atual = tmp_path / "receitas" / cm.codigo / "atual.md"
    assert atual.exists()
    texto = atual.read_text(encoding="utf-8")
    for trecho in ("Identificador", "Versão: 1", "Escopo: setorial", "Parâmetros reutilizáveis", "`periodo-analise`",
                   "Tabela principal: `SRA`", "`RA_DEMISSA`", "Prova esperada", "memória técnica"):
        assert trecho in texto, trecho
    # Nada de dados do ERP: nenhum nome sintético de funcionário nem valor.
    assert "BRUNO" not in texto.upper() and "R$" not in texto
    atual.unlink()
    assert receitas.reconstruir() >= 1 and atual.exists()
