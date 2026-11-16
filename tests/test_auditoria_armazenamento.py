"""Etapa 7: auditoria de não armazenamento acha SQL fora do permitido e não mostra o conteúdo."""
from io import StringIO

import pytest
from django.core.management import call_command



@pytest.mark.django_db
def test_auditoria_limpa_e_com_achado(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    out = StringIO()
    call_command("auditar_armazenamento", stdout=out)
    assert "OK" in out.getvalue()

    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "app.log").write_text("SELECT E1_VALOR FROM SE1010 WHERE D_E_L_E_T_ = ' '")
    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("auditar_armazenamento", stdout=out)
    assert "ARQUIVO var/app.log" in out.getvalue() and "E1_VALOR" not in out.getvalue()
