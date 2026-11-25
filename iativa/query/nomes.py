"""M140: código acompanhado do nome. Regra geral para qualquer tabela.

Toda coluna da resposta que guarda um código ligado a um cadastro pelo dicionário (SX9) ganha, ao lado,
o nome desse cadastro. Exemplos: C7_FORNECE → SA2.A2_NOME, E1_CLIENTE → SA1.A1_NOME, B1_COD → SB1.B1_DESC.

- A busca roda depois da consulta principal, só para os códigos exibidos. Ela não junta tabelas na
  consulta principal, então não pode inflar nem cortar contagens e somas.
- É uma consulta somente leitura ao cadastro, pelo mesmo compilador e validador. O nome segue a
  visibilidade do setor.
- Se a busca falhar, a resposta sai só com o código, como antes.
"""
import logging
import re

from .compilador import compilar, xfilial
from .executor import executar
from .plano import Campo, Coluna, Filtro, Plano
from .validador import validar

log = logging.getLogger(__name__)

# Campo de nome do cadastro, do mais típico ao menos (A2_NOME, B1_DESC, ED_DESCRIC, A1_NREDUZ...).
_NOMES = (r"_NOME$", r"_DESC$", r"_DESCRI$", r"_DESCRIC$", r"_RAZAO$", r"_NREDUZ$", r"_NOM[A-Z]{0,4}$",
          r"_DESC[A-Z]{0,4}$")
# M143: lotes curtos (~30 códigos < 1 KB em UTF-16LE TDS) evitam descarte de pacote por MTU em túneis VPN.
_LOTE = 30


_TITULO_NOME = re.compile(r"^(?:desc|nome|razao)", re.I)


def _campo_nome(tabela, codigo: str, visivel) -> str:
    for padrao in _NOMES:
        for c in tabela.campos:
            if (c.consultavel and c.tipo == "C" and c.nome != codigo and re.search(padrao, c.nome)
                    and visivel(tabela.alias, c.nome)):
                return c.nome
    # M154: campo de descrição com nome fora do padrão (ex.: TQY_DESTAT "Desc Status"): vale o título do dicionário.
    chave = set(getattr(tabela, "chave", ()) or ())
    for c in tabela.campos:
        if (c.consultavel and c.tipo == "C" and c.nome != codigo and c.nome not in chave
                and _TITULO_NOME.match((c.titulo or "").strip()) and visivel(tabela.alias, c.nome)):
            return c.nome
    return ""


_MAX_CADASTRO = 20000


def _padrao_termo(radical: str) -> str:
    """Radical sem acento ("MODUL") casa com o nome gravado com ou sem acento ("MÓDULO"): vogal vira curinga."""
    return "%" + re.sub(r"[AEIOU]", "_", radical.upper()) + "%"


def termos_no_cadastro(cat, mestre, termos, filial, visivel, timeout_s=10) -> dict:
    """M154: quantos registros de um cadastro de domínio (família, status, tipo...) têm cada termo do pedido no
    nome. Devolve {"campo_nome": ..., "contagens": {termo: n}}: só contagens (agregado), nunca nomes ou códigos.
    Mostra ao modelo, sem dados, que "módulo" é uma família de bens (filtrar por ela) ou que "deslocado" não é
    nenhum status (falta a definição). Cadastro grande não é sondado; a leitura passa pelo mesmo validador."""
    campo_nome = _campo_nome(mestre, "", visivel)
    chave = [c for c in (getattr(mestre, "chave", ()) or ()) if not c.endswith("_FILIAL")]
    if not campo_nome or not chave or not termos:
        return {}
    n = cat.linhas(mestre.fisica)
    if n is None or n > _MAX_CADASTRO:
        return {}
    permitidas, contagens = {mestre.fisica}, {}
    for termo in termos:
        p = Plano(objetivo="termos-do-cadastro", base=mestre.alias, filial=filial)
        if mestre.campo_filial:
            p.filtros.append(Filtro(Campo("e1", mestre.campo_filial), "=", filial))
        p.filtros.append(Filtro(Campo("e1", campo_nome), "LIKE", _padrao_termo(termo)))
        p.colunas = [Coluna("n", Campo("e1", chave[0]), "COUNT_DISTINCT", "inteiro")]
        comp = compilar(p, cat, 1)
        validar(comp.sql, permitidas, 2)
        linhas, _ = executar(cat, comp.sql, comp.params, 1, timeout_s,
                             validar_final=lambda sql: validar(sql, permitidas, 2))
        contagens[termo] = int(linhas[0][0] or 0) if linhas else 0
    return {"campo_nome": campo_nome, "contagens": contagens}


def _eh_nome(campo: str) -> bool:
    return any(re.search(p, campo) for p in _NOMES)


def _relacao(cat, tabela: str, campo: str, exibidos: set, visivel):
    """Cadastro do código `campo` da `tabela`, pelo SX9. Devolve (mestre, pares, campo_nome) ou None.
    `pares` liga os campos do cadastro aos da tabela. Só entram os campos exibidos na resposta: com loja exibida,
    a chave é código+loja; sem loja, só o código."""
    melhor, tamanho = None, 0
    for r in cat.relacionados(tabela):
        pares = [(o, d) for o, d in zip(r.campos_origem, r.campos_destino)
                 if not o.endswith("_FILIAL") and not d.endswith("_FILIAL")]
        if not pares or pares[0][1] != campo or r.origem == tabela:
            continue
        if melhor is not None and len(pares) >= tamanho:
            continue  # o cadastro de chave mais curta é o da entidade (fornecedor), não um vínculo (produto x fornecedor)
        mestre = cat.tabelas([r.origem], com_campos=True).get(r.origem)
        if mestre is None or not visivel(mestre.alias, pares[0][0]):
            continue
        nome = _campo_nome(mestre, pares[0][0], visivel)
        if nome:
            melhor, tamanho = (mestre, [(o, d) for o, d in pares if d in exibidos], nome), len(pares)
    return melhor


def _buscar(cat, mestre, pares, campo_nome, chaves, filiais, timeout_s) -> dict:
    """{chave: nome} das `chaves` (tuplas na ordem de `pares`), lendo o cadastro de cada filial distinta."""
    from iativa.agent import prazo

    nomes, vistos = {}, set()
    permitidas = {mestre.fisica}
    for filial in filiais:
        xf = xfilial(mestre, filial) if mestre.campo_filial else ""
        if xf in vistos:
            continue
        vistos.add(xf)
        faltam = sorted({k[0] for k in chaves if k not in nomes})
        for i in range(0, len(faltam), _LOTE):
            if prazo.esgotado():
                return nomes
            tempo_lote = prazo.limitar(timeout_s)
            lote = faltam[i:i + _LOTE]
            p = Plano(objetivo="nomes-do-cadastro", base=mestre.alias, filial=filial)
            if mestre.campo_filial:
                p.filtros.append(Filtro(Campo("e1", mestre.campo_filial), "=", filial))
            p.filtros.append(Filtro(Campo("e1", pares[0][0]), "IN", tuple(lote)))
            p.colunas = [Coluna(o, Campo("e1", o)) for o, _ in pares] + [
                Coluna("nome", Campo("e1", campo_nome), "MIN")]
            p.agrupar = True
            limite = len(lote) * 50  # código com várias lojas: várias linhas por código
            comp = compilar(p, cat, limite)
            validar(comp.sql, permitidas, limite + 1)
            linhas, _ = executar(cat, comp.sql, comp.params, limite, tempo_lote,
                                 validar_final=lambda sql, n=limite: validar(sql, permitidas, n + 1))
            for linha in linhas:
                chave = tuple(str(v).strip() for v in linha[:-1])
                nome = str(linha[-1] or "").strip()
                if nome:
                    nomes.setdefault(chave, nome)
        if all(k in nomes for k in chaves):
            break
    return nomes


def acrescentar_nomes(cat, plano, rotulos, linhas, origem, filiais, visivel, timeout_s):
    """Colunas de nome a inserir. Devolve uma lista de (posição da coluna de código, rótulo, {índice da linha: nome},
    tabela, campo). Linhas e origem são as brutas da consulta, uma filial por linha (None = somadas entre filiais)."""
    if not linhas or len(rotulos) != len(plano.colunas):
        return []
    aliases = {"e1": plano.base, **{j.alias: j.tabela for j in plano.juncoes}}
    juntadas = {j.tabela for j in plano.juncoes}
    feitos, out = set(), []
    for i, c in enumerate(plano.colunas):
        if c.agregacao or c.transformacao or c.tipo != "texto" or i in feitos:
            continue
        if c.campo.nome.endswith("_FILIAL") or c.rotulo == "Filial":  # M150: filial do registro (DA3_FILATU)
            continue
        if any(_eh_nome(x.campo.nome) for x in plano.colunas if x.campo.alias == c.campo.alias):
            continue  # a própria tabela já exibe coluna de nome/descrição (ex.: E1_NOMCLI, RA_NOME): não repete
        tabela = aliases.get(c.campo.alias)
        exibidos = {x.campo.nome: k for k, x in enumerate(plano.colunas)
                    if x.campo.alias == c.campo.alias and not x.agregacao and not x.transformacao}
        rel = _relacao(cat, tabela, c.campo.nome, set(exibidos), visivel)
        if rel is None or rel[0].alias in juntadas:
            continue
        mestre, pares, campo_nome = rel
        posicoes = [exibidos[d] for _, d in pares]
        feitos.update(posicoes)
        chaves_linha = [tuple(str(l[k]).strip() for k in posicoes) for l in linhas]
        validas = {k for k in chaves_linha if k[0]}
        if not validas:
            continue
        filiais_busca = list(dict.fromkeys([o for o in origem if o] + list(filiais)))
        try:
            nomes = _buscar(cat, mestre, pares, campo_nome, validas, filiais_busca, timeout_s)
        except Exception as exc:  # sem nome a resposta continua válida; nada do ERP vai para o log
            log.warning("nome do cadastro não buscado: %s.%s -> %s.%s (%s)", tabela, c.campo.nome,
                        mestre.alias, campo_nome, type(exc).__name__)
            continue
        if nomes:
            por_linha = {n: nomes[k] for n, k in enumerate(chaves_linha) if k in nomes}
            out.append((max(posicoes), f"Nome ({rotulos[i]})", por_linha, mestre.alias, campo_nome))
    return out
