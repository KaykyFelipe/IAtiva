"""Contratos de parâmetros: o que cada objetivo de consulta exige antes de executar.

Duas origens:
- objetivos fixos do Financeiro (abaixo), liberados pelo domínio `financeiro_receber`;
- consultas aprendidas e **aprovadas** (M9, `ConsultaModelo`), liberadas aos setores escolhidos.

O modelo só escolhe entre os objetivos que o usuário pode usar; os parâmetros obrigatórios vêm
daqui. Cada código precisa existir como `ParameterDefinition` ativa.
"""
import re
from dataclasses import dataclass

PREFIXO_APRENDIDO = "aprendido-"
BUSCA = "texto-busca"  # opcional: filtra pelo nome (cliente, fornecedor, funcionário...)
FILTRO = "filtro-campo"  # opcional: CAMPO=valor exato num campo filtrável da consulta aprendida
AGREGACAO = "agregacao"  # opcional: soma/média/contagem/maior/menor, por grupo, top N (consultas aprendidas)
MAX_FILTRAVEIS = 6
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
    # Opcionais: usados se a pessoa informar; nunca perguntados (ex.: busca por nome).
    opcionais: tuple[str, ...] = ()
    filtraveis: tuple[tuple[str, str], ...] = ()  # (campo, rótulo) aceitos em FILTRO

    @property
    def parametros(self) -> tuple[str, ...]:
        return self.obrigatorios + self.opcionais


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
            opcionais=(BUSCA, AGREGACAO),  # M41: aqui o cálculo aceita só "top N" (ranking dos maiores)
        ),
        Objetivo(
            codigo="titulos-a-vencer",
            dominio="financeiro_receber",
            nome="Títulos a vencer",
            descricao="Títulos a receber em aberto com vencimento dentro de um período.",
            tabelas=("SE1", "SA1"),
            obrigatorios=("periodo-analise", "tratamento-baixa-parcial", "filial-analise"),
            opcionais=(BUSCA,),
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


def campo_busca(spec: dict) -> str:
    """Campo de texto usado na busca por nome de uma consulta aprendida: o escolhido no formulário
    ou, nas antigas, a primeira coluna de texto que pareça nome (ex.: E2_NOMFOR, RA_NOME, A2_NOME)."""
    colunas = [c for c in spec.get("colunas", []) if c.get("tipo") == "texto"]
    escolhido = spec.get("campo_busca") or ""
    if escolhido and any(c["campo"] == escolhido for c in colunas):
        return escolhido
    for c in colunas:
        if re.search(r"_(NOME|NOM[A-Z]{0,4}|NREDUZ|RAZAO|DESC)$", c["campo"]):
            return c["campo"]
    return ""


def campos_filtraveis(spec: dict) -> list[tuple[str, str]]:
    """Campos que aceitam filtro exato: os escolhidos no formulário ou, se nenhum, as colunas de texto."""
    colunas = {c["campo"]: c.get("rotulo") or c["campo"] for c in spec.get("colunas", [])}
    escolhidos = [c for c in spec.get("filtraveis", []) if c in colunas]
    if not escolhidos:
        escolhidos = [c["campo"] for c in spec.get("colunas", []) if c.get("tipo") == "texto"]
    return [(c, colunas[c]) for c in escolhidos[:MAX_FILTRAVEIS]]


def de_modelo(cm) -> Objetivo:
    obrig = ["filial-analise"] + (["periodo-analise"] if cm.spec.get("campo_data") else [])
    filtraveis = tuple(campos_filtraveis(cm.spec))
    opcionais = ((BUSCA,) if campo_busca(cm.spec) else ()) + ((FILTRO,) if filtraveis else ()) + (AGREGACAO,)
    return Objetivo(
        codigo=cm.codigo, dominio=DOMINIO_APRENDIDO, nome=cm.nome, descricao=cm.descricao,
        tabelas=(cm.tabela,), obrigatorios=tuple(obrig), modelo_id=cm.pk,
        opcionais=opcionais, filtraveis=filtraveis,
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
