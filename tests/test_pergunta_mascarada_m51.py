"""M51: a pergunta vai mascarada no diagnóstico de capacidade ausente."""
import importlib.util
from pathlib import Path

import pytest

from iativa.manutencao.incidentes import diagnostico, mascarar_pergunta, registrar
from iativa.manutencao.models import Categoria


def _script():
    caminho = Path(__file__).resolve().parents[1] / "scripts" / "manutencao.py"
    spec = importlib.util.spec_from_file_location("manutencao_script_m51", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mascara_nomes_documentos_emails_e_valores():
    t = mascarar_pergunta("quantos dias úteis o João da Silva trabalhou de 01/02/2026 a 10/03/2026? "
                          "CPF 123.456.789-00, email joao@x.com, salário 4.500,00")
    assert "João" not in t and "Silva" not in t and "123.456" not in t and "joao@" not in t and "4.500" not in t
    assert "01/02/2026" in t and "10/03/2026" in t
    assert "[nome]" in t and "[numero]" in t and "[email]" in t


def test_mantem_pergunta_sem_dado_pessoal():
    assert mascarar_pergunta("quero apenas os dias úteis") == "quero apenas os dias úteis"


@pytest.mark.django_db
def test_diagnostico_leva_pergunta_mascarada_e_script_aceita():
    inc = registrar("agente", "objetivo.calculo.acao_repetida", categoria=Categoria.CAPACIDADE,
                    condicoes={"exigencia": "calculo", "trilha": ["calcular_resultado:falha:nenhuma"],
                               "pergunta": "dias úteis da Maria Souza em 2026"})
    d = diagnostico(inc)
    assert d["condicoes"]["pergunta"] == "dias úteis da [nome] em 2026"
    validado = _script().validar_diagnostico(d)
    assert validado["condicoes"]["pergunta"] == "dias úteis da [nome] em 2026"


@pytest.mark.django_db
def test_recorrencia_atualiza_pergunta():
    base = {"exigencia": "calculo", "trilha": ["calcular_resultado:falha:nenhuma"]}
    registrar("agente", "objetivo.calculo.x", categoria=Categoria.CAPACIDADE, condicoes={**base, "pergunta": "a"})
    inc = registrar("agente", "objetivo.calculo.x", categoria=Categoria.CAPACIDADE,
                    condicoes={**base, "pergunta": "dias úteis de 2026"})
    assert inc.condicoes["pergunta"] == "dias úteis de 2026"


def test_script_recusa_pergunta_fora_do_formato():
    d = {"incidente": 1, "codigo": "agente.objetivo.calculo.x", "categoria": "capacidade_ausente",
         "condicoes": {"trilha": ["a:b:c"], "pergunta": "x" * 400}}
    assert "pergunta" not in _script().validar_diagnostico(d)["condicoes"]
