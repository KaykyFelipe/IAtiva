"""Leitura determinística de períodos na mensagem ("desde janeiro", "mês passado", "em agosto de 2026").

Modelos pequenos às vezes não reenviam o período numa continuação ("faça de novo desde janeiro")
e a consulta herdaria o anterior. O backend lê a expressão e a usa quando o modelo não mandou.
"""
import calendar
import re
import unicodedata
from datetime import date

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_MES = "(" + "|".join(MESES) + r")(?:\s+(?:de\s+)?(\d{4}))?"
_DATA = r"(\d{2})/(\d{2})/(\d{4})"


def _norm(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def _ano(mes, ano_txt, hoje):
    if ano_txt:
        return int(ano_txt)
    return hoje.year if mes <= hoje.month else hoje.year - 1  # "desde dezembro" em março = ano passado


def _fim_mes(a, m):
    return date(a, m, calendar.monthrange(a, m)[1])


def _fmt(inicio, fim):
    return f"{inicio:%d/%m/%Y}..{fim:%d/%m/%Y}"


def periodo_da_mensagem(texto: str, hoje: date) -> str:
    """Período no formato do parâmetro (DD/MM/AAAA..DD/MM/AAAA) ou "" se não houver expressão clara."""
    t = _norm(texto)
    m = re.search(r"\bde\s+" + _DATA + r"\s+(?:a|ate)\s+" + _DATA, t)
    if m:
        d, mo, a, d2, mo2, a2 = map(int, m.groups())
        try:
            return _fmt(date(a, mo, d), date(a2, mo2, d2))
        except ValueError:
            return ""
    m = re.search(r"\b(?:desde|a partir de)\s+(?:o\s+dia\s+)?" + _DATA, t)
    if m:
        d, mo, a = map(int, m.groups())
        try:
            return _fmt(date(a, mo, d), hoje)
        except ValueError:
            return ""
    m = re.search(r"\b(?:desde|a partir de)\s+(?:o\s+(?:mes\s+de\s+)?)?" + _MES, t)
    if m:
        mes = MESES[m.group(1)]
        return _fmt(date(_ano(mes, m.group(2), hoje), mes, 1), hoje)
    if re.search(r"\bmes\s+passado\b|\bultimo\s+mes\b", t):
        a, mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
        return _fmt(date(a, mes, 1), _fim_mes(a, mes))
    if re.search(r"\b(?:este|esse|neste|nesse)\s+mes\b", t):
        return _fmt(hoje.replace(day=1), hoje)
    if re.search(r"\b(?:este|esse|neste|nesse)\s+ano\b", t):
        return _fmt(date(hoje.year, 1, 1), hoje)
    m = re.search(r"\b(?:em|no mes de|de)\s+" + _MES, t)
    if m:
        mes = MESES[m.group(1)]
        a = _ano(mes, m.group(2), hoje)
        return _fmt(date(a, mes, 1), min(_fim_mes(a, mes), hoje) if (a, mes) == (hoje.year, hoje.month) else _fim_mes(a, mes))
    return ""
