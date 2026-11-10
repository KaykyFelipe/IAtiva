"""Executa a descoberta progressiva como um usuário, para conferência manual.

Mostra só metadados e trechos de conhecimento aprovado (nunca linhas de dados), respeitando as
permissões do usuário informado. Exemplos:

    python manage.py descobrir_metadados ana
    python manage.py descobrir_metadados ana --tabelas SE1 SA1
    python manage.py descobrir_metadados ana --buscar "baixa parcial"
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from iativa.accounts.authz import PermissaoNegada
from iativa.metadata import descoberta


class Command(BaseCommand):
    help = "Descoberta progressiva de metadados como um usuário (conferência manual)."

    def add_arguments(self, parser):
        parser.add_argument("usuario")
        parser.add_argument("--dominio", default="financeiro_receber")
        parser.add_argument("--tabelas", nargs="*", default=[])
        parser.add_argument("--buscar", default="")

    def handle(self, *args, usuario, dominio, tabelas, buscar, **options):
        user = get_user_model().objects.filter(username=usuario).first()
        if user is None:
            raise CommandError("Usuário não encontrado.")
        try:
            doms = descoberta.dominios_disponiveis(user)
            self.stdout.write("Domínios: " + (", ".join(d["codigo"] for d in doms) or "(nenhum)"))
            for t in descoberta.entidades(user, dominio):
                self.stdout.write(
                    f"  {t.alias} ({t.fisica}) {t.nome} — {t.compartilhamento}; "
                    f"exclusão lógica: {'sim' if t.exclusao_logica else 'não'}; chave: {'+'.join(t.chave)}"
                )
            if tabelas:
                for t in descoberta.detalhar(user, dominio, tabelas):
                    self.stdout.write(f"{t.alias}:")
                    for c in t.campos:
                        marca = " [personalizado]" if c.personalizado else ""
                        estado = "" if c.consultavel else f" ({c.situacao})"
                        self.stdout.write(f"    {c.nome} {c.tipo}{c.tamanho or ''} {c.titulo}{marca}{estado}")
                for r in descoberta.relacionamentos(user, dominio, tabelas):
                    self.stdout.write(
                        f"  {r.origem}.{'+'.join(r.campos_origem)} = {r.destino}.{'+'.join(r.campos_destino)}"
                    )
            if buscar:
                for tr in descoberta.conhecimento(user, dominio, buscar):
                    self.stdout.write(f"[{tr.documento} § {tr.secao} — v{tr.versao}, aprovado por {tr.aprovado_por}]")
                    self.stdout.write(tr.texto)
        except PermissaoNegada as exc:
            raise CommandError(str(exc)) from exc
