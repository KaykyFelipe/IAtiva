import socket

import pytest

from iativa.synthetic_erp.generator import generate


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Os testes não podem depender de rede: qualquer conexão de socket falha."""

    def bloqueado(*args, **kwargs):
        raise RuntimeError("Acesso à rede bloqueado nos testes automatizados.")

    monkeypatch.setattr(socket.socket, "connect", bloqueado)
    monkeypatch.setattr(socket.socket, "connect_ex", bloqueado)
    monkeypatch.setattr(socket, "create_connection", bloqueado)


@pytest.fixture
def usuario(django_user_model):
    return django_user_model.objects.create_user(username="usuario.teste", password="senha-de-teste-123")


@pytest.fixture
def cliente_logado(client, usuario):
    client.force_login(usuario)
    return client


@pytest.fixture
def setores(db):
    from iativa.accounts.models import Department

    return {
        codigo: Department.objects.create(codigo=codigo, nome=codigo.title())
        for codigo in ("cobranca", "vendas", "diretoria", "ti")
    }


@pytest.fixture
def novo_usuario(db, django_user_model):
    """Cria usuário com setor. Uso: novo_usuario("ana", setores["cobranca"])."""
    from iativa.accounts.models import UserProfile

    def criar(username, setor, funcao="colaborador", **extra):
        user = django_user_model.objects.create_user(username=username, password="senha-de-teste-123", **extra)
        UserProfile.objects.create(user=user, department=setor, funcao=funcao)
        return user

    return criar


@pytest.fixture
def politica(db):
    """Concede política. Uso: politica(user, setor, consultar=True, filiais=["01"])."""
    from iativa.accounts.models import AccessPolicy

    def conceder(user, setor=None, *, filiais=None, **perms):
        campos = {f"pode_{k}": v for k, v in perms.items()}
        if campos.get("pode_consultar") and filiais is None:
            filiais = ["01"]
        return AccessPolicy.objects.create(user=user, department=setor, filiais=filiais or [], **campos)

    return conceder


@pytest.fixture
def definicoes(db):
    from iativa.memory.models import ParameterDefinition, TipoValor

    def d(codigo, tipo, **extra):
        return ParameterDefinition.objects.create(codigo=codigo, nome=codigo, tipo=tipo, **extra)

    return {
        "data_corte": d("data-corte", TipoValor.DATA),
        "baixa_parcial": d(
            "tratamento-baixa-parcial", TipoValor.ESCOLHA, opcoes=["considerar-saldo", "desconsiderar-titulo"]
        ),
        "tolerancia": d("dias-tolerancia", TipoValor.NUMERO),
        "cliente": d("cliente-especifico", TipoValor.TEXTO, contem_dado_pessoal=True),
    }


@pytest.fixture
def erp_sintetico(tmp_path, settings):
    path = tmp_path / "erp_sintetico.sqlite3"
    generate(path)
    settings.SYNTHETIC_ERP_PATH = path
    return path
