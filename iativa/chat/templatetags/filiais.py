from django import template

from iativa.accounts import filiais

register = template.Library()


@register.filter
def rotulo_filial(valor):
    return filiais.rotulo(valor)
