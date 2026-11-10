"""Carga dos documentos de conhecimento (`knowledge/**/*.md`).

Cada documento começa com um cabeçalho entre linhas `---` (chave: valor). Campos obrigatórios:
titulo, dominio, origem, versao, responsavel, situacao. Com `situacao: aprovado` também são
obrigatórios aprovado_por e aprovado_em. Opcionais: setores (lista separada por vírgula; vazio =
todos os setores habilitados no domínio) e tabelas.

Só documentos aprovados e sem problemas são recuperáveis. Documentos com algo que pareça CPF,
CNPJ ou credencial são recusados inteiros (PEDIDO_INICIAL §7).
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings

from iativa.metadata.dominios import DOMINIOS

OBRIGATORIOS = ("titulo", "dominio", "origem", "versao", "responsavel", "situacao")
SITUACOES = ("rascunho", "aprovado", "obsoleto")
ORIGENS = ("sintetico", "erp", "negocio", "personalizacao")
PROIBIDOS = [
    ("cpf", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("credencial", re.compile(r"(?i)\b(senha|password|pwd|passwd|secret|token|api[_-]?key)\s*[:=]")),
    ("conexao", re.compile(r"(?i)\b(server|uid|user id)\s*=\s*[^;\s]+;")),
]
SETOR_RE = re.compile(r"^[a-z0-9-]{1,40}$")


@dataclass(frozen=True)
class Secao:
    documento: str  # caminho relativo
    titulo_documento: str
    titulo: str
    texto: str


@dataclass(frozen=True)
class Documento:
    caminho: str
    titulo: str
    dominio: str
    origem: str
    versao: str
    responsavel: str
    situacao: str
    aprovado_por: str
    aprovado_em: str
    setores: tuple[str, ...]
    tabelas: tuple[str, ...]
    secoes: tuple[Secao, ...]
    problemas: tuple[str, ...]

    @property
    def recuperavel(self) -> bool:
        return self.situacao == "aprovado" and not self.problemas


def _lista(valor: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in valor.split(",") if p.strip())


def _cabecalho(texto: str) -> tuple[dict, str] | None:
    linhas = texto.splitlines()
    if not linhas or linhas[0].strip() != "---":
        return None
    for i in range(1, len(linhas)):
        if linhas[i].strip() == "---":
            meta = {}
            for linha in linhas[1:i]:
                if ":" in linha:
                    k, v = linha.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            return meta, "\n".join(linhas[i + 1:])
    return None


def _secoes(caminho: str, titulo_doc: str, corpo: str) -> tuple[Secao, ...]:
    secoes, titulo, buffer = [], titulo_doc, []

    def fechar():
        texto = "\n".join(buffer).strip()
        if texto:
            secoes.append(Secao(caminho, titulo_doc, titulo, texto))

    for linha in corpo.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", linha)
        if m:
            fechar()
            titulo, buffer = m.group(2).strip(), []
        else:
            buffer.append(linha)
    fechar()
    return tuple(secoes)


def carregar_documento(path: Path, raiz: Path) -> Documento | None:
    """Devolve None para arquivos sem cabeçalho (ex.: README da pasta)."""
    texto = path.read_text(encoding="utf-8")
    partes = _cabecalho(texto)
    if partes is None:
        return None
    meta, corpo = partes
    caminho = path.relative_to(raiz).as_posix()
    problemas = [f"falta {c}" for c in OBRIGATORIOS if not meta.get(c)]
    situacao = meta.get("situacao", "")
    if situacao and situacao not in SITUACOES:
        problemas.append("situacao inválida")
    if meta.get("origem") and meta["origem"] not in ORIGENS:
        problemas.append("origem inválida")
    if meta.get("dominio") and meta["dominio"] not in DOMINIOS:
        problemas.append("dominio desconhecido")
    if situacao == "aprovado":
        if not meta.get("aprovado_por"):
            problemas.append("falta aprovado_por")
        try:
            date.fromisoformat(meta.get("aprovado_em", ""))
        except ValueError:
            problemas.append("aprovado_em inválido (AAAA-MM-DD)")
    setores = _lista(meta.get("setores", ""))
    if not all(SETOR_RE.match(s) for s in setores):
        problemas.append("setores inválidos")
    for nome, regex in PROIBIDOS:
        if regex.search(texto):
            problemas.append(f"conteúdo proibido: {nome}")
    titulo = meta.get("titulo", path.stem)
    return Documento(
        caminho=caminho, titulo=titulo, dominio=meta.get("dominio", ""), origem=meta.get("origem", ""),
        versao=meta.get("versao", ""), responsavel=meta.get("responsavel", ""), situacao=situacao,
        aprovado_por=meta.get("aprovado_por", ""), aprovado_em=meta.get("aprovado_em", ""),
        setores=setores, tabelas=tuple(t.upper() for t in _lista(meta.get("tabelas", ""))),
        secoes=_secoes(caminho, titulo, corpo), problemas=tuple(problemas),
    )


def carregar_todos(raiz: Path | None = None) -> list[Documento]:
    raiz = Path(raiz or settings.IATIVA_CONHECIMENTO_DIR)
    if not raiz.is_dir():
        return []
    docs = []
    for path in sorted(raiz.rglob("*.md")):
        doc = carregar_documento(path, raiz)
        if doc is not None:
            docs.append(doc)
    return docs
