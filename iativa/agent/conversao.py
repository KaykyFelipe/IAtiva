"""Converte valores em texto (vindos do modelo ou de formulário) para o tipo da definição.

A validação final continua em `memory.validadores` ao registrar o valor.
"""
import re
from datetime import date, datetime

from django.core.exceptions import ValidationError

from iativa.memory.models import TipoValor

SEPARADOR_PERIODO = ".."


def _data(texto: str) -> str:
    texto = texto.strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    raise ValidationError("Data inválida (use DD/MM/AAAA).")


def converter(definicao, texto) -> object:
    if not isinstance(texto, str):
        raise ValidationError("Valor inválido.")
    texto = texto.strip()
    if not texto:
        raise ValidationError("Informe um valor.")
    tipo = definicao.tipo
    if tipo == TipoValor.DATA:
        return _data(texto)
    if tipo == TipoValor.PERIODO:
        partes = texto.split(SEPARADOR_PERIODO)
        if len(partes) != 2:
            raise ValidationError("Período inválido (informe início e fim).")
        return {"inicio": _data(partes[0]), "fim": _data(partes[1])}
    if tipo == TipoValor.NUMERO:
        t = texto.replace(",", ".")
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        if re.fullmatch(r"-?\d+\.\d+", t):
            return float(t)
        raise ValidationError("Número inválido.")
    if tipo == TipoValor.BOOLEANO:
        t = texto.lower()
        if t in ("sim", "s", "true", "verdadeiro"):
            return True
        if t in ("nao", "não", "n", "false", "falso"):
            return False
        raise ValidationError("Responda sim ou não.")
    if tipo == TipoValor.FILIAL:
        from iativa.accounts.filiais import codigo_por_texto

        return codigo_por_texto(texto) or texto  # nome da filial vira o código
    return texto


def exibir(valor) -> str:
    """Formato curto para mostrar ao usuário."""
    if isinstance(valor, dict) and set(valor) == {"inicio", "fim"}:
        return f"{exibir(valor['inicio'])} a {exibir(valor['fim'])}"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        return date.fromisoformat(valor).strftime("%d/%m/%Y")
    return str(valor)
