"""M107: prova da publicação só no servidor (correção controlada)."""
from pathlib import Path

from django.conf import settings


def test_publicar_local_existe_e_nao_faz_push():
    script = (Path(settings.BASE_DIR) / "scripts" / "publicar_local.sh").read_text()
    assert "PUBLICADO" in script and "git push" not in script
