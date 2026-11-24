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
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

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
from .plano import Campo, Filtro, REGRAS, medidas_do_plano, montar, plano_chave, plano_total
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

    def valor_aditivo(self, rotulo):
        if len(self.moedas) > 1:
            return None
        medida = next((m for m in self.medidas if m.rotulo == rotulo and m.funcao in ("SUM", "COUNT")), None)
        if medida is None:
            return None
        return 0 if self.registros == 0 else medida.valor


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
    escopo: dict = field(default_factory=dict)
    periodos: list = field(default_factory=list)  # intervalos derivados, só no backend/snapshot autorizado
    medida_calculada: str = ''
    agregado: bool = False  # M127: o plano calculou no banco (contagem/soma/média), em qualquer caminho
    memoria_tecnica: dict = field(default_factory=dict)  # M95: {estado, versao, trechos} da busca obrigatória
    curta_salva: str = ''  # M48: resposta curta gravada no snapshot (os totais não são gravados)

    fonte_inconclusiva: str = ""
    legenda_pendente: str = ""
    filtros_contagem_por_filial: tuple[Filtro, ...] = ()
    rodape: list[str] = field(default_factory=list)
    colunas_meta: list[dict] = field(default_factory=list)

    @property
    def cabecalho(self):
        return [(c, "num" if t in ("dinheiro", "inteiro", "valor", "numero") else "") for c, t in zip(self.colunas, self.tipos)]

    @property
    def curta(self) -> str:
        """M48: só o valor pedido ("189 dias úteis", "R$ 1.234,00"), para quem pediu a resposta direta.
        Vazio quando o resultado é uma lista que não cabe numa frase: aí a tabela aparece sem extras."""
        if self.curta_salva:
            return self.curta_salva
        if not self.ok:
            return ""
        unidades = {"dias_uteis": "dias úteis", "dias_corridos": "dias corridos", "meses": ""}
        if self.medida_calculada in unidades and self.linhas:
            u = unidades[self.medida_calculada]
            valores = [f"{l[3]} {u}".strip() for l in self.linhas]
            return valores[0] if len(valores) == 1 else "; ".join(f"{l[0]}: {v}" for l, v in zip(self.linhas, valores))
        if self.medida_calculada and self.linhas:
            sufixo = "%" if self.medida_calculada == "percentual" else ""
            def valor(t):
                try:
                    return _numero_br(t) + sufixo
                except Exception:
                    return str(t)
            if len(self.linhas) == 1:
                return valor(self.linhas[0][1])
            return "; ".join(f"{l[0]}: {valor(l[1])}" for l in self.linhas)
        if len(self.linhas) == 1 and self.colunas:
            pares = [(c, str(v)) for c, v in zip(self.colunas, self.linhas[0])]  # linhas já formatadas
            numeros = [(c, v) for (c, v), t in zip(pares, self.tipos) if t in ("dinheiro", "valor", "inteiro", "numero")]
            # M55: "só o número" de um total é o valor em reais; a contagem de títulos fica na tabela.
            dinheiro = [(c, v) for (c, v), t in zip(pares, self.tipos) if t == "dinheiro"]
            escolhidos = dinheiro or numeros or pares
            return escolhidos[0][1] if len(escolhidos) == 1 else "; ".join(f"{c}: {v}" for c, v in escolhidos)
        if self.totais is not None and self.totais.medidas:
            m = self.totais.medidas
            return m[0].texto if len(m) == 1 else "; ".join(f"{x.rotulo}: {x.texto}" for x in m)
        return ""

    @property
    def linhas_tela(self):
        classes = ["num" if t in ("dinheiro", "inteiro", "valor", "numero") else "" for t in self.tipos]
        return [list(zip(linha, classes)) for linha in self.linhas]

    @property
    def rodape_tela(self):
        return [(v, cls) for v, (_, cls) in zip(self.rodape, self.cabecalho)]

    @property
    def avisos_leitura(self):
        return [p for p in self.premissas if p.startswith(("Há período em andamento", "Há mais de uma moeda",
                                                          "Há códigos sem correspondência")) and p not in self.mensagem]


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
            elif func in ("COUNT", "COUNT_DISTINCT"):
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
        if plano.categorias and specs[0][1] == "COUNT_DISTINCT" and int(l.pop(0)) != n:
            raise PlanoInvalido("Existem registros sem a chave da entidade; confirme outra fonte ou chave.")
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
    """M95: antes de preparar a consulta do ERP, a memória técnica é consultada (etapa obrigatória, em todos os
    caminhos: chat, reexecução, continuação e worker). O estado real vai no resultado e nas premissas; se o índice
    estiver indisponível, a consulta segue só com o dicionário local e diz isso."""
    from iativa.agent import tempos
    with tempos.medir("erp"):  # M125: memória técnica + consulta ao ERP
        memoria = memoria_tecnica_da_solicitacao(s)
        r = _executar_solicitacao(user, s)
    r.memoria_tecnica = memoria
    if memoria.get("estado") == "ok":
        r.premissas = list(r.premissas) + [f"Memória técnica {memoria['versao']} consultada ({memoria['trechos']} trecho(s)); "
                                           "estrutura confirmada no dicionário local."]
    else:
        r.premissas = list(r.premissas) + ["Memória técnica indisponível: usei só o dicionário local da instalação."]
    return r


def memoria_tecnica_da_solicitacao(s) -> dict:
    from iativa.knowledge import memoria_tecnica as mt
    obj = OBJETIVOS[s.objetivo] if s.objetivo in OBJETIVOS else None
    aliases = list(obj.tabelas) if obj is not None else []
    if obj is not None and obj.modelo_id:
        from iativa.agent.models import ConsultaModelo
        cm = ConsultaModelo.objects.filter(pk=obj.modelo_id).first()
        if cm is not None:
            aliases += [j["tabela"] for j in cm.spec.get("juncoes", [])]
    try:
        r = mt.recuperar(aliases=aliases, permitidos=set(aliases))
    except Exception:
        r = mt.Recuperacao(mt.INDISPONIVEL)
    return {**r.resumo(), "aliases": aliases}


def _executar_solicitacao(user, s: RequestContext) -> Resultado:
    if s.usuario_id != user.pk:
        raise PermissaoNegada("Somente o dono executa a solicitação.")
    if s.estado != E.PRONTA:
        raise TransicaoInvalida("A solicitação não está pronta.")

    # 1. Reconfere tudo no momento da execução (permissão pode ter mudado desde "pronta").
    conferencia = orquestrador.avaliar(user, s)
    s.refresh_from_db()
    if conferencia.tipo != "pronta":
        situacao = ERRO_PARAMETROS if conferencia.tipo == "pergunta" else (
            ERRO_METADADOS if conferencia.mensagem in (orquestrador.MOTIVOS["metadados_indisponiveis"],
                                                       orquestrador.MOTIVOS["fonte_ausente"]) else SEM_PERMISSAO)
        return Resultado(False, conferencia.mensagem, estado=s.estado, solicitacao=s, situacao=situacao)
    transversais = set()
    if s.objetivo.startswith(PREFIXO_APRENDIDO):
        from iativa.agent.planejamento import FonteInconclusiva, autorizar_fonte
        from iativa.agent.provedores import FalhaModelo
        from iativa.agent.models import ConsultaModelo
        cm = ConsultaModelo.objects.get(pk=OBJETIVOS[s.objetivo].modelo_id)
        if not cm.setores.filter(pk=s.setor_id).exists():
            return Resultado(False, "Esta receita não está liberada para o setor da solicitação.",
                             estado=s.estado, solicitacao=s, situacao=SEM_PERMISSAO)
        from iativa.agent.aprendizado import camada_por_codigo
        camada = camada_por_codigo(user, s.objetivo)
        juncoes = cm.spec.get('juncoes', []) + camada.get('juncoes', [])
        aliases = list(dict.fromkeys([cm.tabela] + [j["tabela"] for j in juncoes]))
        try:
            if cm.spec.get("leitura_transversal"):
                from iativa.accounts.authz import leitura_de_todos_os_setores
                if (cm.criado_por_id != user.pk or s.conversa.tipo != "individual" or s.conversa.usuario_id != user.pk
                        or not leitura_de_todos_os_setores(user, aliases)):
                    raise PermissaoNegada("A consulta depende de acesso vigente às mesmas filiais em todos os setores.")
                transversais.update(aliases)
            for alias in aliases:
                setores = autorizar_fonte(user, None, alias, conversa=s.conversa, transversais=transversais)
                if s.setor.codigo not in setores:
                    raise PermissaoNegada("A fonte não é autorizada para o setor desta solicitação.")
        except FonteInconclusiva as exc:
            return Resultado(False, str(exc), fonte_inconclusiva=alias,
                             estado=s.estado, solicitacao=s, situacao=ERRO_METADADOS)
        except (FalhaModelo, ImproperlyConfigured):
            return Resultado(False, 'O classificador de fontes está indisponível. Verifique as outras fontes autorizadas.',
                             estado=s.estado, solicitacao=s, situacao=ERRO_METADADOS)
        except PermissaoNegada as exc:
            return Resultado(False, str(exc),
                             estado=s.estado, solicitacao=s, situacao=SEM_PERMISSAO)
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
        from .plano import FILTROS_USUARIO

        valores[FILTROS_USUARIO] = camada  # M42: filtros só desta pessoa

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
    nomes_juncoes = {}
    try:
        with abrir_catalogo() as cat:
            from iativa.agent import andamento

            categorias = {}
            legenda_pendente = ""
            legenda_grupo = {}
            for filial in filiais:  # "todas as filiais": uma consulta por filial autorizada, mesmo validador
                andamento.avisar(f"Consultando o ERP — {rotulo(filial)}")
                plano = montar(s.objetivo, {**valores, "filial-analise": filial})
                if s.objetivo.startswith(PREFIXO_APRENDIDO):
                    from iativa.metadata.descoberta import _Visibilidade
                    campos = escopo_do_plano(plano, s.setor_id, filiais)["campos"]
                    visibilidade = _Visibilidade({s.setor_id}, campos)
                    if any(not visibilidade.campo(a, c) for a, nomes in campos.items() for c in nomes):
                        return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                                         "O setor não tem acesso a todos os campos solicitados.", SEM_PERMISSAO)
                if (not plano.categorias and plano.agrupar and not legenda_pendente and plano.colunas
                        and not plano.colunas[0].agregacao and not plano.colunas[0].transformacao):
                    # M105: contagem agrupada por um campo cuja legenda o Protheus monta por função (ex.: FP0_STATUS
                    # por LOCA001CI): entrega por código e diz que a legenda falta; não repete a consulta em segundo plano.
                    campo = plano.colunas[0].campo
                    alias = {"e1": plano.base, **{j.alias: j.tabela for j in plano.juncoes}}.get(campo.alias)
                    if alias:
                        legenda_pendente = cat.dominios_campos(alias)[1].get(campo.nome, "")
                        from iativa.agent.legendas import legendas_aprendidas
                        legenda_grupo = legendas_aprendidas(alias).get(campo.nome, {})  # M106
                if plano.categorias and not (categorias or legenda_pendente):
                    campo = plano.colunas[0].campo
                    alias = {"e1": plano.base, **{j.alias: j.tabela for j in plano.juncoes}}[campo.alias]
                    dominios, dinamicos = cat.dominios_campos(alias)
                    opcoes = dominios.get(campo.nome, {})
                    legenda_pendente = dinamicos.get(campo.nome, "")
                    if not legenda_pendente and any(k not in opcoes for k in plano.categorias):
                        raise PlanoInvalido(f"O dicionário local não confirma os códigos pedidos em {campo.nome}.")
                    categorias = {k: opcoes[k] for k in plano.categorias if k in opcoes}
                    if len(set(categorias.values())) != len(categorias):
                        raise PlanoInvalido("Mais de um código corresponde à mesma descrição no dicionário local.")
                if len(filiais) > 1 and any(c.agregacao == "COUNT_DISTINCT" for c in plano.colunas):
                    if cat.tabelas([plano.base])[plano.base].compartilhamento != "filial":
                        raise PlanoInvalido("O identificador precisa ser contado no escopo de uma única filial física.")
                mesclar = todas and len(filiais) > 1 and _pode_mesclar(plano)
                # O líder geral pode não ser top N em nenhuma filial. Somar todos os grupos
                # autorizados antes do ranking, mantendo o teto de leitura de cada consulta.
                # M139: no ranking geral cada filial devolve TODOS os seus grupos (só totais agregados, não registros)
                # para somar entre filiais antes do top N. O teto de exibição (500) recusava rankings como
                # "10 fornecedores com mais pedidos"; a leitura agregada tem o próprio teto, bem maior.
                lim = max(limite, int(cfg.get("limite_grupos", 20000))) if mesclar else limite
                comp = compilar(replace(plano, top=0) if mesclar else plano, cat, lim)
                infos_tab = cat.tabelas([plano.base] + [j.tabela for j in plano.juncoes])
                permitidas = {t.fisica for t in infos_tab.values()}
                nomes_juncoes = {j.alias: infos_tab[j.tabela].nome for j in plano.juncoes
                                 if j.tabela in infos_tab and infos_tab[j.tabela].nome}
                validar(comp.sql, permitidas, lim + 1)
                if plano.juncoes:
                    andamento.avisar("Conferindo se as junções não duplicam registros")
                if plano.juncoes and not _juncoes_sem_duplicar(cat, plano, cfg, permitidas):
                    return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                                     "A junção desta consulta repete registros e inflaria os valores; por isso o "
                                     "resultado não foi validado. A consulta precisa ser refeita sem essa junção.",
                                     ERRO_METADADOS)
                parte, cortou = executar(
                    cat, comp.sql, comp.params, lim, cfg["timeout_s"],
                    validar_final=lambda sql, p=permitidas, n=lim: validar(sql, p, n + 1),
                )
                if mesclar and cortou:
                    return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                                     "O número de grupos ultrapassou o limite de leitura. O ranking geral "
                                     "não foi validado, pois faltariam valores de outras filiais. "
                                     "Reduza o período ou delimite os critérios para conferir todos os grupos.",
                                     ERRO_PARAMETROS)
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
    if todas and len(filiais) > 1 and plano.agrupar and linhas and not getattr(plano, "por_filial", False):
        linhas, mesclou = _mesclar_filiais(plano, comp, linhas)
        if mesclou:
            if len(linhas) > limite:  # M139: sem top N, a tela mostra os primeiros grupos do ranking e avisa
                linhas, truncado = linhas[:limite], True
            origem = [None] * len(linhas)
    sem_grupos = not linhas
    if plano.categorias:
        if truncado:
            return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                             "O limite de grupos impediu conferir todas as categorias.", ERRO_PARAMETROS)
        if plano.colunas[1].agregacao == "COUNT_DISTINCT":
            medida = acumulador.totais().medidas if totais_ok else []
            if not medida or sum(int(n) for _, n in linhas) != medida[0].valor:
                return _encerrar(user, s, E.NAO_VALIDADA, inicio, 0,
                                 "Não foi possível confirmar uma situação única por entidade no período. "
                                 "Procure a fonte do status atual ou o histórico na data de referência.", ERRO_METADADOS)
    if legenda_grupo and not categorias:
        # M106: código do grupo com o nome da legenda aprendida (ex.: "3 - Aprovado").
        linhas = [(f"{str(l[0]).strip()} - {legenda_grupo[str(l[0]).strip()]}" if str(l[0]).strip() in legenda_grupo
                   else l[0], *l[1:]) for l in linhas]
    if categorias:
        encontrados = {str(codigo).strip(): n for codigo, n in linhas}
        desconhecidos = set(encontrados) - set(opcoes)
        linhas = [(nome, encontrados.get(codigo, 0)) for codigo, nome in categorias.items()]
        origem = [filiais[0]] * len(linhas)
        if desconhecidos:
            plano.premissas.append("Há códigos sem correspondência no dicionário, fora das categorias apresentadas.")
    tipos = list(comp.tipos)
    formatadas = [[_fmt(v, t) for v, t in zip(l, tipos)] for l in linhas]
    nomes = []
    if linhas and not categorias and not legenda_grupo and not legenda_pendente:
        nomes = _nomes_dos_codigos(s, plano, comp, linhas, origem, filiais, cfg)
    totais = acumulador.totais() if (acumulador is not None and totais_ok) else None
    total_valor = totais.soma_dinheiro() if totais else None
    total = _fmt(total_valor, "dinheiro") if total_valor is not None else ""
    subtotal = ""
    if truncado and "dinheiro" in tipos and not any(c.agregacao for c in plano.colunas):
        i = tipos.index("dinheiro")
        subtotal = _fmt(sum((_dec(l[i]) for l in linhas if l[i] is not None), Decimal(0)), "dinheiro")
    rotulos_base = _rotulos_exibicao(nomes_juncoes, plano, comp.rotulos)
    colunas_meta = [{"campo": "" if c.agregacao else c.campo.nome, "rotulo": r}
                    for c, r in zip(plano.colunas, rotulos_base)]
    if s.objetivo == "titulos-em-atraso" and linhas and "Cliente" in comp.rotulos:
        corte = date.fromisoformat(valores["data-corte"])
        i = comp.rotulos.index("Vencimento mais antigo")
        tipos.append("inteiro")
        for linha_bruta, linha in zip(linhas, formatadas):
            t = str(linha_bruta[i]).strip()
            linha.append(str((corte - date(int(t[:4]), int(t[4:6]), int(t[6:8]))).days) if len(t) == 8 else "")
        colunas = list(rotulos_base) + ["Dias de atraso (maior)"]
        colunas_meta.append({"campo": "", "rotulo": "Dias de atraso (maior)"})
    else:
        colunas = list(rotulos_base)
    if todas and mesclou:
        plano.premissas = [p for p in plano.premissas if "filial" not in p.lower()] + [
            "Filiais: " + ", ".join(rotulo(f) for f in filiais) + ".",
            "Grupos iguais somados entre as filiais" + (f"; ranking geral dos {plano.top} maiores." if plano.top else ".")]
    elif todas and not categorias:
        colunas, tipos = ["Filial"] + colunas, ["texto"] + tipos
        colunas_meta = [{"campo": "_FILIAL", "rotulo": "Filial"}] + colunas_meta
        formatadas = [[rotulo(o)] + linha for o, linha in zip(origem, formatadas)]
        plano.premissas = [p for p in plano.premissas if "filial" not in p.lower()] + [
            "Filiais: " + ", ".join(rotulo(f) for f in filiais) + "."]

    if categorias and todas and not mesclou:
        plano.premissas.append("Filiais consultadas: " + ", ".join(rotulo(f) for f in filiais) + ".")
    mensagem = _resposta_final(s.objetivo, plano, len(linhas), truncado, totais, total, subtotal,
                               bool(valores.get(AGREGACAO)), len(filiais))
    desloc = 1 if colunas and colunas[0] == "Filial" and comp.rotulos[:1] != ("Filial",) else 0
    com_nome = [list(f) for f in formatadas]
    for pos, _, por_linha, _, _ in nomes:  # M140: o destaque diz "000269 - NOME DO FORNECEDOR"
        for n, nome in por_linha.items():
            com_nome[n][desloc + pos] = f"{com_nome[n][desloc + pos]} - {nome}"
    destaque = _destaque(plano, comp, colunas, com_nome, linhas, tipos)
    for pos, rot, por_linha, mestre, campo_nome in sorted(nomes, key=lambda x: -x[0]):
        colunas.insert(desloc + pos + 1, rot)
        colunas_meta.insert(desloc + pos + 1, {"campo": campo_nome, "rotulo": rot})
        tipos.insert(desloc + pos + 1, "texto")
        for n, linha in enumerate(formatadas):
            linha.insert(desloc + pos + 1, por_linha.get(n, ""))
    for _, rot, _, mestre, campo_nome in nomes:
        plano.premissas.append(f"{rot}: buscado no cadastro {mestre} ({campo_nome}) pelo código.")
    if destaque and not categorias:
        mensagem = destaque + " " + mensagem
    if legenda_pendente and not plano.categorias:
        mensagem += (f" Os nomes de {plano.colunas[0].rotulo} não ficam gravados no banco (o Protheus os monta pela "
                     f"rotina {legenda_pendente}); a tabela mostra os códigos.")
    if plano.categorias:
        periodo = valores.get("periodo-analise")
        mensagem = "Contagem por " + comp.rotulos[0]
        if periodo:
            mensagem += f" no período de {_fmt(periodo['inicio'].replace('-', ''), 'data')} a {_fmt(periodo['fim'].replace('-', ''), 'data')}"
        mensagem += "."
        if sem_grupos:
            mensagem += " A consulta completa não encontrou registros no período."
        if legenda_pendente:
            mensagem += (f" O Protheus fornece a legenda de {campo.nome} por {legenda_pendente}. "
                         "A tabela mostra os códigos encontrados. Falta confirmar quais correspondem às situações solicitadas.")
        totais = None
    situacao = VAZIO if sem_grupos else (PARCIAL if truncado else SUCESSO)
    res = _encerrar(user, s, E.CONCLUIDA, inicio, len(linhas), mensagem, situacao)
    res.legenda_pendente = legenda_pendente
    if legenda_pendente:
        res.situacao = PARCIAL
    res.ok, res.colunas, res.colunas_meta, res.tipos, res.linhas = True, colunas, colunas_meta, tipos, formatadas
    res.premissas, res.truncado, res.total, res.total_valor = plano.premissas, truncado, total, total_valor
    res.totais, res.subtotal = totais, subtotal
    if formatadas and colunas[0] == "Filial" and totais and any(c.agregacao for c in plano.colunas):
        valores_totais = [_fmt(totais.valor_aditivo(c), t) for c, t in zip(colunas[1:], tipos[1:])]
        if any(valores_totais):
            res.rodape = ["Total", *valores_totais]
    if categorias:
        res.medida_calculada = "contagem"
    if s.objetivo.startswith(PREFIXO_APRENDIDO) and not truncado and totais_ok and not legenda_pendente and not cm.spec.get("operacao_validada"):
        import hashlib
        import json
        estrutura = [(c.nome, c.tipo, c.tamanho, c.titulo, c.descricao) for c in base.campos if c.consultavel]
        assinatura_local = hashlib.sha256(json.dumps([estrutura, base.chave, categorias],
                                                     sort_keys=True).encode()).hexdigest()[:16]
        from iativa.agent import receitas
        from .plano import expressao_validada
        # O plano validado contém apenas campos do catálogo e operadores, sem os valores dos filtros.
        prova = {"dicionario_local": assinatura_local, "dominio": categorias,
                 "chave_local": list(base.chave), "campo_data": cm.spec.get("campo_data", ""),
                 "agregacao": expressao_validada(valores.get(AGREGACAO, ""), cm.spec)}
        receitas.registrar_validacao(cm, prova)
    res.escopo = escopo_do_plano(plano, s.setor_id, filiais)
    for _, _, _, mestre, campo_nome in nomes:
        res.escopo["campos"][mestre] = sorted(set(res.escopo["campos"].get(mestre, [])) | {campo_nome})
    res.agregado = any(c.agregacao for c in plano.colunas)
    res.funcoes = sorted({c.agregacao for c in plano.colunas if c.agregacao})  # M133: comprova contagem/soma/média
    if transversais:
        res.escopo["fontes_transversais"] = sorted(transversais)
    if (todas and not truncado and not plano.juncoes and not plano.agrupar and not plano.top
            and len(plano.colunas) == 1 and plano.colunas[0].agregacao == "COUNT"
            and plano.colunas[0].campo == plano.filtros[0].campo and plano.filtros[0].campo.nome.endswith("_FILIAL")):
        res.filtros_contagem_por_filial = tuple(plano.filtros[1:])
    return res


def _nomes_dos_codigos(s, plano, comp, linhas, origem, filiais, cfg):
    """M140: nome do cadastro ao lado de cada código exibido (fornecedor, cliente, produto...). Ver `nomes`."""
    from functools import lru_cache

    from iativa.agent import andamento
    from iativa.metadata.descoberta import _Visibilidade
    from .nomes import acrescentar_nomes

    from iativa.agent import prazo

    if prazo.esgotado() or not any(not c.agregacao and not c.transformacao and c.tipo == "texto"
                                   and not c.campo.nome.endswith("_FILIAL") for c in plano.colunas):
        return []

    @lru_cache(maxsize=None)
    def _vis(alias):
        return _Visibilidade({s.setor_id}, [alias])

    try:
        with abrir_catalogo() as cat:
            andamento.avisar("Buscando os nomes dos códigos no cadastro")
            resta = prazo.restante()
            # M142: o nome é complemento; nunca come o tempo da resposta (no máximo 10 s, dentro do prazo).
            tempo = max(1, int(min(10, cfg["timeout_s"], (resta - 5) if resta is not None else 10)))
            if resta is not None and resta < 8:
                return []
            return acrescentar_nomes(cat, plano, list(comp.rotulos), linhas, origem, filiais,
                                     lambda a, c: _vis(a).campo(a, c), tempo)
    except Exception as exc:  # sem nome a resposta continua válida
        import logging
        logging.getLogger(__name__).warning("nomes dos códigos não buscados (%s)", type(exc).__name__)
        return []


def _rotulos_exibicao(nomes_juncoes: dict, plano, rotulos) -> list[str]:
    """M144: títulos genéricos de tabelas juntadas ('Descricao' em SRJ/Funções, ou 'Nome' repetido em duas tabelas)
    recebem o nome da tabela do dicionário para não ficarem ambíguos na tela nem na revisão."""
    out = list(rotulos)
    if not nomes_juncoes or len(out) != len(plano.colunas):
        return out
    contagem_nome = sum(1 for r in out if "nome" in str(r or "").lower())
    for i, (c, r) in enumerate(zip(plano.colunas, out)):
        if c.agregacao or c.campo.alias == "e1" or "(" in str(r or ""):
            continue
        nome_tab = (nomes_juncoes.get(c.campo.alias) or "").strip()
        if not nome_tab:
            continue
        norm = str(r or "").strip().lower().replace("ç", "c").replace("ã", "a").rstrip(".")
        if norm in ("descricao", "desc") or (norm == "nome" and contagem_nome > 1):
            out[i] = f"{str(r).strip()} ({nome_tab})"
    return out


def escopo_do_plano(plano, setor_id, filiais):
    campos = {}
    aliases = {"e1": plano.base, **{j.alias: j.tabela for j in plano.juncoes}}
    usados = [c.campo for c in plano.colunas] + [f.campo for f in plano.filtros]
    usados.extend(f.valor for f in plano.filtros if isinstance(f.valor, Campo))
    for j in plano.juncoes:
        origem = getattr(j, "origem_alias", "e1") or "e1"
        for a, b in j.pares:
            usados.extend([Campo(j.alias, a), Campo(origem, b)])
    for campo in usados:
        campos.setdefault(aliases[campo.alias], set()).add(campo.nome)
    return {"setor": setor_id, "filiais": list(filiais), "campos": {a: sorted(ns) for a, ns in campos.items()}}


def _pode_mesclar(plano):
    aggs = [c.agregacao for c in plano.colunas]
    return (plano.agrupar and any(aggs) and (not all(aggs) or plano.somar_filiais) and not plano.por_filial
            and all(a in ("SUM", "COUNT", "COUNT_DISTINCT", "MIN", "MAX") for a in aggs if a))


def _mesclar_filiais(plano, comp, linhas):
    """M40: "todas as filiais" roda uma consulta por filial. Agrupado por algo que não é a filial (cliente,
    produto...), o mesmo grupo aparece em várias filiais: soma SUM/COUNT entre elas e aplica o top N geral.
    MIN/MAX escolhem o extremo entre filiais; AVG mantém uma linha por filial."""
    aggs = [c.agregacao for c in plano.colunas]
    if len(aggs) != len(comp.rotulos) or not _pode_mesclar(plano):
        return linhas, False
    grupos = {}
    for linha in linhas:
        chave = tuple(str(v).strip() if not a else None for v, a in zip(linha, aggs))
        atual = grupos.get(chave)
        if atual is None:
            grupos[chave] = list(linha)
            continue
        for i, a in enumerate(aggs):
            if not a or linha[i] is None:
                continue
            if atual[i] is None:
                atual[i] = linha[i]
            elif a in ("MIN", "MAX"):  # M41: data/valor mais antigo ou maior entre as filiais
                atual[i] = (min if a == "MIN" else max)(atual[i], linha[i])
            else:
                atual[i] = _dec(atual[i]) + _dec(linha[i])
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
    # Coluna "Filial" na frente (uma linha por filial); colunas extras no fim (ex.: dias de atraso) não deslocam.
    desloc = 1 if colunas and colunas[0] == "Filial" and comp.rotulos[:1] != ["Filial"] else 0
    if not (plano.agrupar or desloc) or not linhas:
        return ""
    aggs = [c.agregacao for c in plano.colunas]
    if len(aggs) != len(comp.rotulos) or not any(aggs):
        return ""
    ordem = plano.ordenar[0][0] if plano.ordenar else None
    # M41: o destaque segue a coluna do ranking (ex.: "Valor em atraso"), não a primeira conta da tabela.
    i = (comp.rotulos.index(ordem) if ordem in comp.rotulos and aggs[comp.rotulos.index(ordem)] in ("SUM", "COUNT")
         else next(k for k, a in enumerate(aggs) if a))
    pares = [(_dec(l[i]), f) for l, f in zip(linhas, formatadas) if l[i] is not None]
    def nome(f):
        partes = [f[k] for k in range(desloc + len(aggs)) if k < desloc or not aggs[k - desloc]]
        return " · ".join(str(p) for p in partes if str(p).strip()) or "?"
    if len(pares) == 1 and plano.top == 1:
        return f"O maior é {nome(pares[0][1])} ({pares[0][1][i + desloc]})."
    if len(pares) < 2:
        return ""
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
            return (f"{'Ranking com os ' + str(n) + ' primeiros. ' if n > 1 else ''}{geral} O total geral considera todos os registros, não só os "
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
