"""M109: revisor de entrega. Antes de a resposta ir para a pessoa, a própria IA confere se o que foi montado
responde ao que foi pedido. Reprovado: o ciclo tenta de outro jeito; esgotadas as tentativas, o pedido vai para a
manutenção automática (M107), que corrige e refaz o pedido, que passa pelo revisor de novo.

O modelo recebe SÓ metadados do que foi montado (títulos, colunas, quantidade de registros, medida calculada,
critérios e a frase-resumo do cálculo); nunca as linhas do ERP."""
import re

from django.conf import settings
from django.utils import timezone

from iativa.manutencao.incidentes import mascarar_pergunta

MODO = "revisar_entrega"
INDISPONIVEL = "A revisão da resposta está temporariamente indisponível."


class ClassificacaoPendente(str):
    """O revisor aprovou a base exibida e a classificação ainda depende da definição."""


def ligado() -> bool:
    return bool(getattr(settings, "IATIVA_REVISAO", True))


def tentativas() -> int:
    return int(getattr(settings, "IATIVA_REVISAO_TENTATIVAS", 2))


def _blocos(res):
    if res is None:
        return []
    if getattr(res, "consolidado", False) or hasattr(res, "blocos"):
        return [(getattr(b, "titulo", ""), b.resultado) for b in getattr(res, "blocos", []) if b.resultado is not None]
    return [(getattr(res, "titulo_entrega", ""), res)]


def descrever(saida) -> list[dict]:
    """O que vai ser entregue, sem dados de linhas."""
    out = []
    for titulo, r in _blocos(getattr(saida, "resultado", None)):
        if not getattr(r, "ok", False) and not getattr(r, "consolidado", False):
            continue
        medida = getattr(r, "medida_calculada", "") or ""
        criterios = list(getattr(r, "premissas", []) or [])
        if medida == "intervalo_entre_datas" and len(criterios) > 8:
            criterios = criterios[:7] + criterios[-1:]  # limite e datas usados pelo cálculo
        out.append({
            "titulo": str(titulo or "")[:200],
            "colunas": list(getattr(r, "colunas", []) or [])[:30],
            "registros": len(getattr(r, "linhas", []) or []),
            "medida_calculada": medida,
            # Frase do sistema sobre o resultado: total geral calculado no banco, maior/menor grupo, resumo do
            # cálculo (ex.: "41 de 72 com Dt. Demissão"). M113: antes só ia para cálculos e o revisor achava que
            # faltava o total que estava na resposta. São agregados do sistema, não linhas do ERP.
            "resumo": _resumo(getattr(r, "mensagem", ""), 400),
            "resumo_calculo": _resumo(getattr(r, "mensagem", ""), 300) if medida else "",
            "criterios": [str(p)[:200] for p in criterios[:8]],
            # M154: o que o backend comprova sobre a consulta (entidade, chave, filtros com o significado do domínio,
            # classificação e alcance). Só metadados e códigos de domínio do dicionário.
            **({"evidencia": getattr(r, "evidencia")} if getattr(r, "evidencia", None) else {}),
        })
    return out


def _resumo(texto, limite):
    """Frase do sistema sem dado de pessoa: tira o destaque "Maior/Menor <grupo>" (o grupo pode ser um nome) e troca
    sequências em maiúsculas (nomes do cadastro) por [nome]; depois o mascaramento da manutenção."""
    frases = [f for f in re.split(r"(?<=\.)\s+", str(texto or "")) if not re.match(r"\s*(Maior|Menor)\b", f)]
    t = re.sub(r"\b[A-ZÀ-Ú]{2,}(?:\s+(?:D[AEO]S?\s+)?[A-ZÀ-Ú]{2,})+\b", "[nome]", " ".join(frases))
    return mascarar_pergunta(t, limite)


def deterministico(blocos: list[dict]) -> str:
    """Erros óbvios, sem gastar o modelo."""
    for b in blocos:
        if b["medida_calculada"] == "intervalo_entre_datas" and " 0 de 0 " in f" {b['resumo_calculo']} ":
            return ("O cálculo do prazo rodou sobre uma base em que nenhum registro tem a data do segundo evento "
                    "(ex.: consulta só de ativos não traz quem saiu). Use uma consulta que traga também quem já teve o "
                    "segundo evento, com o período na data do primeiro evento.")
    return ""


def classificacao_sem_definicao(contrato, blocos, texto) -> str:
    """M155: várias entregas de categorias (ex.: disponíveis, deslocados) respondidas por UMA tabela agrupada pelos
    códigos de um cadastro (os nomes aparecem para a pessoa, não para a revisão) não comprovam as categorias. Sem
    definição na conversa: levantamento + pergunta específica. Com a definição dada: contar cada categoria."""
    from .contagem import rotulos_curtos

    itens = [e for e in (contrato or []) if isinstance(e, dict) and e.get("metrica") == "contagem"]
    if len(itens) < 2 or len(blocos) != 1:
        return ""
    ev = blocos[0].get("evidencia") or {}
    classes = ev.get("classificacao_pelo_dominio") or []
    if not ev.get("agrupado_por") or ev.get("categorias_pedidas") or not any(
            "cadastro" in str(d) for c in classes for d in c.get("dominio", [])):
        return ""
    rotulos = rotulos_curtos([str(e.get("descricao", "")) for e in itens])
    categorias = [r.strip() for r in rotulos if r.strip() and not re.search(r"\btota", r, re.I)]
    if len(categorias) < 1 or len(categorias) == len(rotulos) and len(categorias) < 2:
        return ""
    campo = re.sub(r"^[A-Z0-9]+_[A-Z0-9]+\s*\(|\)$", "", classes[0].get("campo", "")).strip() or "situação"
    falta = "A resposta montada não atende: " + "; ".join(str(e.get("descricao", "")) for e in itens[-len(categorias):])
    if "Esclarecimento da pessoa" in (texto or ""):
        return (falta + ". Como corrigir: a pessoa já definiu as categorias no esclarecimento. Conte cada categoria em "
                "uma consulta própria, com filtro-campo no nome da situação (CAMPO_NOME~termo da definição) e o mesmo "
                "filtro da entidade, uma entrega por consulta.")
    lista = " e quais como ".join(f"“{c.lower()}”" for c in categorias)
    return falta + f". Como corrigir: Na contagem por {campo.lower()}, quais situações contam como {lista}?"


def revisar(user, provedor, texto: str, contrato: list, saida, *, preservadas=None) -> str:
    """'' = aprovado; caso contrário, devolve o motivo para o atendimento retentar."""
    if not ligado():
        return ""
    blocos = descrever(saida)
    if not blocos:
        return ""  # sem tabela/cálculo (conversa, pergunta, bloqueio): nada a revisar aqui
    det = deterministico(blocos)
    if det:
        return det
    from . import andamento, prazo
    from .decisao import EntradaAgente

    classificacao = classificacao_sem_definicao(contrato, blocos, texto)
    resta = prazo.restante()
    if resta is not None and resta < 10:
        return INDISPONIVEL if classificacao else ""  # M143: sem folga no prazo, preserva o resultado já validado pelo contrato em vez de descartá-lo
    andamento.avisar("Conferindo se a resposta atende ao que você pediu")
    entrada = EntradaAgente(
        "Confira se a resposta montada atende ao pedido.", timezone.localdate().isoformat(), [], [], None,
        planejamento={"modo": MODO, "pedido": texto[:1000],
                      "contrato": [str(e.get("descricao", ""))[:300] for e in (contrato or []) if isinstance(e, dict)],
                      "resposta_montada": blocos})
    from . import tempos
    try:
        with tempos.medir("revisao"):
            a = provedor.planejar(entrada)
    except prazo.PrazoEsgotado:
        from . import objetivos as coord
        return INDISPONIVEL if classificacao or int(coord.cfg().get("continuacoes", 0)) > 0 else ""
    except Exception:
        return INDISPONIVEL
    if not isinstance(a, dict):
        return INDISPONIVEL
    faltas = [str(e.get("descricao", "")).strip() for e in (a.get("entregas") or [])
              if isinstance(e, dict) and str(e.get("descricao", "")).strip()]
    if not faltas:
        # M155: entidade e filtros aprovados pela revisão; só então categorias pedidas sobre uma tabela agrupada por
        # cadastro (nomes que a revisão não vê) viram levantamento + pergunta, ou contagem filtrada se já definidas.
        return ClassificacaoPendente(classificacao) if classificacao else ""
    if preservadas is not None:
        descricoes = [item.get("descricao") for item in contrato]
        # Uma reprovação geral ou parafraseada não identifica blocos independentes.
        if set(faltas) <= set(descricoes):
            titulos = [b["titulo"] for b in blocos]
            preservadas.update(i for i, desc in enumerate(descricoes)
                               if desc not in faltas and descricoes.count(desc) == 1 and titulos.count(desc) == 1)
    como = str(a.get("resumo") or "").strip()
    return ("A resposta montada não atende: " + "; ".join(faltas) + "." + (f" Como corrigir: {como}" if como else ""))[:600]
