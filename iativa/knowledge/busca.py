"""Recuperação de trechos relevantes (BM25 simples, local, sem rede e sem IA).

Devolve só as seções mais relevantes, com limite de quantidade e de tamanho, para nunca levar
a base inteira ao contexto do modelo.
"""
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .documentos import Documento, Secao

STOPWORDS = frozenset(
    "a o as os de da do das dos e em no na nos nas um uma uns umas por para com sem que se ao aos "
    "é ser foi são como mais ou quando qual quais quem onde sobre entre até".split()
)
MAX_TRECHOS = 5
MAX_CARACTERES = 1200


def normalizar(texto: str) -> list[str]:
    sem_acento = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9_]+", sem_acento)
    out = []
    for t in tokens:
        if t in STOPWORDS or len(t) < 2:
            continue
        if len(t) > 4 and t.endswith("s"):
            t = t[:-1]  # plural simples
        out.append(t)
    return out


@dataclass(frozen=True)
class Trecho:
    documento: str
    titulo_documento: str
    secao: str
    texto: str
    versao: str
    aprovado_por: str
    aprovado_em: str
    pontuacao: float


def buscar(consulta: str, documentos: list[Documento], limite: int = 3) -> list[Trecho]:
    termos = normalizar(consulta)
    limite = max(1, min(limite, MAX_TRECHOS))
    secoes: list[tuple[Documento, Secao, Counter]] = [
        (d, s, Counter(normalizar(f"{d.titulo} {s.titulo} {s.texto} {' '.join(d.tabelas)}")))
        for d in documentos
        for s in d.secoes
    ]
    if not termos or not secoes:
        return []
    n = len(secoes)
    media = sum(sum(c.values()) for _, _, c in secoes) / n
    df = Counter(t for _, _, c in secoes for t in set(c))
    k1, b = 1.5, 0.75
    pontuados = []
    for doc, secao, contagem in secoes:
        tamanho = sum(contagem.values())
        score = 0.0
        for t in set(termos):
            tf = contagem.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * tamanho / media))
        if score > 0:
            pontuados.append((score, doc, secao))
    pontuados.sort(key=lambda x: (-x[0], x[1].caminho, x[2].titulo))
    return [
        Trecho(
            documento=doc.caminho, titulo_documento=doc.titulo, secao=secao.titulo,
            texto=secao.texto[:MAX_CARACTERES], versao=doc.versao, aprovado_por=doc.aprovado_por,
            aprovado_em=doc.aprovado_em, pontuacao=round(score, 3),
        )
        for score, doc, secao in pontuados[:limite]
    ]
