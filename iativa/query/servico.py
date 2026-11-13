"""Execução de uma solicitação pronta (Etapa 5).

Fluxo: reconfere tudo (permissões, filiais, parâmetros, metadados) → plano → compilação →
validação AST → execução somente leitura → checagem de duplicação → resultado em memória.
SQL e linhas nunca são gravados nem registrados em log; a auditoria guarda só duração e
número de linhas.

Cálculos (M22): o total que o sistema informa é sempre o do banco, sobre todos os registros
autorizados que atendem aos filtros. Quando a lista é cortada pelo limite de exibição, ou quando
a consulta já é um cálculo (soma, média, ranking), uma segunda consulta agregada, com os mesmos
filtros e sem limite, traz quantidade, somas, mínimos e máximos. Médias entre filiais saem de
soma ÷ contagem (nunca média de médias). A soma das linhas exibidas é chamada de subtotal.
"""
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings

from iativa.accounts.authz import PermissaoNegada, filiais_autorizadas
from iativa.agent import orquestrador
from iativa.agent.contratos import AGREGACAO, OBJETIVOS, PREFIXO_APRENDIDO
from iativa.audit.servicos import registrar
from iativa.chat.estados import TransicaoInvalida
from iativa.chat.models import EstadoSolicitacao as E, RequestContext
from iativa.chat.servicos import criar_solicitacao, transicionar
from iativa.memory import servicos as memoria
from iativa.memory.models import Escopo, ParameterBinding, ParameterDefinition, Situacao
from iativa.memory.resolucao import RESOLVIDO, resolver
from iativa.metadata.catalogo import abrir_catalogo

from iativa.accounts.filiais import TODAS, rotulo

from .compilador import PlanoInvalido, compilar, xfilial
from .executor import TempoEsgotado, ValorRecusado, executar
from .plano import REGRAS, medidas_do_plano, montar, plano_chave, plano_total
from .validador import ConsultaRecusada, validar

# Situação do resultado (observação estruturada para o agente).
SUCESSO = "sucesso"
VAZIO = "vazio"
PARCIAL = "parcial"  # lista cortada pelo limite de exibição (o total continua completo)
ERRO_PARAMETROS = "erro_parametros"
ERRO_METADADOS = "erro_metadados"
TEMPO_ESGOTADO = "tempo_esgotado"
FALHA_TRANSITORIA = "falha_transitoria"
SEM_PERMISSAO = "sem_permissao"
TRANSITORIAS = (TEMPO_ESGOTADO, FALHA_TRANSITORIA)


@dataclass
class Medida:
    rotulo: str
    funcao: str  # SUM | COUNT | AVG | MIN | MAX
    tipo: str  # dinheiro | valor | inteiro | data ...
    valor: object = None  # Decimal, int, texto de data ou None (sem valores: tudo nulo ou sem registros)
    moeda: str = ""

    @property
    def texto(self) -> str:
        return "sem valor" if self.valor is None else _fmt(self.valor, self.tipo)


@dataclass
class Totais:
    """Calculado sobre TODOS os registros que atendem aos filtros, não só os exibidos."""
    registros: int
    medidas: list[Medida] = field(default_factory=list)
    moedas: list[str] = field(default_factory=list)  # mais de uma: não são somadas entre si

    def soma_dinheiro(self):
        if len(self.moedas) > 1:
            return None
        return next((m.valor for m in self.medidas if m.funcao == "SUM" and m.tipo == "dinheiro"), None)


@dataclass
class Resultado:
    ok: bool
    mensagem: str  # resposta final ao usuário, montada depois da execução (transitória: não é salva)
    colunas: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    linhas: list[list] = field(default_factory=list)
    premissas: list[str] = field(default_factory=list)
    truncado: bool = False
    total: str = ""  # total completo da coluna de dinheiro, formatado
    total_valor: object = None  # Decimal do total completo, para a soma do chat mestre
    totais: Totais | None = None  # None: o total completo não pôde ser calculado
    subtotal: str = ""  # soma só das linhas exibidas (quando a lista foi cortada)
    situacao: str = ""
    estado: str = ""
    solicitacao: RequestContext | None = None
    duracao_ms: int = 0

    @property
    def cabecalho(self):
        return [(c, "num" if t in ("dinheiro", "inteiro", "valor", "numero") else "") for c, t in zip(self.colunas, self.tipos)]

    @property
    def linhas_tela(self):
        classes = ["num" if t in ("dinheiro", "inteiro", "valor", "numero") else "" for t in self.tipos]
        return [list(zip(linha, classes)) for linha in self.linhas]


def _cfg():
    """Tempo de cada consulta: o configurado ou o que resta do prazo da mensagem (M24)."""
    from iativa.agent import prazo

    cfg = dict(settings.IATIVA_CONSULTA)
    cfg["timeout_s"] = prazo.limitar(cfg["timeout_s"])
    return cfg


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


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _fmt(valor, tipo):
    if valor is None:
        return ""
    if tipo == "dinheiro":
        v = _dec(valor).quantize(Decimal("0.01"))
        inteiro, _, dec = f"{v:,.2f}".partition(".")
        return f"R$ {inteiro.replace(',', '.')},{dec}"
    if tipo == "data":
        t = str(valor).strip()
        return f"{t[6:8]}/{t[4:6]}/{t[0:4]}" if len(t) == 8 else ""
    if tipo == "inteiro":
        return str(int(valor))
    if tipo == "valor":
        return _numero_br(valor)
    if tipo == "mes":
        t = str(valor).strip()
        return f"{t[4:6]}/{t[0:4]}" if len(t) == 6 else t
    if tipo == "numero":
        d = _dec(valor).normalize()
        return format(d, "f").replace(".", ",")
    return str(valor).strip()


# --- totais completos ---------------------------------------------------------------------------

class _Acumulador:
    """Junta os totais de cada filial sem perder a conta: somas e contagens se somam; a média sai
    de soma ÷ contagem no fim; mínimo e máximo comparam. Uma moeda não se soma com outra."""

    def __init__(self, specs):
        self.specs = specs
        self.por_moeda: dict[str, dict] = {}

    def _linha(self, moeda):
        return self.por_moeda.setdefault(moeda, {"registros": 0, "m": [
            {"soma": None, "cont": 0, "v": None} for _ in self.specs]})

    def juntar(self, moeda, registros, valores):
        """`valores`: por medida, (soma, contagem) em SUM/AVG ou (valor,) nas demais."""
        alvo = self._linha(moeda)
        alvo["registros"] += int(registros or 0)
        for (_, func, _, _), m, v in zip(self.specs, alvo["m"], valores):
            if func in ("SUM", "AVG"):
                soma, cont = v
                m["cont"] += int(cont or 0)
                if soma is not None:
                    m["soma"] = _dec(soma) if m["soma"] is None else m["soma"] + _dec(soma)
            elif func == "COUNT":
                m["v"] = int(v[0] or 0) + (m["v"] or 0)
            else:
                novo = v[0]
                if isinstance(novo, str):
                    novo = novo.strip() or None
                if novo is not None:
                    m["v"] = novo if m["v"] is None else (min if func == "MIN" else max)(m["v"], novo)

    def totais(self) -> Totais:
        moedas = sorted(self.por_moeda)
        medidas = []
        for moeda in moedas:
            for (rot, func, tipo, _), m in zip(self.specs, self.por_moeda[moeda]["m"]):
                if func == "SUM":
                    valor = m["soma"] if m["cont"] else None
                elif func == "AVG":
                    valor = (m["soma"] / m["cont"]) if m["cont"] and m["soma"] is not None else None
                else:
                    valor = m["v"]
                medidas.append(Medida(rot, func, tipo, valor, moeda if len(moedas) > 1 else ""))
        return Totais(sum(d["registros"] for d in self.por_moeda.values()), medidas, moedas if len(moedas) > 1 else [])


def _campo_moeda(tabela):
    """Campo de moeda da tabela base (ex.: E1_MOEDA), se existir e for consultável."""
    from .plano import Campo

    c = next((c for c in tabela.campos if c.nome.endswith("_MOEDA") and c.consultavel), None)
    return Campo("e1", c.nome) if c else None


def _total_no_banco(cat, plano, specs, acumulador, cfg, permitidas) -> int:
    """Consulta agregada com os mesmos filtros e sem limite de exibição. Devolve os registros da filial."""
    base = cat.tabelas([plano.base], com_campos=True)[plano.base]
    moeda = _campo_moeda(base) if any(f in ("SUM", "AVG") for _, f, _, _ in specs) else None
    total = plano_total(plano, specs, base.campo_filial, moeda)
    comp = compilar(total, cat, cfg["limite_linhas"])
    validar(comp.sql, permitidas, cfg["limite_linhas"] + 1)
    linhas, _ = executar(cat, comp.sql, comp.params, cfg["limite_linhas"], cfg["timeout_s"],
                         validar_final=lambda sql: validar(sql, permitidas, cfg["limite_linhas"] + 1))
    registros = 0
    for l in linhas:
        l = list(l)
        chave = str(l.pop(0)).strip() if moeda else ""
        n = int(l.pop(0) or 0)
        registros += n
        valores = []
        for _, func, _, _ in specs:
            valores.append((l.pop(0), l.pop(0)) if func in ("SUM", "AVG") else (l.pop(0),))
        acumulador.juntar(chave, n, valores)
    return registros


def _total_das_linhas(linhas, comp, specs, acumulador):
    """Lista completa (não cortada): o total sai das próprias linhas, com precisão decimal."""
    valores = []
    for rot, _, _, _ in specs:
        i = comp.rotulos.index(rot)
        nao_nulos = [_dec(l[i]) for l in linhas if l[i] is not None]
        valores.append((sum(nao_nulos, Decimal(0)) if nao_nulos else None, len(nao_nulos)))
    acumulador.juntar("", len(linhas), valores)


_CHAVES_CONFERIDAS: dict = {}
VALIDADE_CHAVE_S = 600


def _juncoes_sem_duplicar(cat, plano, cfg, permitidas) -> bool:
    """Cardinalidade antes de agregar: cada junção precisa ligar por uma chave que não se repete no
    outro lado (dentro da filial). Se repetir, a soma sairia inflada mesmo com o resultado "bonito"."""
    for j in plano.juncoes:
        t = cat.tabelas([j.tabela], com_campos=True)[j.tabela]
        chave = (t.fisica, j.pares, xfilial(t, plano.filial) if t.campo_filial else "")
        visto = _CHAVES_CONFERIDAS.get(chave) if cat.dialeto != "sqlite" else None
        if visto and time.monotonic() - visto < VALIDADE_CHAVE_S:
            continue
        comp = compilar(plano_chave(j, t.campo_filial, plano.filial), cat, cfg["limite_linhas"])
        validar(comp.sql, permitidas, cfg["limite_linhas"] + 1)
        linhas, _ = executar(cat, comp.sql, comp.params, 1, cfg["timeout_s"],
                             validar_final=lambda sql: validar(sql, permitidas, cfg["limite_linhas"] + 1))
        if linhas and int(linhas[0][-1] or 0) > 1:
            return False
        _CHAVES_CONFERIDAS[chave] = time.monotonic()
    return True


def _situacao_da_falha(exc) -> str:
    from .executor import tipo_falha

    return TEMPO_ESGOTADO if tipo_falha(exc) in ("tempo", "bloqueio") else FALHA_TRANSITORIA


def executar_solicitacao(user, s: RequestContext) -> Resultado:
    if s.usuario_id != user.pk:
        raise PermissaoNegada("Somente o dono executa a solicitação.")
    if s.estado != E.PRONTA:
        raise TransicaoInvalida("A solicitação não está pronta.")

    # 1. Reconfere tudo no momento da execução (permissão pode ter mudado desde "pronta").
    conferencia = orquestrador.avaliar(user, s)
    s.refresh_from_db()
    if conferencia.tipo != "pronta":
        situacao = ERRO_PARAMETROS if conferencia.tipo == "pergunta" else (
            ERRO_METADADOS if conferencia.mensagem == orquestrador.MOTIVOS["metadados_indisponiveis"] else SEM_PERMISSAO)
        return Resultado(False, conferencia.mensagem, estado=s.estado, solicitacao=s, situacao=situacao)
    valores = _valores(user, s)
    autorizadas = filiais_autorizadas(user, s.setor)
    todas = valores.get("filial-analise") == TODAS
    if todas:
        filiais = sorted(autorizadas)
    else:
        filiais = [valores.get("filial-analise")]
    if not filiais or any(f not in autorizadas for f in filiais):
        registrar("acesso_negado", "solicitacao", usuario=user, setor=s.setor, entidade_id=s.pk,
                  estado_novo="filial", resultado="negado")
        s = transicionar(user, s, E.AGUARDANDO_PARAMETROS)
        return Resultado(False, "A filial escolhida não está mais entre as suas filiais autorizadas. Escolha outra.",
                         estado=s.estado, solicitacao=s, situacao=SEM_PERMISSAO)
    if s.objetivo.startswith(PREFIXO_APRENDIDO):
        # Regras da memória (M22): critérios estruturados e confirmados, conferidos pelo compilador.
        from iativa.agent import memoria as memoria_agente

        valores[REGRAS] = memoria_agente.regras_aplicaveis(user, s.setor, OBJETIVOS[s.objetivo].tabelas[0])

    from iativa.agent import prazo

    if prazo.esgotado():
        return Resultado(False, "O tempo total desta mensagem acabou antes de consultar o ERP.", estado=s.estado,
                         solicitacao=s, situacao=TEMPO_ESGOTADO)
    s = transicionar(user, s, E.EXECUTANDO)
    cfg = _cfg()
    limite = cfg["limite_linhas"]
    inicio = time.monotonic()
    linhas, truncado, origem = [], False, []
    acumulador, totais_ok = None, True
    try:
        with abrir_catalogo() as cat:
            from iativa.agent import andamento

            for filial in filiais:  # "todas as filiais": uma consulta por filial autorizada, mesmo validador
                andamento.avisar(f"Consultando o ERP — {rotulo(filial)}")
                plano = montar(s.objetivo, {**valores, "filial-analise": filial})
                comp = compilar(plano, cat, limite)
                permitidas = {t.fisica for t in cat.tabelas([plano.base] + [j.tabela for j in plano.juncoes]).values()}
                validar(comp.sql, permitidas, limite + 1)
                if plano.juncoes:
                    andamento.avisar("Conferindo se as junções não duplicam registros")
                if plano.juncoes and not _juncoes_sem_duplicar(cat, plano, cfg, permitidas):
                    return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                                     "A junção desta consulta repete registros e inflaria os valores; por isso o "
                                     "resultado não foi validado. A consulta precisa ser refeita sem essa junção.",
                                     ERRO_METADADOS)
                parte, cortou = executar(
                    cat, comp.sql, comp.params, limite, cfg["timeout_s"],
                    validar_final=lambda sql, p=permitidas: validar(sql, p, limite + 1),
                )
                specs = medidas_do_plano(plano)
                calculo = any(c.agregacao for c in plano.colunas)
                if acumulador is None:
                    acumulador = _Acumulador(specs)
                base = cat.tabelas([plano.base], com_campos=True)[plano.base]
                com_moeda = bool(specs) and _campo_moeda(base) is not None  # moedas nunca se misturam
                registros = None
                if calculo or cortou or com_moeda:
                    andamento.avisar("Calculando o total no banco, sobre todos os registros")
                    try:
                        registros = _total_no_banco(cat, plano, specs, acumulador, cfg, permitidas)
                    except (ConsultaRecusada, PlanoInvalido, ValorRecusado):
                        raise
                    except Exception:
                        totais_ok = False  # o resultado vale; só o total completo fica sem informar
                else:
                    _total_das_linhas(parte, comp, specs, acumulador)
                    registros = len(parte)
                if registros is not None:
                    if not calculo and len(parte) > registros:
                        return _encerrar(user, s, E.NAO_VALIDADA, inicio, len(parte),
                                         "O resultado tem registros repetidos e não foi validado. Os critérios "
                                         "precisam ser revistos.", ERRO_METADADOS)
                    if calculo and not plano.agrupar and registros == 0:
                        parte = []  # SUM sem registros devolve uma linha com NULL: não é "um registro"
                linhas += parte
                origem += [filial] * len(parte)
                truncado = truncado or cortou
    except (ConsultaRecusada, PlanoInvalido, ValorRecusado) as exc:
        return _encerrar(user, s, E.FALHOU, inicio, 0, f"Consulta recusada pelo validador: {exc}",
                         ERRO_PARAMETROS if isinstance(exc, PlanoInvalido) else ERRO_METADADOS)
    except Exception as exc:  # rede, tempo, banco: sem detalhes (podem conter dados)
        from iativa.manutencao import incidentes

        incidentes.registrar("consulta", "executar", exc, condicoes={
            "objetivo_tipo": "aprendido" if s.objetivo.startswith("aprendido-") else s.objetivo})
        return _encerrar(user, s, E.FALHOU, inicio, 0,
                         f"A consulta não pôde ser concluída ({type(exc).__name__}). Tente de novo em instantes.",
                         _situacao_da_falha(exc))

    # 2. Checagem pós-execução: chave repetida indica junção que duplica valores.
    idx = [comp.rotulos.index(r) for r in plano.chave_unica]
    chaves = [(o,) + tuple(str(l[i]).strip() for i in idx) for o, l in zip(origem, linhas)]
    if idx and len(chaves) != len(set(chaves)):
        return _encerrar(user, s, E.NAO_VALIDADA, inicio, len(linhas),
                         "O resultado tem registros repetidos e não foi validado. Os critérios precisam ser revistos.",
                         ERRO_METADADOS)

    mesclou = False
    if todas and len(filiais) > 1 and plano.agrupar and linhas:
        linhas, mesclou = _mesclar_filiais(plano, comp, linhas)
        if mesclou:
            origem = [None] * len(linhas)
    tipos = list(comp.tipos)
    formatadas = [[_fmt(v, t) for v, t in zip(l, tipos)] for l in linhas]
    totais = acumulador.totais() if (acumulador is not None and totais_ok) else None
    total_valor = totais.soma_dinheiro() if totais else None
    total = _fmt(total_valor, "dinheiro") if total_valor is not None else ""
    subtotal = ""
    if truncado and "dinheiro" in tipos and not any(c.agregacao for c in plano.colunas):
        i = tipos.index("dinheiro")
        subtotal = _fmt(sum((_dec(l[i]) for l in linhas if l[i] is not None), Decimal(0)), "dinheiro")
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
    if todas and mesclou:
        plano.premissas = [p for p in plano.premissas if "filial" not in p.lower()] + [
            "Filiais: " + ", ".join(rotulo(f) for f in filiais) + ".",
            "Grupos iguais somados entre as filiais" + (f"; ranking geral dos {plano.top} maiores." if plano.top else ".")]
    elif todas:
        colunas, tipos = ["Filial"] + colunas, ["texto"] + tipos
        formatadas = [[rotulo(o)] + linha for o, linha in zip(origem, formatadas)]
        plano.premissas = [p for p in plano.premissas if "filial" not in p.lower()] + [
            "Filiais: " + ", ".join(rotulo(f) for f in filiais) + "."]

    mensagem = _resposta_final(s.objetivo, plano, len(linhas), truncado, totais, total, subtotal,
                               bool(valores.get(AGREGACAO)), len(filiais))
    destaque = _destaque(plano, comp, colunas, formatadas, linhas, tipos)
    if destaque:
        mensagem = destaque + " " + mensagem
    situacao = VAZIO if not linhas else (PARCIAL if truncado else SUCESSO)
    res = _encerrar(user, s, E.CONCLUIDA, inicio, len(linhas), mensagem, situacao)
    res.ok, res.colunas, res.tipos, res.linhas = True, colunas, tipos, formatadas
    res.premissas, res.truncado, res.total, res.total_valor = plano.premissas, truncado, total, total_valor
    res.totais, res.subtotal = totais, subtotal
    return res


def _mesclar_filiais(plano, comp, linhas):
    """M40: "todas as filiais" roda uma consulta por filial. Agrupado por algo que não é a filial (cliente,
    produto...), o mesmo grupo aparece em várias filiais: soma SUM/COUNT entre elas e aplica o top N geral.
    MIN/MAX/AVG não se somam: nesses casos mantém uma linha por filial."""
    aggs = [c.agregacao for c in plano.colunas]
    if (len(aggs) != len(comp.rotulos) or not any(aggs) or all(aggs)
            or any(a not in ("SUM", "COUNT") for a in aggs if a)):
        return linhas, False
    grupos = {}
    for linha in linhas:
        chave = tuple(str(v).strip() if not a else None for v, a in zip(linha, aggs))
        atual = grupos.get(chave)
        if atual is None:
            grupos[chave] = list(linha)
            continue
        for i, a in enumerate(aggs):
            if a and linha[i] is not None:
                atual[i] = (_dec(atual[i]) if atual[i] is not None else Decimal(0)) + _dec(linha[i])
    novas = list(grupos.values())
    ordem = plano.ordenar[0] if plano.ordenar else None
    i = (comp.rotulos.index(ordem[0]) if ordem and ordem[0] in comp.rotulos
         else next(k for k, a in enumerate(aggs) if a))
    if aggs[i]:
        novas.sort(key=lambda l: _dec(l[i]) if l[i] is not None else Decimal(0), reverse=ordem[1] if ordem else True)
    if plano.top:
        novas = novas[:plano.top]
    return novas, True


def _destaque(plano, comp, colunas, formatadas, linhas, tipos) -> str:
    """M40: cálculo agrupado com 2+ grupos: diz qual grupo ficou no topo (e no fim), em vez de só a tabela."""
    if not plano.agrupar or len(linhas) < 2:
        return ""
    aggs = [c.agregacao for c in plano.colunas]
    if len(aggs) != len(comp.rotulos) or not any(aggs):
        return ""
    i = next(k for k, a in enumerate(aggs) if a)
    desloc = len(colunas) - len(comp.rotulos)  # coluna "Filial" na frente
    pares = [(_dec(l[i]), f) for l, f in zip(linhas, formatadas) if l[i] is not None]
    if len(pares) < 2:
        return ""
    def nome(f):
        partes = [f[k] for k in range(len(f)) if k < desloc or not aggs[k - desloc]]
        return " · ".join(str(p) for p in partes if str(p).strip()) or "?"
    maior = max(pares, key=lambda p: p[0])
    menor = min(pares, key=lambda p: p[0])
    rot = comp.rotulos[i]
    return (f"Maior {rot}: {nome(maior[1])} ({maior[1][i + desloc]}). "
            f"Menor: {nome(menor[1])} ({menor[1][i + desloc]}).")


def _numero_br(v) -> str:
    inteiro, _, dec = f"{_dec(v).quantize(Decimal('0.01')):,.2f}".partition(".")
    return f"{inteiro.replace(',', '.')},{dec}"


def _medidas_texto(totais: Totais) -> str:
    return "; ".join(f"{m.rotulo}{f' (moeda {m.moeda})' if m.moeda else ''}: {m.texto}" for m in totais.medidas)


def _resposta_final(objetivo, plano, n, truncado, totais, total, subtotal, pediu_calculo, n_filiais) -> str:
    """Resposta montada pelo backend DEPOIS da execução, só com números conferidos. Separa total
    completo (banco, todos os registros), subtotal (linhas exibidas) e ranking."""
    unidade = {"titulos-em-atraso": "clientes", "titulos-a-vencer": "títulos", "posicao-cliente": "títulos"}.get(
        objetivo, "registros")
    aviso_moeda = " Há mais de uma moeda: os valores não foram somados entre si." if totais and totais.moedas else ""
    sem_total = " Não consegui calcular o total completo agora; os números acima são só das linhas exibidas."
    if n == 0 or (totais is not None and totais.registros == 0):
        return "Nenhum registro encontrado com esses critérios." + (
            " Não há o que calcular: isso é diferente de um total igual a zero." if pediu_calculo else "")
    if pediu_calculo:
        if totais is None:
            return f"Cálculo feito no banco: {n} linha(s)." + sem_total
        filiais = f", nas {n_filiais} filiais" if n_filiais > 1 else ""
        vazios = [m.rotulo for m in totais.medidas if m.valor is None and m.funcao != "COUNT"]
        nota = (" Sem valor preenchido para: " + ", ".join(vazios) + " (campo vazio em todos os registros; não é zero)."
                if vazios else "")
        if not plano.agrupar:
            return f"{_medidas_texto(totais)}. Calculado no banco sobre {totais.registros} registro(s){filiais}.{nota}{aviso_moeda}"
        geral = f"Total geral ({totais.registros} registro(s){filiais}, calculado no banco): {_medidas_texto(totais)}."
        if plano.top:
            return (f"Ranking com os {n} primeiros. {geral} O total geral considera todos os registros, não só os "
                    f"do ranking.{nota}{aviso_moeda}")
        corte = f" Mostrando só os primeiros {n} grupos." if truncado else ""
        return f"{n} grupo(s).{corte} {geral}{nota}{aviso_moeda}"
    if truncado:
        if totais is None:
            return f"Mostrando só os primeiros {n} {unidade}." + sem_total
        texto = f"Mostrando os primeiros {n} {unidade}; ao todo são {totais.registros} registro(s)."
        if total:
            texto += (f" Subtotal das linhas exibidas: {subtotal}." if subtotal else "") + (
                f" Total completo, calculado no banco sobre todos os registros: {total}.")
        return texto + aviso_moeda
    return f"{n} {unidade}" + (f", somando {total}" if total else "") + "." + aviso_moeda


def _encerrar(user, s, estado, inicio, linhas, mensagem, situacao="") -> Resultado:
    duracao = int((time.monotonic() - inicio) * 1000)
    s = transicionar(user, s, estado)
    registrar("consulta_executada", "solicitacao", usuario=user, setor=s.setor, entidade_id=s.pk,
              estado_novo=estado, resultado="ok" if estado == E.CONCLUIDA else "erro",
              duracao_ms=duracao, linhas=linhas)
    return Resultado(estado == E.CONCLUIDA, mensagem, estado=estado, solicitacao=s, duracao_ms=duracao,
                     situacao=situacao)


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
