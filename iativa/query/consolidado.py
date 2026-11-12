"""Visão consolidada do chat mestre (Etapa 6).

Depois que a consulta roda no setor do usuário, roda de novo em cada outro setor em que ele tem
"consultar", cada uma com a memória (valores padrão e regras) e as filiais daquele setor. O que o
usuário disse explicitamente na conversa vale para todos os setores.

Somar só faz sentido quando as premissas são iguais e as filiais diferentes: com premissas
diferentes os números não são comparáveis; com a mesma filial o mesmo título seria contado duas
vezes. Fora disso, os blocos aparecem lado a lado com a diferença explicada.

Resultados ficam só em memória, como na consulta simples.
"""
from dataclasses import dataclass, field

from iativa.accounts.authz import Permissao, setores_autorizados
from iativa.accounts.models import Department
from iativa.agent import orquestrador as orq
from iativa.agent.contratos import OBJETIVOS
from iativa.agent.conversao import exibir
from iativa.chat.estados import EM_PREPARACAO
from iativa.chat.servicos import cancelar, criar_solicitacao
from iativa.memory import servicos as memoria_valores
from iativa.memory.models import Escopo, ParameterBinding, Situacao
from iativa.memory.resolucao import RESOLVIDO, resolver

from . import servico
from .servico import _fmt

FILIAL = "filial-analise"


@dataclass
class Bloco:
    setor: str
    resultado: object = None  # servico.Resultado quando executou
    mensagem: str = ""
    valores: dict = field(default_factory=dict)  # código → valor exibido (premissas comparáveis)


@dataclass
class Consolidado:
    blocos: list
    resumo: str
    somado: bool = False
    consolidado = True  # o template diferencia do resultado simples


REGRAS_MEMORIA = "_regras_memoria"


def _valores(user, s) -> dict:
    """Premissas do bloco: parâmetros resolvidos e as regras estruturadas da memória que a execução
    aplica neste setor (M24). Regras diferentes = premissas diferentes: não se soma."""
    defs = orq._definicoes(OBJETIVOS[s.objetivo].parametros)
    out = {}
    for codigo, d in defs.items():
        r = resolver(d, user, s)
        out[codigo] = (d.nome, exibir(r.valor) if r.situacao == RESOLVIDO else "")
    obj = OBJETIVOS[s.objetivo]
    if obj.modelo_id:
        from iativa.agent import memoria as memoria_agente

        regras = memoria_agente.regras_aplicaveis(user, s.setor, obj.tabelas[0])
        out[REGRAS_MEMORIA] = ("Regras da memória", "; ".join(
            sorted(f"{campo} {op} {valor}" for campo, op, valor, _ in regras)) or "nenhuma")
    return out


def _copiar_explicitos(user, origem, destino):
    for b in ParameterBinding.objects.filter(solicitacao=origem, escopo=Escopo.SOLICITACAO,
                                             situacao=Situacao.APROVADA).select_related("definicao"):
        memoria_valores.registrar_valor(user, b.definicao, Escopo.SOLICITACAO, b.valor, solicitacao=destino)


def _bloco_de_outro_setor(user, s, setor) -> Bloco:
    nova = criar_solicitacao(user, s.dominio, s.objetivo, conversa=s.conversa, setor=setor)
    try:
        _copiar_explicitos(user, s, nova)
    except Exception:
        cancelar(user, nova)
        return Bloco(setor.nome, mensagem="Os critérios informados não valem para este setor (ex.: filial não autorizada).")
    r = orq.avaliar(user, nova)
    nova.refresh_from_db()
    if r.tipo != "pronta":
        if nova.estado in EM_PREPARACAO:
            cancelar(user, nova)  # não deixa solicitação pendente na conversa
        falta = ", ".join(p.nome.lower() for p in r.perguntas)
        motivo = f"falta definir {falta} para este setor" if falta else (r.mensagem or "não pôde ser executada")
        return Bloco(setor.nome, mensagem=f"Não consultado: {motivo}.")
    valores = _valores(user, nova)
    res = servico.executar_solicitacao(user, nova)
    if not res.ok:
        return Bloco(setor.nome, mensagem=res.mensagem, valores=valores)
    return Bloco(setor.nome, res, res.mensagem, valores)


def _resumo(blocos) -> tuple[str, bool]:
    ok = [b for b in blocos if b.resultado is not None]
    if len(ok) < 2:
        return "Só um setor retornou resultado; não há o que somar.", False
    premissas = [{k: v for k, v in b.valores.items() if k != FILIAL} for b in ok]
    diferentes = sorted({nome for p in premissas for k, (nome, v) in p.items()
                         if any(q.get(k, ("", None))[1] != v for q in premissas)})
    if diferentes:
        return ("Não somei os setores: as premissas são diferentes (" + ", ".join(d.lower() for d in diferentes)
                + "). Compare os blocos lado a lado."), False
    filiais = [b.valores.get(FILIAL, ("", ""))[1] for b in ok]
    if len(set(filiais)) != len(filiais):
        return ("Não somei os setores: mais de um usa a mesma filial, e os mesmos títulos seriam contados duas vezes."), False
    if any(b.resultado.truncado for b in ok):
        return "Não somei os setores: algum resultado foi limitado e o total seria parcial.", False
    linhas = sum(len(b.resultado.linhas) for b in ok)
    texto = f"Consolidado de {len(ok)} setores (mesmas premissas, filiais diferentes): {linhas} registro(s)"
    if all(b.resultado.total_valor is not None for b in ok):
        texto += ", somando " + _fmt(sum(b.resultado.total_valor for b in ok), "dinheiro")
    return texto + ".", True


def consolidar(user, s, res) -> Consolidado:
    """`s`/`res`: consulta já executada no setor do usuário."""
    blocos = [Bloco(s.setor.nome, res, res.mensagem, _valores(user, s))]
    outros = Department.objects.filter(
        pk__in=setores_autorizados(user, Permissao.CONSULTAR) - {s.setor_id}, ativo=True).order_by("nome")
    for setor in outros:
        blocos.append(_bloco_de_outro_setor(user, s, setor))
    resumo, somado = _resumo(blocos)
    return Consolidado(blocos, resumo, somado)


def talvez_consolidar(user, s, res):
    """No chat mestre, troca o resultado simples pelo consolidado; nas demais conversas, devolve o mesmo."""
    if not res.ok or s.conversa is None or s.conversa.tipo != "mestre":
        return res
    return consolidar(user, s, res)
