"""Memória técnica do Protheus (M95): índice local do dicionário público, versionado pelo SHA-256 do arquivo.

O Markdown (`IATIVA_MEMORIA_TECNICA`, no volume persistente compartilhado por web e worker) é REFERÊNCIA textual
para descobrir tabelas, campos e relações. Nada dele vira SQL: a estrutura é sempre confirmada no dicionário local
da instalação (`metadata.catalogo`) e só operações do compilador executam.

Índice: um SQLite por versão (`indice/<sha12>.sqlite`, com FTS5), construído num arquivo temporário e publicado com
`os.replace` (atômico). Web e worker leem o mesmo arquivo pela mesma versão. Reindexa só quando o hash muda; o hash
só é recalculado quando tamanho/mtime do Markdown mudam. Saúde e versão vão ao log de auditoria, nunca a pergunta.

Contingência (explícita): sem arquivo ou com índice quebrado, `recuperar` devolve estado "indisponivel" e nenhum
trecho; quem chama segue só com o dicionário local e registra esse estado (nunca finge que consultou o Markdown).
"""
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

OK, INDISPONIVEL, SEM_ARQUIVO = "ok", "indisponivel", "sem_arquivo"
MAX_TRECHOS = 6
MAX_TEXTO_TRECHO = 220

_TRAVA = threading.Lock()
_CACHE: dict = {"assinatura": None, "sha": "", "caminho": None}


@dataclass
class Recuperacao:
    estado: str
    versao: str = ""
    trechos: list = field(default_factory=list)  # [{alias, campo, secao, fonte, texto}]

    def resumo(self) -> dict:
        return {"estado": self.estado, "versao": self.versao, "trechos": len(self.trechos)}


def arquivo() -> Path:
    return Path(getattr(settings, "IATIVA_MEMORIA_TECNICA",
                        Path(settings.BASE_DIR) / "var" / "conhecimento-externo" / "MEMORIA-PROTHEUS-IATIVA.md"))


def _norm(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


# --- leitura do Markdown -------------------------------------------------------------------------------------

_TABELA = re.compile(r"^## ([A-Z0-9_]{2,4}) - (.+)$")
_SECAO = re.compile(r"^### ([A-Z0-9_]{2,4}) — (.+)$")
_FONTE = re.compile(r"^\[P[aá]gina de origem de [A-Z0-9_]+\]\((https?://[^)]+)\)")


def _linhas_tabela(linha: str) -> list[str] | None:
    if not linha.startswith("|") or set(linha.replace("|", "").strip()) <= {"-", " "}:
        return None
    return [c.strip() for c in linha.strip().strip("|").split("|")]


def ler(caminho: Path):
    """Gera registros (tipo, dados) do Markdown, em uma passada (arquivo de ~10 MB)."""
    alias = secao = fonte = ""
    cabecalho = None
    with open(caminho, encoding="utf-8", errors="replace") as f:
        for bruta in f:
            linha = bruta.rstrip("\n")
            m = _TABELA.match(linha)
            if m:
                alias, secao, cabecalho = m.group(1), "Tabela", None
                yield "tabela", {"alias": alias, "nome": m.group(2).strip(), "chave": "", "modo": ""}
                continue
            m = _SECAO.match(linha)
            if m and alias:
                secao, cabecalho = _norm(m.group(2)).strip(), None
                continue
            m = _FONTE.match(linha)
            if m and alias:
                fonte = m.group(1)
                yield "fonte", {"alias": alias, "fonte": fonte}
                continue
            if alias and linha.startswith("- Chave Unica:"):
                yield "chave", {"alias": alias, "chave": linha.split(":", 1)[1].strip()}
                continue
            if alias and linha.startswith("- Modo:"):
                yield "modo", {"alias": alias, "modo": linha.split(":", 1)[1].strip()}
                continue
            celulas = _linhas_tabela(linha)
            if celulas is None or not alias:
                continue
            if cabecalho is None:
                cabecalho = [_norm(c) for c in celulas]
                continue
            reg = dict(zip(cabecalho, celulas))
            if secao.startswith("campo"):
                yield "campo", {"alias": alias, "campo": reg.get("campo", ""), "titulo": reg.get("titulo", ""),
                                "termos": reg.get("descricao (termos de busca)", ""), "tipo": reg.get("tipo", ""),
                                "contexto": reg.get("contexto", ""), "tamanho": reg.get("tamanho", "")}
            elif secao.startswith("indice"):
                yield "indice", {"alias": alias, "texto": " ".join(celulas)}
            elif secao.startswith("relacion"):
                yield "relacao", {"alias": alias, "origem": reg.get("tabela origem", ""),
                                  "destino": reg.get("tabela destino", ""),
                                  "expr_origem": reg.get("expressao origem", ""),
                                  "expr_destino": reg.get("expressao destino", ""),
                                  "cardinalidade": reg.get("relacionamento", "")}


# --- índice --------------------------------------------------------------------------------------------------

def _pasta() -> Path:
    return Path(getattr(settings, "IATIVA_MEMORIA_TECNICA_INDICE", None) or arquivo().parent / "indice")


def construir(caminho: Path, sha: str) -> Path:
    """Constrói o índice num temporário e publica com os.replace (atômico; web e worker veem a mesma versão)."""
    destino = _pasta() / f"{sha[:12]}.sqlite"
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    inicio = time.monotonic()
    con = sqlite3.connect(tmp)
    try:
        con.executescript("""
            CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT);
            CREATE TABLE tabelas (alias TEXT PRIMARY KEY, nome TEXT, chave TEXT, modo TEXT, fonte TEXT);
            CREATE TABLE campos (alias TEXT, campo TEXT, titulo TEXT, termos TEXT, tipo TEXT, contexto TEXT,
                                 tamanho TEXT);
            CREATE TABLE relacoes (alias TEXT, origem TEXT, destino TEXT, expr_origem TEXT, expr_destino TEXT,
                                   cardinalidade TEXT);
            CREATE VIRTUAL TABLE busca USING fts5(alias UNINDEXED, campo UNINDEXED, secao UNINDEXED, texto,
                                                  tokenize = 'unicode61 remove_diacritics 2');
        """)
        n = {"tabela": 0, "campo": 0, "relacao": 0, "indice": 0}
        tabelas = {}
        for tipo, d in ler(caminho):
            if tipo == "tabela":
                tabelas[d["alias"]] = d
                n["tabela"] += 1
                con.execute("INSERT INTO busca VALUES (?,?,?,?)", (d["alias"], "", "tabela", f"{d['alias']} {d['nome']}"))
            elif tipo in ("fonte", "chave", "modo"):
                tabelas[d["alias"]][tipo] = d[tipo]
            elif tipo == "campo":
                n["campo"] += 1
                con.execute("INSERT INTO campos VALUES (?,?,?,?,?,?,?)", tuple(d[k] for k in (
                    "alias", "campo", "titulo", "termos", "tipo", "contexto", "tamanho")))
                con.execute("INSERT INTO busca VALUES (?,?,?,?)", (d["alias"], d["campo"], "campo",
                                                                    f"{d['campo']} {d['titulo']} {d['termos']}"))
            elif tipo == "relacao":
                n["relacao"] += 1
                con.execute("INSERT INTO relacoes VALUES (?,?,?,?,?,?)", tuple(d[k] for k in (
                    "alias", "origem", "destino", "expr_origem", "expr_destino", "cardinalidade")))
            elif tipo == "indice":
                n["indice"] += 1
        con.executemany("INSERT INTO tabelas VALUES (?,?,?,?,?)", [
            (t["alias"], t["nome"], t.get("chave", ""), t.get("modo", ""), t.get("fonte", "")) for t in tabelas.values()])
        con.execute("CREATE INDEX campos_alias ON campos(alias, campo)")
        con.execute("CREATE INDEX relacoes_alias ON relacoes(alias)")
        con.executemany("INSERT INTO meta VALUES (?,?)", [("sha", sha), ("construido_em", time.strftime("%Y-%m-%dT%H:%M:%S"))]
                        + [(f"n_{k}", str(v)) for k, v in n.items()])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, destino)
    _auditar("indexado", sha, n=n["tabela"], ms=int((time.monotonic() - inicio) * 1000))
    return destino


def _auditar(estado, sha, **extra):
    """Só versão, saúde e contagens; nunca a pergunta nem dados do ERP."""
    logger.info("memoria_tecnica %s versao=%s %s", estado, sha[:12], " ".join(f"{k}={v}" for k, v in extra.items()))
    try:
        from iativa.audit.servicos import registrar
        registrar("metadado_consultado", "memoria_tecnica", estado_novo=f"{estado}:{sha[:12]}"[:40],
                  linhas=int(extra.get("n", 0)))
    except Exception:
        pass


def indice() -> tuple[str, Path | None, str]:
    """(estado, caminho do índice, sha). Reindexa só quando o arquivo muda (tamanho/mtime, depois hash)."""
    caminho = arquivo()
    try:
        st = caminho.stat()
    except OSError:
        return SEM_ARQUIVO, None, ""
    assinatura = (str(caminho), st.st_size, st.st_mtime_ns)
    with _TRAVA:
        if _CACHE["assinatura"] == assinatura and _CACHE["caminho"] is not None and _CACHE["caminho"].exists():
            return OK, _CACHE["caminho"], _CACHE["sha"]
        try:
            sha = _sha(caminho)
            destino = construir(caminho, sha)
        except Exception as exc:
            logger.warning("memoria_tecnica indisponivel: %s", type(exc).__name__)
            return INDISPONIVEL, None, ""
        _CACHE.update(assinatura=assinatura, sha=sha, caminho=destino)
        return OK, destino, sha


def saude() -> dict:
    estado, caminho, sha = indice()
    out = {"estado": estado, "versao": sha[:12], "arquivo": str(arquivo())}
    if caminho is not None:
        with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as con:
            out.update(dict(con.execute("SELECT chave, valor FROM meta").fetchall()))
    return out


# --- recuperação ---------------------------------------------------------------------------------------------

_VAZIAS = {"qual", "quais", "foram", "foi", "sobre", "para", "pelo", "pela", "como", "quantos", "quantas",
           "liste", "mostre", "esse", "este", "desse", "deste", "mes", "ano", "dia", "todos", "todas", "tem",
           "uma", "das", "dos", "com", "sem", "que", "por", "ultimas", "ultimos", "ultima", "ultimo"}


def _termos(texto: str) -> list[str]:
    palavras = [w for w in re.findall(r"[a-z0-9_]{3,}", _norm(texto)) if w not in _VAZIAS]
    return list(dict.fromkeys(palavras))[:12]


def recuperar(texto: str = "", aliases=(), limite: int = MAX_TRECHOS, permitidos=None) -> Recuperacao:
    """Poucos trechos pertinentes (alias, campo, seção, fonte). `aliases`: busca direta pela tabela (usa o índice).
    `permitidos`: aliases autorizados; o que estiver fora não é devolvido (nem vai ao modelo)."""
    estado, caminho, sha = indice()
    if estado != OK:
        return Recuperacao(estado)
    trechos = []
    try:
        with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as con:
            fontes = dict(con.execute("SELECT alias, fonte FROM tabelas").fetchall()) if aliases or texto else {}
            for alias in list(dict.fromkeys(a.upper() for a in aliases))[:4]:
                t = con.execute("SELECT alias, nome, chave, modo FROM tabelas WHERE alias = ?", (alias,)).fetchone()
                if t is None:
                    trechos.append({"alias": alias, "campo": "", "secao": "ausente", "fonte": "",
                                    "texto": "Tabela fora da referência pública: confirmar só no dicionário local."})
                    continue
                trechos.append({"alias": alias, "campo": "", "secao": "tabela", "fonte": fontes.get(alias, ""),
                                "texto": f"{t[1]}; chave única: {t[2] or '-'}; modo: {t[3] or '-'}"[:MAX_TEXTO_TRECHO]})
            termos = _termos(texto)
            if termos:
                consulta = " OR ".join(f'"{t}"*' for t in termos)
                for alias, campo, secao, txt in con.execute(
                        "SELECT alias, campo, secao, texto FROM busca WHERE busca MATCH ? ORDER BY rank LIMIT ?",
                        (consulta, limite * 4)):
                    if permitidos is not None and alias not in permitidos:
                        continue
                    trechos.append({"alias": alias, "campo": campo, "secao": secao, "fonte": fontes.get(alias, ""),
                                    "texto": txt[:MAX_TEXTO_TRECHO]})
    except sqlite3.Error as exc:
        logger.warning("memoria_tecnica falha na busca: %s", type(exc).__name__)
        return Recuperacao(INDISPONIVEL, sha[:12])
    vistos, out = set(), []
    for t in trechos:
        chave = (t["alias"], t["campo"], t["secao"])
        if chave not in vistos and (permitidos is None or t["alias"] in permitidos or t["secao"] == "ausente"):
            vistos.add(chave)
            out.append(t)
    return Recuperacao(OK, sha[:12], out[:limite])


def tabelas_candidatas(texto: str, limite: int = 8) -> list[tuple[str, int]]:
    """Aliases citados pelos termos do pedido, por relevância (para a descoberta; confirmar no catálogo local)."""
    r = recuperar(texto, limite=limite * 3)
    pontos: dict[str, int] = {}
    for i, t in enumerate(r.trechos):
        pontos[t["alias"]] = pontos.get(t["alias"], 0) + max(1, 10 - i)
    return sorted(pontos.items(), key=lambda x: -x[1])[:limite]


def relacoes(alias: str) -> list[dict]:
    """Relacionamentos publicados (referência). Nunca entram como SQL: a junção é validada no catálogo local."""
    estado, caminho, _ = indice()
    if estado != OK:
        return []
    with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as con:
        cols = ("origem", "destino", "expr_origem", "expr_destino", "cardinalidade")
        return [dict(zip(cols, r)) for r in con.execute(
            "SELECT origem, destino, expr_origem, expr_destino, cardinalidade FROM relacoes WHERE alias = ?",
            (alias.upper(),))]
