"""Contratos de parâmetros: o que cada objetivo de consulta exige antes de executar.

Duas origens:
- objetivos fixos do Financeiro (abaixo), liberados pelo domínio `financeiro_receber`;
- consultas aprendidas e **aprovadas** (M9, `ConsultaModelo`), liberadas aos setores escolhidos.

O modelo só escolhe entre os objetivos que o usuário pode usar; os parâmetros obrigatórios vêm
daqui. Cada código precisa existir como `ParameterDefinition` ativa.
"""
from dataclasses import dataclass

PREFIXO_APRENDIDO = "aprendido-"
DOMINIO_APRENDIDO = "aprendido"


@dataclass(frozen=True)
class Objetivo:
    codigo: str
    dominio: str
    nome: str
    descricao: str
    tabelas: tuple[str, ...]
    obrigatorios: tuple[str, ...]
    modelo_id: int | None = None


FIXOS = {
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


def de_modelo(cm) -> Objetivo:
    obrig = ["filial-analise"] + (["periodo-analise"] if cm.spec.get("campo_data") else [])
    return Objetivo(
        codigo=cm.codigo, dominio=DOMINIO_APRENDIDO, nome=cm.nome, descricao=cm.descricao,
        tabelas=(cm.tabela,), obrigatorios=tuple(obrig), modelo_id=cm.pk,
    )


class _Objetivos(dict):
    """Fixos + aprendidos aprovados, com a mesma interface de dicionário."""

    def _modelo(self, codigo):
        from .models import ConsultaModelo, SituacaoModelo

        if not str(codigo).startswith(PREFIXO_APRENDIDO):
            return None
        cm = ConsultaModelo.objects.filter(codigo=codigo, situacao=SituacaoModelo.APROVADA).first()
        return de_modelo(cm) if cm else None

    def __getitem__(self, codigo):
        if dict.__contains__(self, codigo):
            return dict.__getitem__(self, codigo)
        obj = self._modelo(codigo)
        if obj is None:
            raise KeyError(codigo)
        return obj

    def __contains__(self, codigo):
        return dict.__contains__(self, codigo) or self._modelo(codigo) is not None


OBJETIVOS = _Objetivos(FIXOS)


def objetivos_do_dominio(dominio: str) -> list[Objetivo]:
    return [o for o in FIXOS.values() if o.dominio == dominio]


def objetivos_para(user) -> list[Objetivo]:
    """Objetivos que o usuário pode usar: fixos (domínio liberado ao setor) + aprendidos aprovados
    para algum setor em que ele tem "consultar"."""
    from iativa.accounts.authz import Permissao, setores_autorizados
    from iativa.metadata.descoberta import setores_do_dominio

    from .models import ConsultaModelo, SituacaoModelo

    out = list(FIXOS.values()) if setores_do_dominio(user, "financeiro_receber") else []
    setores = setores_autorizados(user, Permissao.CONSULTAR)
    if setores:
        modelos = ConsultaModelo.objects.filter(situacao=SituacaoModelo.APROVADA, setores__in=setores).distinct()
        out += [de_modelo(cm) for cm in modelos]
    return out
