"""Cria setores e definições de parâmetros de EXEMPLO (fictícios) para o ambiente local.

A lista real de setores e perfis ainda está pendente (STATUS.md, informações 1 e 2). Estes
registros servem para testar a Etapa 2 e podem ser editados ou desativados pelo admin.
Não cria usuários nem concede permissões. Pode ser executado mais de uma vez.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from iativa.accounts.models import Department
from iativa.memory.models import ParameterDefinition, TipoValor

SETORES = (
    ("financeiro-cobranca", "Financeiro — Cobrança"),
    ("diretoria", "Diretoria"),
    ("ti", "TI"),
)

DEFINICOES = (
    {
        "codigo": "data-corte",
        "nome": "Data de corte",
        "descricao": "Data de referência para considerar um título vencido.",
        "tipo": TipoValor.DATA,
    },
    {
        "codigo": "tratamento-baixa-parcial",
        "nome": "Tratamento de baixa parcial",
        "descricao": "Como tratar títulos com baixa parcial ao calcular valores em aberto.",
        "tipo": TipoValor.ESCOLHA,
        "opcoes": ["considerar-saldo", "considerar-valor-original", "desconsiderar-titulo"],
    },
    {
        "codigo": "filial-analise",
        "nome": "Filial da análise",
        "descricao": "Filial considerada na análise, dentro das filiais autorizadas.",
        "tipo": TipoValor.FILIAL,
    },
    {
        "codigo": "dias-tolerancia-atraso",
        "nome": "Dias de tolerância de atraso",
        "descricao": "Dias após o vencimento real antes de considerar o título em atraso.",
        "tipo": TipoValor.NUMERO,
    },
    {
        "codigo": "periodo-analise",
        "nome": "Período da análise",
        "descricao": "Início e fim do período de vencimento considerado.",
        "tipo": TipoValor.PERIODO,
    },
    {
        "codigo": "cliente-especifico",
        "nome": "Cliente específico",
        "descricao": "Código e loja do cliente. Dado cadastral: só vale na solicitação.",
        "tipo": TipoValor.TEXTO,
        "contem_dado_pessoal": True,
    },
)


class Command(BaseCommand):
    help = "Cria setores e definições de parâmetros de exemplo (fictícios)."

    @transaction.atomic
    def handle(self, *args, **options):
        criados = 0
        for codigo, nome in SETORES:
            _, novo = Department.objects.get_or_create(codigo=codigo, defaults={"nome": nome})
            criados += novo
        for dados in DEFINICOES:
            dados = dict(dados)
            codigo = dados.pop("codigo")
            definicao = ParameterDefinition.objects.filter(codigo=codigo).first()
            if definicao is None:
                definicao = ParameterDefinition(codigo=codigo, **dados)
                definicao.full_clean()
                definicao.save()
                criados += 1
        # Etapa 3: o setor de cobrança (exemplo) usa o domínio de contas a receber.
        from iativa.metadata.models import DominioSetor

        cobranca = Department.objects.get(codigo="financeiro-cobranca")
        _, novo = DominioSetor.objects.get_or_create(dominio="financeiro_receber", setor=cobranca)
        criados += novo
        self.stdout.write(f"Exemplos prontos ({criados} registro(s) novo(s)).")
