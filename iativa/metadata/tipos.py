"""Estruturas de metadados devolvidas pelas fontes e pelo catálogo (só descrição, nunca dados)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColunaFisica:
    nome: str
    tipo: str


@dataclass(frozen=True)
class CampoDicionario:
    tabela: str  # alias (ex.: SE1)
    nome: str
    tipo: str  # C, N, D, L, M
    tamanho: int
    decimal: int
    titulo: str
    descricao: str
    personalizado: bool  # X3_PROPRI = 'U' (marca explícita do dicionário, não o prefixo)
    virtual: bool  # X3_CONTEXT = 'V'


@dataclass(frozen=True)
class TabelaDicionario:
    alias: str
    fisica: str
    nome: str
    modo: str  # 'C' compartilhado, 'E' exclusivo


@dataclass(frozen=True)
class Relacionamento:
    origem: str
    destino: str
    campos_origem: tuple[str, ...]
    campos_destino: tuple[str, ...]


@dataclass(frozen=True)
class Campo:
    nome: str
    tipo: str
    tamanho: int | None
    decimal: int | None
    titulo: str
    descricao: str
    personalizado: bool
    consultavel: bool
    situacao: str  # ok | virtual | sem_coluna_fisica | sem_dicionario


@dataclass(frozen=True)
class Tabela:
    alias: str
    fisica: str
    nome: str
    compartilhamento: str  # compartilhado | exclusivo | desconhecido
    exclusao_logica: bool
    campo_filial: str | None
    chave: tuple[str, ...]
    campos: tuple[Campo, ...] = field(default_factory=tuple)
