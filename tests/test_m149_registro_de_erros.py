"""M149: toda resposta da IA que termina em erro fica registrada: pergunta, resposta e o que corrigir."""
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from iativa.chat import conversas, erros
from iativa.chat.models import AvaliacaoResposta, ErroResposta, Mensagem, Papel

pytestmark = pytest.mark.django_db


@pytest.fixture
def conversa(setores, novo_usuario):
    u = novo_usuario("diego", setores["ti"])
    return conversas.nova_individual(u)


def _par(c, pergunta, resposta, ficha=None, capturar=None):
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=c.usuario, texto=pergunta)
    with capturar(execute=True):
        return Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=resposta, ficha=ficha or {})


def test_resposta_com_erro_e_registrada_com_os_passos(conversa, django_capture_on_commit_callbacks):
    erros.anotar_passo(SimpleNamespace(acao="conversar", objetivo="", resposta="Você não tem permissão."),
                       [SimpleNamespace(acao="conversar", situacao="entrega_pendente", detalhe="Ainda faltam entregas")])
    m = _par(conversa, "quantos veiculos tem a empresa?",
             "Não terminei esta resposta. Tentei: responder sem executar.", capturar=django_capture_on_commit_callbacks)
    e = ErroResposta.objects.get(mensagem_ia=m)
    assert e.pergunta == "quantos veiculos tem a empresa?" and e.resposta.startswith("Não terminei")
    assert e.tipo == "nao_concluida" and "Decisões do modelo: conversar" in e.erro
    assert "Você não tem permissão." in e.erro and e.detalhe["passos"][0]["observacoes"]
    assert erros._passos.get() is None  # os passos não vazam para o próximo pedido


@pytest.mark.parametrize("texto,ficha", [
    ("A fonte não é autorizada para o setor desta solicitação.", {"tipo": "falha_execucao"}),
    ("Sem permissão de consulta ou sem filiais autorizadas neste setor.", {}),
    ("Não consegui processar sua mensagem agora.", {"tipo": "falha_modelo"}),
])
def test_tipos_de_erro(conversa, django_capture_on_commit_callbacks, texto, ficha):
    m = _par(conversa, "pergunta", texto, ficha, capturar=django_capture_on_commit_callbacks)
    assert ErroResposta.objects.filter(mensagem_ia=m).exists()


@pytest.mark.parametrize("texto,ficha", [
    ("Consulta executada às 17:02: 8 linha(s).", {"tipo": "executada"}),
    ("Qual filial você quer?", {"tipo": "texto"}),
    ("Não informo aniversários.", {"tipo": "bloqueada", "dado_protegido": "nascimento"}),
])
def test_respostas_normais_nao_sao_erro(conversa, django_capture_on_commit_callbacks, texto, ficha):
    m = _par(conversa, "pergunta", texto, ficha, capturar=django_capture_on_commit_callbacks)
    assert not ErroResposta.objects.filter(mensagem_ia=m).exists()


def test_resposta_marcada_incorreta_vira_erro(conversa, django_capture_on_commit_callbacks):
    m = _par(conversa, "quanto vendemos?", "Consulta executada.", {"tipo": "executada"},
             capturar=django_capture_on_commit_callbacks)
    AvaliacaoResposta.objects.create(mensagem=m, usuario=conversa.usuario, correta=False, comentario="filial errada")
    e = ErroResposta.objects.get(mensagem_ia=m)
    assert e.tipo == "avaliada_incorreta" and "filial errada" in e.erro and e.pergunta == "quanto vendemos?"


def test_falha_do_atendimento_registra_sem_a_mensagem_da_excecao(conversa):
    try:
        raise ValueError("dado sensível do ERP")
    except ValueError as exc:
        e = erros.registrar_falha(conversa.usuario, conversa, "pergunta x", exc)
    assert e.tipo == "excecao" and "ValueError" in e.erro and "dado sensível" not in e.erro


def test_comando_lista_e_marca(conversa, django_capture_on_commit_callbacks, capsys):
    m = _par(conversa, "pergunta y", "Não terminei esta resposta.", capturar=django_capture_on_commit_callbacks)
    e = ErroResposta.objects.get(mensagem_ia=m)
    call_command("erros_ia")
    saida = capsys.readouterr().out
    assert "pergunta y" in saida and "Corrigir:" in saida
    call_command("erros_ia", "--corrigido", str(e.pk))
    e.refresh_from_db()
    assert e.corrigido
