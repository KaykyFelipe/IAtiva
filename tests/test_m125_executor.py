"""M125: falha do executor (ambiente) separada da falha da correção; tempo limite com o nome da etapa."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from iativa.manutencao import incidentes
from iativa.manutencao.models import Incidente, SituacaoIncidente as SI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import manutencao as mx  # noqa: E402


@pytest.mark.parametrize("erro,causa", [
    ("Error: not logged in. Run codex login", "login"),
    ("stream error: 429 Too Many Requests: usage limit reached", "cota"),
    ("error sending request: could not resolve host", "conexão"),
    ("Unable to find image 'iativa-app:x' locally", "imagem"),
])
def test_diagnostico_especifico_do_executor(erro, causa, tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "TRABALHO", tmp_path)  # o log do executor vai para a pasta de trabalho
    with pytest.raises(mx.FalhaExecutor, match=causa):
        mx.validar_executor(SimpleNamespace(returncode=1, stderr=erro))


def test_tempo_limite_diz_a_etapa_e_mede_cada_uma():
    cr = mx.Cronometro()

    def lento():
        raise subprocess.TimeoutExpired(["docker"], 600)
    with pytest.raises(mx.Recusa, match="tempo limite na etapa ensaio \(600s\)"):
        cr.etapa("ensaio", lento)
    assert cr.etapa("teste_novo", lambda: True) is True
    assert set(cr.tempos) == {"ensaio", "teste_novo"} and "ensaio 0s" in cr.resumo()


def _corrigir(tmp_path, monkeypatch, saida_agente, alterou):
    monkeypatch.setattr(mx, "TRABALHO", tmp_path)
    monkeypatch.setattr(mx, "imagem_ativa", lambda: "img")
    monkeypatch.setattr(mx, "git", lambda *a, **kw: None)
    monkeypatch.setattr(mx, "preparar_escrita", lambda wt: None)
    monkeypatch.setattr(mx, "checar_cancelamento", lambda inc: None)
    monkeypatch.setattr(mx, "etapa", lambda *a: None)
    monkeypatch.setattr(mx, "manage", lambda *a, **k: "falha: reproduzido" if a[0] == "ensaiar" else "ok")
    monkeypatch.setattr(mx, "pytest_em", lambda *a, **k: True)
    monkeypatch.setattr(mx, "comando_agente", lambda *a: [])
    monkeypatch.setattr(mx, "alterou", lambda wt: alterou)
    monkeypatch.setattr(mx, "rodar_agente", saida_agente)
    monkeypatch.setattr(mx, "ensaiar_pedido", lambda *a: "")
    monkeypatch.setattr(mx, "publicar_no_servidor", lambda *a: "publicado_no_servidor")
    validadas = []
    monkeypatch.setattr(mx, "validar_correcao", lambda *a: validadas.append(1) or ["tests/t.py::test_x"])
    r = mx.corrigir({"incidente": 24, "codigo": "t", "versao": ""},
                    {"MANUTENCAO_TEMPO_S": "240", "MANUTENCAO_PUBLICAR": "servidor", "MANUTENCAO_PROVEDOR": "chatgpt"})
    return r, validadas


def test_agente_sem_alteracao_e_falha_do_executor_sem_gastar_tentativas(tmp_path, monkeypatch):
    def agente(*a):
        raise subprocess.TimeoutExpired(["docker"], 240)
    with pytest.raises(mx.FalhaExecutor, match="não alterou nada"):
        _corrigir(tmp_path, monkeypatch, agente, alterou=False)


def test_agente_que_editou_e_estourou_o_tempo_tem_a_edicao_validada(tmp_path, monkeypatch):
    def agente(*a):
        raise subprocess.TimeoutExpired(["docker"], 240)
    assert _corrigir(tmp_path, monkeypatch, agente, alterou=True) == ("publicado_no_servidor", [1])


def test_executor_com_erro_mas_que_editou_tambem_e_validado(tmp_path, monkeypatch):
    r = _corrigir(tmp_path, monkeypatch, lambda *a: SimpleNamespace(returncode=1, stderr="max turns"), alterou=True)
    assert r == ("publicado_no_servidor", [1])


@pytest.mark.django_db
def test_aguardar_devolve_a_fila_sem_gastar_tentativa_e_espera_o_executor(monkeypatch):
    monkeypatch.setattr(incidentes, "automatica", lambda: True)
    inc = Incidente.objects.create(assinatura="a" * 64, categoria="capacidade_ausente", codigo="x.y.z",
                                   componente="agente", operacao="op", versao="v", correlacao="c",
                                   condicoes={"trilha": ["a:b:c"]})
    call_command("manutencao", "iniciar", inc.pk)
    call_command("manutencao", "aguardar", inc.pk, "--motivo", "executor: provedor sem cota ou sobrecarregado")
    inc.refresh_from_db()
    assert inc.situacao == SI.ABERTO and inc.tentativas == 0
    assert inc.condicoes["executor_falha"].startswith("executor: provedor sem cota")
    assert not incidentes.elegivel(inc)  # ainda no intervalo de recuperação
    inc.condicoes = {**inc.condicoes, "executor_falha_em": inc.condicoes["executor_falha_em"] - 3600}
    assert incidentes.elegivel(inc)  # depois do intervalo, retoma sozinho


@pytest.mark.django_db
def test_executor_fora_muitas_vezes_vai_para_a_ti():
    inc = Incidente.objects.create(assinatura="b" * 64, categoria="capacidade_ausente", codigo="x.y.w",
                                   componente="agente", operacao="op", versao="v", correlacao="c",
                                   condicoes={"trilha": ["a:b:c"], "executor_falhas": incidentes.EXECUTOR_MAX_FALHAS - 1})
    call_command("manutencao", "aguardar", inc.pk, "--motivo", "executor: login expirado")
    inc.refresh_from_db()
    assert inc.situacao == SI.BLOQUEADO and "indisponível" in inc.motivo
