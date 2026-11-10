"""Domínios de negócio e as entidades (aliases de tabela) que cada um pode descobrir.

Lista fechada: a descoberta nunca sai deste conjunto, mesmo que o dicionário tenha mais tabelas.
"""
DOMINIOS = {
    "financeiro_receber": {
        "nome": "Financeiro — Contas a Receber",
        "tabelas": ("SA1", "SE1", "SE5"),
    },
}
