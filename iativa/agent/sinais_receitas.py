"""M95: a receita em Markdown acompanha o catálogo (gravar a consulta aprendida = atualizar o Markdown)."""
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ConsultaModelo


@receiver(post_save, sender=ConsultaModelo)
def atualizar_receita(sender, instance, **kwargs):
    from . import receitas
    transaction.on_commit(lambda: receitas.gerar(instance))
