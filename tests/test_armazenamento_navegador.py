"""Nenhum dado em armazenamento do navegador; htmx sem cache de histórico."""
import hashlib
import json
import re
from pathlib import Path

from django.conf import settings

RAIZ = Path(settings.BASE_DIR)
PROIBIDOS = re.compile(r"localStorage|sessionStorage|indexedDB|document\.cookie|caches\.open|serviceWorker")
HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"


def _arquivos_proprios():
    for pasta in (RAIZ / "templates", RAIZ / "static", RAIZ / "iativa"):
        for arquivo in pasta.rglob("*"):
            if arquivo.is_file() and "vendor" not in arquivo.parts and arquivo.suffix in {".html", ".js", ".css"}:
                yield arquivo


def test_templates_e_scripts_nao_usam_armazenamento_do_navegador():
    ocorrencias = [str(a) for a in _arquivos_proprios() if PROIBIDOS.search(a.read_text(encoding="utf-8"))]
    assert ocorrencias == []


def test_htmx_configurado_sem_historico():
    base = (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")
    bruto = re.search(r"name=\"htmx-config\" content='([^']+)'", base).group(1)
    config = json.loads(bruto)
    assert config["historyEnabled"] is False
    assert config["historyCacheSize"] == 0
    assert config["allowEval"] is False
    assert config["selfRequestsOnly"] is True


def test_htmx_servido_localmente_e_integro():
    arquivo = RAIZ / "static" / "vendor" / "htmx-2.0.4.min.js"
    assert hashlib.sha256(arquivo.read_bytes()).hexdigest() == HTMX_SHA256
    base = (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")
    assert "https://" not in base and "http://" not in base
