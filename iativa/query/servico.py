"""Execução de uma solicitação pronta (Etapa 5).

Fluxo: reconfere tudo (permissões, filiais, parâmetros, metadados) → plano → compilação →
validação AST → execução somente leitura → checagem de duplicação → resultado em memória.
SQL e linhas nunca são gravados nem registrados em log; a auditoria guarda só duração e
número de linhas.
"""
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings

from iativa.accounts.authz import PermissaoNegada, filiais_autorizadas
from iativa.agent import orquestrador
from iativa.agent.contratos import OBJETIVOS
from iativa.audit.servicos import registrar
from iativa.chat.estados import TransicaoInvalida
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.chat.servicos import criar_solicitacao, transicionar
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, ParameterBinding, ParameterDefinition, Situacao
from iativa.memory.resolucao import RESOLVIDO, resolver
from iativa.metadata.catalogo import abrir_catalogo

from .compilador import PlanoInvalido, compilar
from .executor import ValorRecusado, executar
from .plano import montar
from .validador import ConsultaRecusada, validar


@dataclass
class Resultado:
    ok: bool
    mensagem: str
    colunas: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    linhas: list[list] = field(default_factory=list)
    premissas: list[str] = field(default_factory=list)
    truncado: bool = False
    total: str = ""
    total_valor: object = None  # Decimal da coluna de dinheiro, para a soma do chat mestre
    estado: str = ""
    solicitacao: RequestContext | None = None
    duracao_ms: int = 0

    @property
    def cabecalho(self):
        return [(c, "num" if t in ("dinheiro", "inteiro") else "") for c, t in zip(self.colunas, self.tipos)]

    @property
    def linhas_tela(self):
        classes = ["num" if t in ("dinheiro", "inteiro") else "" for t in self.tipos]
        return [list(zip(linha, classes)) for linha in self.linhas]


def _cfg():
    return settings.IATIVA_CONSULTA


def _valores(user, s) -> dict:
    objetivo = OBJETIVOS[s.objetivo]
    defs = ParameterDefinition.objects.filter(codigo__in=objetivo.parametros, ativo=True)
    out = {}
    for d in defs:
        r = resolver(d, user, s)
        if r.situacao != RESOLVIDO:
            if d.codigo in objetivo.opcionais:
                continue
            raise TransicaoInvalida("Parâmetro deixou de estar definido.")
        out[d.codigo] = r.valor
    return out


def _fmt(valor, tipo):
    if valor is None:
        return ""
    if tipo == "dinheiro":
        v = Decimal(str(valor)).quantize(Decimal("0.01"))
        inteiro, _, dec = f"{v:,.2f}".partition(".")
        return f"R$ {inteiro.replace(',', '.')},{dec}"
    if tipo == "data":
        t = str(valor).strip()
        return f"{t[6:8]}/{t[4:6]}/{t[0:4]}" if len(t) == 8 else ""
    if tipo == "inteiro":
        return str(int(valor))
    if tipo == "numero":
        d = Decimal(str(valor)).normalize()
        return format(d, "f").replace(".", ",")
    return str(valor).strip()


def executar_solicitacao(user, s: RequestContext) -> Resultado:
    if s.usuario_id != user.pk:
        raise PermissaoNegada("Somente o dono executa a solicitação.")
    if s.estado != E.PRONTA:
        raise TransicaoInvalida("A solicitação não está pronta.")

    # 1. Reconfere tudo no momento da execução (permissão pode ter mudado desde "pronta").
    conferencia = orquestrador.avaliar(user, s)
    s.refresh_from_db()
    if conferencia.tipo != "pronta":
        return Resultado(False, conferencia.mensagem, estado=s.estado, solicitacao=s)
    valores = _valores(user, s)
    if valores.get("filial-analise") not in filiais_autorizadas(user, s.setor):
        registrar("acesso_negado", "solicitacao", usuario=user, setor=s.setor, entidade_id=s.pk,
                  estado_novo="filial", resultado="negado")
        s = transicionar(user, s, E.AGUARDANDO_PARAMETROS)
        return Resultado(False, "A filial escolhida não está mais entre as suas filiais autorizadas. Escolha outra.",
                         estado=s.estado, solicitacao=s)

    plano = montar(s.objetivo, valores)
    s = transicionar(user, s, E.EXECUTANDO)
    cfg = _cfg()
    inicio = time.monotonic()
    try:
        with abrir_catalogo() as cat:
            comp = compilar(plano, cat, cfg["limite_linhas"])
            permitidas = {t.fisica for t in cat.tabelas([plano.base] + [j.tabela for j in plano.juncoes]).values()}
            validar(comp.sql, permitidas, cfg["limite_linhas"] + 1)
            linhas, truncado = executar(
                cat, comp.sql, comp.params, cfg["limite_linhas"], cfg["timeout_s"],
                validar_final=lambda sql: validar(sql, permitidas, cfg["limite_linhas"] + 1),
            )
    except (ConsultaRecusada, PlanoInvalido, ValorRecusado) as exc:
        return _encerrar(user, s, E.FALHOU, inicio, 0, f"Consulta recusada pelo validador: {exc}")
    except Exception as exc:  # rede, tempo, banco: sem detalhes (podem conter dados)
        return _encerrar(user, s, E.FALHOU, inicio, 0,
                         f"A consulta não pôde ser concluída ({type(exc).__name__}). Tente de novo em instantes.")

    # 2. Checagem pós-execução: chave repetida indica junção que duplica valores.
    idx = [comp.rotulos.index(r) for r in plano.chave_unica]
    chaves = [tuple(str(l[i]).strip() for i in idx) for l in linhas]
    if idx and len(chaves) != len(set(chaves)):
        return _encerrar(user, s, E.NAO_VALIDADA, inicio, len(linhas),
                         "O resultado tem registros repetidos e não foi validado. Os critérios precisam ser revistos.")

    tipos = list(comp.tipos)
    formatadas = [[_fmt(v, t) for v, t in zip(l, tipos)] for l in linhas]
    total, total_valor = "", None
    if "dinheiro" in tipos:
        i = tipos.index("dinheiro")
        total_valor = sum((Decimal(str(l[i] or 0)) for l in linhas), Decimal(0))
        total = _fmt(total_valor, "dinheiro")
    if s.objetivo == "titulos-em-atraso" and linhas:
        corte = date.fromisoformat(valores["data-corte"])
        i = comp.rotulos.index("Vencimento mais antigo")
        tipos.append("inteiro")
        for linha_bruta, linha in zip(linhas, formatadas):
            t = str(linha_bruta[i]).strip()
            linha.append(str((corte - date(int(t[:4]), int(t[4:6]), int(t[6:8]))).days) if len(t) == 8 else "")
        colunas = list(comp.rotulos) + ["Dias de atraso (maior)"]
    else:
        colunas = list(comp.rotulos)

    res = _encerrar(user, s, E.CONCLUIDA, inicio, len(linhas), _resumo(s.objetivo, len(linhas), total, truncado))
    res.ok, res.colunas, res.tipos, res.linhas = True, colunas, tipos, formatadas
    res.premissas, res.truncado, res.total, res.total_valor = plano.premissas, truncado, total, total_valor
    return res


def _resumo(objetivo, n, total, truncado) -> str:
    if n == 0:
        return "Nenhum registro encontrado com esses critérios."
    unidade = {"titulos-em-atraso": "clientes", "titulos-a-vencer": "títulos", "posicao-cliente": "títulos"}.get(
        objetivo, "registros")
    texto = f"{n} {unidade}" + (f", somando {total}" if total else "") + "."
    if truncado:
        texto += f" Mostrando só os primeiros {n}: o total é parcial."
    return texto


def _encerrar(user, s, estado, inicio, linhas, mensagem) -> Resultado:
    duracao = int((time.monotonic() - inicio) * 1000)
    s = transicionar(user, s, estado)
    registrar("consulta_executada", "solicitacao", usuario=user, setor=s.setor, entidade_id=s.pk,
              estado_novo=estado, resultado="ok" if estado == E.CONCLUIDA else "erro",
              duracao_ms=duracao, linhas=linhas)
    return Resultado(estado == E.CONCLUIDA, mensagem, estado=estado, solicitacao=s, duracao_ms=duracao)


def repetir(user, anterior: RequestContext, conversa=None) -> RequestContext:
    """Nova solicitação com os mesmos valores informados na anterior (reabrir = nova execução)."""
    nova = criar_solicitacao(user, anterior.dominio, anterior.objetivo, conversa=conversa or anterior.conversa)
    vistos = set()
    for b in (ParameterBinding.objects.filter(solicitacao=anterior, escopo=Escopo.SOLICITACAO,
                                               situacao=Situacao.APROVADA).select_related("definicao").order_by("-id")):
        if b.definicao_id in vistos:
            continue
        vistos.add(b.definicao_id)
        memoria.registrar_valor(user, b.definicao, Escopo.SOLICITACAO, b.valor, solicitacao=nova)
    return nova
