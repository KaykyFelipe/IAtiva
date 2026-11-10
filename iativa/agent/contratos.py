"""Contratos de parâmetros: o que cada objetivo de consulta exige antes de executar.

Lista fechada. O modelo só escolhe entre estes objetivos; os parâmetros obrigatórios vêm daqui,
não do modelo. Cada código precisa existir como `ParameterDefinition` ativa; se faltar, a
solicitação é bloqueada ("regra de negócio desconhecida").
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Objetivo:
    codigo: str
    dominio: str
    nome: str
    descricao: str
    tabelas: tuple[str, ...]
    obrigatorios: tuple[str, ...]


OBJETIVOS = {
    o.codigo: o
    for o in (
        Objetivo(
            codigo="titulos-em-atraso",
            dominio="financeiro_receber",
            nome="Títulos em atraso (inadimplência)",
            descricao="Títulos a receber vencidos e não pagos até uma data de corte, por cliente.",
            tabelas=("SE1", "SA1", "SE5"),
            obrigatorios=("data-corte", "dias-tolerancia-atraso", "tratamento-baixa-parcial", "filial-analise"),
        ),
        Objetivo(
            codigo="titulos-a-vencer",
            dominio="financeiro_receber",
            nome="Títulos a vencer",
            descricao="Títulos a receber em aberto com vencimento dentro de um período.",
            tabelas=("SE1", "SA1"),
            obrigatorios=("periodo-analise", "tratamento-baixa-parcial", "filial-analise"),
        ),
        Objetivo(
            codigo="posicao-cliente",
            dominio="financeiro_receber",
            nome="Posição financeira de um cliente",
            descricao="Títulos em aberto e vencidos de um cliente específico (código e loja).",
            tabelas=("SE1", "SA1", "SE5"),
            obrigatorios=("cliente-especifico", "data-corte", "tratamento-baixa-parcial", "filial-analise"),
        ),
    )
}


def objetivos_do_dominio(dominio: str) -> list[Objetivo]:
    return [o for o in OBJETIVOS.values() if o.dominio == dominio]
