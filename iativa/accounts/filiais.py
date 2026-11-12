"""Nomes das filiais da empresa (código do Protheus -> cidade).

Só estrutura da empresa: nenhum dado de movimento. Usado para mostrar o nome ao lado do código
e para entender a filial escrita por nome ("filial Cuiabá" -> 001002).
"""
import re
import unicodedata

TODAS = "TODAS"  # valor especial: todas as filiais autorizadas da pessoa

NOMES = {
    "001001": "Matriz",
    "001002": "Cuiabá",
    "001003": "Uberlândia",
    "001004": "Limeira",
    "001005": "Campo Grande",
    "001006": "Araçatuba",
    "001007": "Londrina",
    "001008": "Ribeirão Preto",
    "002001": "Eficaz Londrina Matriz",
    "002002": "Eficaz Araçatuba",
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t).split())


def nome(codigo: str) -> str:
    return NOMES.get(codigo, "")


def rotulo(codigo) -> str:
    """'001002 · Cuiabá'; código desconhecido volta como está."""
    if codigo == TODAS:
        return "Todas as filiais"
    n = NOMES.get(str(codigo), "")
    return f"{codigo} · {n}" if n else str(codigo)


def codigo_por_texto(texto: str) -> str | None:
    """Código da filial a partir de código, nome ou rótulo ('Cuiabá', 'filial cuiaba', '001002 · Cuiabá')."""
    t = _norm(texto)
    if not t:
        return None
    if re.fullmatch(r"(?:em |de |na |nas )?(?:todas|todas as filiais|todas filiais|todas elas|geral|tudo|todas as unidades)", t):
        return TODAS
    if m := re.fullmatch(r"(?:filial )?(\d{6})(?: .*)?", t):
        return m.group(1) if m.group(1) in NOMES else None
    t = re.sub(r"^(?:a |da |de |na |em )?(?:filial )?(?:de |da |em )?", "", t)
    return next((c for c, n in NOMES.items() if _norm(n) == t), None)
