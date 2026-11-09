"""Filtro de redação de logs."""
import logging

import pytest

from iativa.core.logging_filters import RedactSensitiveFilter, redact


@pytest.mark.parametrize(
    "entrada, proibido",
    [
        ("falha ao executar SELECT A1_NOME FROM SA1010 WHERE A1_COD = '000001'", "A1_NOME"),
        ("erro: insert into SE1010 values (1)", "SE1010"),
        ("DRIVER={ODBC Driver 18};SERVER=srv;UID=leitura;PWD=Sup3rS3cret;", "Sup3rS3cret"),
        ("conectando em postgresql://iativa:minhaSenha@127.0.0.1/iativa", "minhaSenha"),
        ("password=abc123 token: tkn-999", "abc123"),
        ("password=abc123 token: tkn-999", "tkn-999"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ('"GET /chat/?q=clientes+inadimplentes HTTP/1.1" 200', "inadimplentes"),
    ],
)
def test_redige_conteudo_sensivel(entrada, proibido):
    assert proibido not in redact(entrada)


def test_mantem_texto_comum():
    assert redact("Servidor iniciado em 127.0.0.1:8000") == "Servidor iniciado em 127.0.0.1:8000"


def test_filtro_redige_argumentos_e_excecoes():
    registro = logging.LogRecord("t", logging.ERROR, __file__, 1, "consulta: %s", ("SELECT * FROM SE1010",), None)
    try:
        raise ValueError("PWD=segredo123 SELECT E1_SALDO FROM SE1010")
    except ValueError:
        import sys

        registro.exc_info = sys.exc_info()
    RedactSensitiveFilter().filter(registro)
    texto = logging.Formatter("%(message)s").format(registro)
    assert "SE1010" not in texto
    assert "segredo123" not in texto
    assert "[SQL REDIGIDO]" in texto


def test_handler_do_projeto_usa_o_filtro():
    from django.conf import settings

    assert "redact" in settings.LOGGING["handlers"]["console"]["filters"]
    assert settings.LOGGING["loggers"]["django.db.backends"]["handlers"] == []
