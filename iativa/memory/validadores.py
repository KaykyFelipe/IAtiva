"""Validação de valores de parâmetros.

Além do tipo, recusa qualquer valor que pareça CPF ou CNPJ: esses identificadores não são
persistidos em nenhum escopo (PLANO.md §4).
"""
import re
from datetime import date

from django.core.exceptions import ValidationError

from iativa.accounts.models import FILIAL_RE

from .models import TipoValor

TEXTO_MAX = 200
_CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_CNPJ_RE = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")


def contem_cpf_cnpj(texto: str) -> bool:
    return bool(_CPF_RE.search(texto) or _CNPJ_RE.search(texto))


def _textos(valor):
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        yield str(valor)
    elif isinstance(valor, dict):
        for k, v in valor.items():
            yield str(k)
            yield from _textos(v)
    elif isinstance(valor, list):
        for item in valor:
            yield from _textos(item)


def recusar_cpf_cnpj(valor) -> None:
    if any(contem_cpf_cnpj(t) for t in _textos(valor)):
        raise ValidationError("CPF e CNPJ não podem ser armazenados na memória.")


def _data(valor) -> date:
    if not isinstance(valor, str):
        raise ValidationError("Data deve estar no formato AAAA-MM-DD.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValidationError("Data inválida (use AAAA-MM-DD).") from None


def validar_valor(definicao, valor) -> None:
    tipo = definicao.tipo
    if tipo == TipoValor.DATA:
        _data(valor)
    elif tipo == TipoValor.PERIODO:
        if not isinstance(valor, dict) or set(valor) != {"inicio", "fim"}:
            raise ValidationError('Período deve ter "inicio" e "fim".')
        if _data(valor["inicio"]) > _data(valor["fim"]):
            raise ValidationError("O início do período deve ser anterior ao fim.")
    elif tipo == TipoValor.FILIAL:
        if not isinstance(valor, str) or not FILIAL_RE.match(valor):
            raise ValidationError("Código de filial inválido.")
    elif tipo == TipoValor.TEXTO:
        if not isinstance(valor, str) or not valor.strip() or len(valor) > TEXTO_MAX:
            raise ValidationError(f"Texto deve ter de 1 a {TEXTO_MAX} caracteres.")
    elif tipo == TipoValor.NUMERO:
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValidationError("Valor numérico inválido.")
    elif tipo == TipoValor.BOOLEANO:
        if not isinstance(valor, bool):
            raise ValidationError("Use verdadeiro ou falso.")
    elif tipo == TipoValor.ESCOLHA:
        if valor not in (definicao.opcoes or []):
            raise ValidationError("Opção inválida.")
    else:
        raise ValidationError("Tipo de parâmetro desconhecido.")
    recusar_cpf_cnpj(valor)
