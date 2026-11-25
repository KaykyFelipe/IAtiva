"""M154: pergunta inédita respondida na primeira conversa, com a descoberta restrita, a revisão apoiada na evidência
do backend, a pergunta de definição de negócio na hora certa e a receita guardada com o método que funcionou.

Caso de reprodução: “quantos módulos tem hoje na ativa, disponíveis e deslocados”. O ERP sintético ganha um cadastro
de módulos (situação atual com domínio no SX3) e um histórico de movimentações. O modelo controlado erra de propósito
no primeiro plano (conta movimentações), volta a escolher a consulta reprovada e, já com as tabelas em mãos, tenta de
novo a ação errada. O revisor controlado só decide pela evidência que o backend comprova (entidade, filtros e domínio)."""
import sqlite3
from dataclasses import replace

import pytest

from iativa.agent import agente
from iativa.agent.decisao import DecisaoInvalida, EntradaAgente, validar
from iativa.agent.models import ConsultaModelo, NotaAgente
from iativa.agent.provedores import FakeProvider
from iativa.chat import conversas
from .test_m95_novos_pedidos import _ambiente, _d, _usuario  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

PEDIDO = "quantos módulos tem hoje na ativa, disponíveis e deslocados"
ENTREGAS = [
    {"descricao": "Total de módulos da empresa hoje", "tipo": "calculo", "metrica": "contagem"},
    {"descricao": "Módulos disponíveis hoje", "tipo": "calculo", "metrica": "contagem"},
    {"descricao": "Módulos deslocados hoje", "tipo": "calculo", "metrica": "contagem"},
]
DEFINICAO = "Qual situação do módulo a operação chama de deslocado: em trânsito, locado ou outra?"


@pytest.fixture
def agregacao_definida(db):
    """Testes transaction=True apagam até as definições semeadas por migração: recria a de agregação."""
    from importlib import import_module
    from iativa.memory.models import ParameterDefinition
    for codigo, migracao in (("texto-busca", "0002_definicao_texto_busca"),
                             ("filtro-campo", "0003_definicao_filtro_campo"), ("agregacao", "0004_definicao_agregacao")):
        dados = import_module(f"iativa.memory.migrations.{migracao}").DADOS
        ParameterDefinition.objects.get_or_create(codigo=codigo, defaults=dados)


@pytest.fixture
def modulos(erp_sintetico, agregacao_definida):
    with sqlite3.connect(erp_sintetico) as db:
        db.execute("ALTER TABLE SX3010 ADD COLUMN X3_CBOX TEXT")
        for alias, nome, chave in (("ZZB", "Cadastro de modulos habitacionais", "ZZB_FILIAL+ZZB_COD"),
                                   ("ZZM", "Movimentacoes de modulos", "ZZM_FILIAL+ZZM_COD+ZZM_SEQ")):
            db.execute("""INSERT INTO SX2010 (X2_CHAVE,X2_ARQUIVO,X2_NOME,X2_MODO,X2_MODOUN,X2_MODOEMP)
                          VALUES (?,?,?,'E','E','E')""", (alias, f"{alias}010", nome))
            db.execute("INSERT INTO SIX010 (INDICE,ORDEM,CHAVE) VALUES (?,'1',?)", (alias, chave))
        db.execute("""CREATE TABLE ZZB010 (ZZB_FILIAL TEXT, ZZB_COD TEXT, ZZB_DESC TEXT, ZZB_SITUAC TEXT,
                                           D_E_L_E_T_ TEXT DEFAULT ' ')""")
        db.execute("""CREATE TABLE ZZM010 (ZZM_FILIAL TEXT, ZZM_COD TEXT, ZZM_SEQ TEXT, ZZM_DATA TEXT,
                                           ZZM_TIPO TEXT, D_E_L_E_T_ TEXT DEFAULT ' ')""")
        campos = [
            ("ZZB", "ZZB_FILIAL", "Filial", "C", 2, ""), ("ZZB", "ZZB_COD", "Codigo do modulo", "C", 6, ""),
            ("ZZB", "ZZB_DESC", "Descricao do modulo", "C", 30, ""),
            ("ZZB", "ZZB_SITUAC", "Situacao atual do modulo", "C", 1,
             "D=Disponivel;L=Locado;T=Em transito;M=Em manutencao"),
            ("ZZM", "ZZM_FILIAL", "Filial", "C", 2, ""), ("ZZM", "ZZM_COD", "Codigo do modulo", "C", 6, ""),
            ("ZZM", "ZZM_SEQ", "Sequencia", "C", 3, ""), ("ZZM", "ZZM_DATA", "Data da movimentacao", "D", 8, ""),
            ("ZZM", "ZZM_TIPO", "Tipo de movimentacao de modulos", "C", 1, "E=Entrada;S=Saida;T=Transferencia"),
        ]
        for alias, campo, titulo, tipo, tamanho, caixa in campos:
            db.execute("""INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TITULO,X3_DESCRIC,X3_TIPO,X3_TAMANHO,X3_CBOX)
                          VALUES (?,?,?,?,?,?,?)""", (alias, campo, titulo, titulo, tipo, tamanho, caixa))
        # Filial 01: 6 módulos vivos (D, D, D, L, T, M) e um excluído (T); 9 movimentações (mais que módulos).
        db.executemany("INSERT INTO ZZB010 VALUES (?,?,?,?,?)", [
            ("01", "M00001", "MODULO A", "D", " "), ("01", "M00002", "MODULO B", "D", " "),
            ("01", "M00003", "MODULO C", "D", " "), ("01", "M00004", "MODULO D", "L", " "),
            ("01", "M00005", "MODULO E", "T", " "), ("01", "M00006", "MODULO F", "M", " "),
            ("01", "M00007", "MODULO G", "T", "*"),
        ])
        db.executemany("INSERT INTO ZZM010 VALUES (?,?,?,?,?,?)", [
            ("01", f"M0000{i % 6 + 1}", f"{i:03}", "20261101", "ET S"[i % 4], " ") for i in range(9)])
    return erp_sintetico


from iativa.accounts.filiais import TODAS  # noqa: E402
FILIAL = {"codigo": "filial-analise", "valor": TODAS}


def _agregacao(valor):
    return [FILIAL, {"codigo": "agregacao", "valor": valor}]


def _criar(tabela, agregacao, atende, nome, colunas):
    return _d(acao="criar_consulta", entrega="calculo", atende=atende, parametros=_agregacao(agregacao),
              consulta={"tabela": tabela, "nome": nome, "colunas": colunas})


class Operador(FakeProvider):
    """Modelo controlado: segue o roteiro (itens podem ser funções da entrada) e registra o que recebeu."""

    def __init__(self, roteiro, revisor, entregas=ENTREGAS):
        super().__init__(list(roteiro))
        self.revisor, self.entregas = revisor, entregas
        self.entradas, self.revisoes_recebidas = [], []

    def decidir(self, entrada):
        self.entradas.append(entrada)
        if not self.respostas:
            raise AssertionError("o modelo não deveria ser chamado aqui: " + str(entrada.observacoes)[-900:]
                                 + str(entrada.planejamento.get("pendentes")))
        item = self.respostas.pop(0)
        return item(entrada) if callable(item) else item

    def planejar(self, entrada):
        modo = entrada.planejamento.get("modo")
        if modo == "revisar_entrega":
            self.revisoes_recebidas.append(entrada.planejamento)
            return self.revisor(entrada.planejamento)
        a = super().planejar(entrada)
        if modo == "classificar":
            a = {**a, "entregas": [dict(e) for e in self.entregas]}
        return a


def _texto(res):
    """Texto mostrado (mensagem + linhas), de um resultado simples ou consolidado em blocos."""
    if res is None:
        return ""
    if hasattr(res, "blocos"):
        return " ".join([str(getattr(res, "resumo", ""))] + [b.titulo + " " + _texto(b.resultado) for b in res.blocos])
    return str(getattr(res, "mensagem", "")) + " " + " ".join(" ".join(map(str, l)) for l in getattr(res, "linhas", []))


def _ok(res):
    return res is not None and (getattr(res, "ok", False) or bool(getattr(res, "blocos", None)))


def _aprovado():
    return {"natureza": "dados", "entregas": [], "resumo": ""}


def revisor_pela_evidencia(plan):
    """Decide SÓ pelo que o backend comprova: a entidade contada e o significado do domínio dos filtros."""
    blocos = plan["resposta_montada"]
    evid = [b.get("evidencia") or {} for b in blocos]
    assert all(evid), "o backend precisa mandar a evidência da consulta"
    if any(e["entidade"].startswith("ZZM") for e in evid):
        return {"natureza": "dados", "entregas": [{"descricao": e["descricao"], "tipo": "calculo", "metrica": "contagem"}
                                                  for e in ENTREGAS],
                "resumo": "A consulta contou movimentações (histórico), não módulos. Conte o cadastro de módulos pela "
                          "situação atual."}
    significados = " ".join([s for e in evid for f in e["filtros_aplicados"] for s in f["significado"]]
                            + [c for e in evid for c in e["categorias_pedidas"]])
    # "Deslocado" não está no domínio: só fica comprovado se o método usa UMA situação definida (ou a pessoa definiu).
    candidatas = [x for x in ("Locado", "Em transito") if x in significados]
    if len(candidatas) == 1 or "Esclarecimento" in plan["pedido"]:
        return _aprovado()
    return {"natureza": "dados", "entregas": [{"descricao": ENTREGAS[2]["descricao"], "tipo": "calculo",
                                               "metrica": "contagem"}], "resumo": DEFINICAO}


def _cadastro(e):
    """Com as tabelas em mãos: monta a consulta sobre o cadastro (se ele estiver entre as tabelas)."""
    assert {t["tabela"] for t in e.tabelas_para_aprender} >= {"ZZB"}, e.tabelas_para_aprender
    return _criar("ZZB", "contagem_distinta:ZZB_COD", [0], "Modulos cadastrados", ["ZZB_COD", "ZZB_SITUAC"])


def test_modulos_primeiro_plano_errado_descoberta_definicao_e_receita(modulos, novo_usuario, politica):
    user = _usuario(novo_usuario, politica, "comercial")
    c = conversas.nova_individual(user)
    errado = _criar("ZZM", "contagem_distinta:ZZM_COD", [0, 1, 2], "Movimentacoes de modulos", ["ZZM_COD", "ZZM_TIPO"])
    roteiro = [
        errado,
        # O modelo volta à consulta reprovada (mesmos parâmetros): o backend troca pela descoberta.
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[0, 1, 2],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZM").codigo, parametros=_agregacao("contagem_distinta:ZZM_COD")),
        # Na descoberta, insiste na consulta antiga: fora das ações da etapa, volta com a restrição.
        lambda e: _d(acao="preparar_consulta", objetivo=ConsultaModelo.objects.get(tabela="ZZM").codigo,
                     parametros=_agregacao("contagem_distinta:ZZM_COD"), calculo={"operacao": "", "limite": 0},
                     formato="tabela"),
        _cadastro,
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[1, 2],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZB").codigo,
                     parametros=_agregacao("contagem_distinta:ZZB_COD por ZZB_SITUAC categorias D,L,T")),
    ]
    p = Operador(roteiro, revisor_pela_evidencia)
    msg, res = conversas.enviar(user, c, PEDIDO, p)
    # 1ª conversa: a definição que muda o resultado vira pergunta específica; nada reprovado aparece.
    assert msg.texto.endswith(DEFINICAO), msg.texto
    assert msg.ficha.get("tipo") == "esclarecimento" and msg.ficha.get("pedido_original")
    # M155: o que a revisão aprovou (total de módulos do cadastro) vai junto; nada da consulta reprovada (ZZM).
    blocos = [b.resultado for b in res.blocos] if hasattr(res, "blocos") else [res]
    assert blocos and all(b.evidencia["entidade"].startswith("ZZB") for b in blocos)
    assert "6" in _texto(res)
    restrita = [e for e in p.entradas if e.tabelas_para_aprender]
    assert restrita and all(e.acoes_permitidas == agente.ETAPA_MONTAR for e in restrita)
    assert any(o.get("situacao") == "etapa_errada" for o in restrita[-1].observacoes), "restrição volta ao modelo"
    evid = [b["evidencia"] for r in p.revisoes_recebidas for b in r["resposta_montada"]]
    assert evid[0]["entidade"].startswith("ZZM") and evid[0]["alcance"]["todas_responderam"]
    assert any(c_["dominio"] and "D = Disponivel" in c_["dominio"] for e in evid for c_ in e["classificacao_pelo_dominio"])

    # 2ª mensagem: resposta curta retoma o pedido original e calcula com a definição.
    roteiro2 = [
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[0],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZB").codigo,
                     parametros=_agregacao("contagem_distinta:ZZB_COD")),
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[1],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZB").codigo,
                     parametros=_agregacao("contagem_distinta:ZZB_COD por ZZB_SITUAC categorias D")),
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[2],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZB").codigo,
                     parametros=_agregacao("contagem_distinta:ZZB_COD por ZZB_SITUAC categorias T")),
    ]
    p2 = Operador(roteiro2, revisor_pela_evidencia)
    msg2, res2 = conversas.enviar(user, c, "deslocado é o módulo em trânsito", p2)
    assert "Esclarecimento da pessoa" in p2.entradas[0].mensagem and PEDIDO in p2.entradas[0].mensagem
    assert _ok(res2), msg2.texto
    texto = _texto(res2)
    assert res2.colunas == ["Situação", "Quantidade"], texto  # M155: tabela compacta com data de referência
    assert [l[1] for l in res2.linhas] == ["6", "3", "1"], res2.linhas  # total, disponíveis, em trânsito (sem excluído)
    assert "Posição em" in res2.mensagem
    receita = NotaAgente.objects.filter(ativo=True, criterios__has_key="receita").exclude(
        criterios__receita__passos=None).order_by("-pk").first()
    assert receita is not None
    objetivos_receita = {p_["objetivo"] for p_ in receita.criterios["receita"]["passos"]}
    assert objetivos_receita == {ConsultaModelo.objects.get(tabela="ZZB").codigo}  # o método que funcionou

    # 3ª: pergunta equivalente em outra conversa recupera o método sem o modelo e recalcula com dados atuais.
    with sqlite3.connect(modulos) as db:
        db.execute("UPDATE ZZB010 SET ZZB_SITUAC = 'T' WHERE ZZB_COD = 'M00001'")
    p3 = Operador([], revisor_pela_evidencia)
    msg3, res3 = conversas.enviar(user, conversas.nova_individual(user), PEDIDO, p3)
    assert _ok(res3), msg3.texto
    assert not p3.entradas, "a receita verificada é o primeiro passo, sem o modelo"
    linhas3 = _texto(res3)
    assert "2" in linhas3, linhas3  # agora 2 em trânsito e 2 disponíveis


def test_descoberta_aceita_outra_fonte_justificada(modulos, novo_usuario, politica):
    """Com as tabelas em mãos, pedir outra fonte (com motivo) acrescenta tabela NOVA; pedir as mesmas não."""
    user = _usuario(novo_usuario, politica, "comercial")
    novas = agente._outras_fontes(user, "cadastro de modulos situacao", {"ZZM"}, PEDIDO)
    assert [t["tabela"] for t in novas][:1] == ["ZZB"]
    assert agente._outras_fontes(user, "ZZB ZZM", {"ZZB", "ZZM"}, PEDIDO) == []


@pytest.mark.parametrize("extra", [
    {"calculo": {"operacao": "", "mensagem": 0, "campo": "", "campo_final": "", "limite": 0}},
    {"calculo": {"operacao": "contagem", "limite": None}},
    {"formato": "tabela"}, {"formato": "completo"},
])
def test_campos_irrelevantes_nao_derrubam_a_decisao(extra):
    d = validar(_d(acao="preparar_consulta", **extra), [], [])
    assert d.calculo is None and d.formato == ""


def test_calcular_resultado_continua_exigindo_calculo_valido():
    with pytest.raises(DecisaoInvalida):
        validar(_d(acao="calcular_resultado", calculo={"operacao": "inventada"}), [], [])
    d = validar(_d(acao="calcular_resultado", calculo={"operacao": "soma", "mensagem": 0, "limite": 0}), [], [])
    assert d.calculo["operacao"] == "soma" and "limite" not in d.calculo


def test_etapa_restrita_no_esquema_e_na_validacao():
    from iativa.agent import decisao
    assert set(decisao.esquema([], [], agente.ETAPA_MONTAR)["properties"]["acao"]["enum"]) == set(agente.ETAPA_MONTAR)
    e = EntradaAgente("x", "2026-11-25", [], [], None, acoes_permitidas=agente.ETAPA_MONTAR)
    preparar = validar(_d(acao="preparar_consulta"), [], [])
    assert "não vale nesta etapa" in agente._fora_da_etapa(preparar, e)
    assert agente._fora_da_etapa(validar(_d(resposta="Vou consultar."), [], []), e)
    assert not agente._fora_da_etapa(validar(_d(resposta="O que a operação chama de deslocado?"), [], []), e)
    assert not agente._fora_da_etapa(preparar, replace(e, acoes_permitidas=()))
    assert "objetivo" in agente._correcao_do_campo("objetivo", e)


@pytest.mark.parametrize("falta,pergunta", [
    ("A resposta montada não atende: x. Como corrigir: Qual situação a operação chama de deslocado?",
     "Qual situação a operação chama de deslocado?"),
    ("A resposta montada não atende: x. Como corrigir: Qual campo da tabela ST9 indica o status?", ""),
    ("A resposta montada não atende: x. Como corrigir: Use T9_SITBEM = A?", ""),
    ("A resposta montada não atende: x. Como corrigir: Refaça pelo cadastro.", ""),
])
def test_pergunta_de_definicao_so_de_negocio(falta, pergunta):
    assert agente.pergunta_de_definicao(falta) == pergunta


# --- Generalização: outros setores, sem fluxo especial para "módulos" ---------------------------------------------

from .test_m95_novos_pedidos import AREAS  # noqa: E402
from .test_objetivos import Espiao  # noqa: E402


def _periodo(campo_data, valor):
    return [{"codigo": "periodo-analise", "valor": valor}] if campo_data else []


@pytest.mark.parametrize("setor", list(AREAS))
def test_pedido_inedito_por_setor_descoberta_restrita_continuacao_e_assunto_novo(setor, novo_usuario, politica):
    pedido, reformulado, tabela, colunas, campo_data = AREAS[setor]
    u = _usuario(novo_usuario, politica, setor)
    c = conversas.nova_individual(u)
    params = [{"codigo": "filial-analise", "valor": "01"}] + _periodo(campo_data, "01/01/2000..31/12/2030")
    p = Espiao([
        _d(acao="aprender_tabela", alvo=pedido),
        # Com as tabelas em mãos, o modelo tenta "responder" sem consultar e com campos opcionais sujos.
        _d(acao="conversar", resposta="Vou verificar.", calculo={"operacao": "", "limite": 0}, formato="completo"),
        _d(acao="criar_consulta", parametros=params,
           consulta={"tabela": tabela, "nome": f"Pedido {setor}", "colunas": colunas, "campo_data": campo_data}),
    ])
    msg, res = conversas.enviar(u, c, pedido, p)
    assert res is not None and res.ok, msg.texto
    restrita = [e for e in p.entradas if e.tabelas_para_aprender]
    assert restrita[-1].acoes_permitidas == agente.ETAPA_MONTAR
    assert any(o.get("situacao") == "etapa_errada" for o in restrita[-1].observacoes)
    assert any(t.get("natureza") for t in restrita[-1].tabelas_para_aprender)
    cm = ConsultaModelo.objects.get()
    # Continuação curta com outro período: a mesma consulta, dados atuais, sem nova descoberta.
    p2 = Espiao([_d(acao="preparar_consulta", objetivo=cm.codigo,
                    parametros=[{"codigo": "filial-analise", "valor": "TODAS"}]
                    + _periodo(campo_data, "01/01/2010..31/12/2025"))])
    msg2, res2 = conversas.enviar(u, c, reformulado, p2)
    assert res2 is not None and res2.ok, msg2.texto
    assert not any(e.tabelas_para_aprender for e in p2.entradas)
    assert ConsultaModelo.objects.count() == 1
    # Mudança de assunto na mesma conversa: não vira esclarecimento do pedido anterior.
    assert agente.pedido_esclarecido(c, "quantos títulos vencidos temos por filial hoje?") == \
        "quantos títulos vencidos temos por filial hoje?"


def test_tentativa_fora_da_permissao_nao_executa(novo_usuario, politica):
    from iativa.accounts.models import Department
    pedido, _, tabela, colunas, campo_data = AREAS["comercial"]
    dono = _usuario(novo_usuario, politica, "comercial")
    conversas.enviar(dono, conversas.nova_individual(dono), pedido, Espiao([
        _d(acao="aprender_tabela", alvo=pedido),
        _d(acao="criar_consulta", parametros=[{"codigo": "filial-analise", "valor": "01"}],
           consulta={"tabela": tabela, "nome": "Clientes", "colunas": colunas})]))
    cm = ConsultaModelo.objects.get()
    rh = novo_usuario("so-rh-154", Department.objects.get(codigo="rh"))
    politica(rh, Department.objects.get(codigo="rh"), consultar=True, filiais=["01"])
    p = Espiao([_d(acao="preparar_consulta", objetivo=cm.codigo, parametros=[{"codigo": "filial-analise", "valor": "01"}])] * 4)
    msg, res = conversas.enviar(rh, conversas.nova_individual(rh), pedido, p)
    assert res is None or not getattr(res, "ok", False), "a consulta de outro setor não pode executar"


def test_esclarecimento_so_retoma_com_resposta_curta(novo_usuario, politica):
    from iativa.chat.models import Mensagem, Papel
    u = _usuario(novo_usuario, politica, "comercial")
    c = conversas.nova_individual(u)
    Mensagem.objects.create(conversa=c, papel=Papel.USUARIO, autor=u, texto=PEDIDO)
    Mensagem.objects.create(conversa=c, papel=Papel.IA, texto=DEFINICAO,
                            ficha={"tipo": "esclarecimento", "pedido_original": PEDIDO})
    combinado = agente.pedido_esclarecido(c, "os que estão em trânsito")
    assert combinado.startswith(PEDIDO) and "os que estão em trânsito" in combinado
    assert agente.pedido_esclarecido(c, PEDIDO) == PEDIDO  # repetiu o pedido: não duplica
    novo = "quantos clientes bloqueados temos na filial 01 hoje?"
    assert agente.pedido_esclarecido(c, novo) == novo


def test_natureza_da_tabela_e_termos_da_empresa():
    from iativa.agent import aprendizado
    assert aprendizado.natureza_tabela("Calculo Ativa - Módulos/Header") == "calculo_ou_parametro"
    assert aprendizado.natureza_tabela("Movimentação de Bens") == "historico_ou_movimentacao"
    assert aprendizado.natureza_tabela("Bem", ("T9_FILIAL", "T9_CODBEM")) == "cadastro"
    assert "ATIVA" not in aprendizado._radicais(PEDIDO) - aprendizado.termos_empresa()
    assert "MODUL" in aprendizado._radicais(PEDIDO) - aprendizado.termos_empresa()


def test_cadastro_de_dominio_pelo_sx9():
    from types import SimpleNamespace as N
    from iativa.agent import aprendizado

    class Cat:
        def relacionados(self, alias):
            return [N(origem="TQY", campos_origem=("TQY_FILIAL", "TQY_STATUS"), campos_destino=("T9_FILIAL", "T9_STATUS")),
                    N(origem="SA1", campos_origem=("A1_COD", "A1_LOJA"), campos_destino=("T9_CLIENTE", "T9_LOJACLI"))]
    assert aprendizado.cadastros_de_dominio(Cat(), "ST9") == {"T9_STATUS": "TQY"}


def test_consulta_reprovada_duas_vezes_muda_de_fonte_mesmo_com_outro_parametro(modulos, novo_usuario, politica):
    user = _usuario(novo_usuario, politica, "comercial")
    zzm = lambda: ConsultaModelo.objects.get(tabela="ZZM").codigo  # noqa: E731
    roteiro = [
        _criar("ZZM", "contagem_distinta:ZZM_COD", [0, 1, 2], "Movimentacoes de modulos", ["ZZM_COD", "ZZM_TIPO"]),
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[0, 1, 2], objetivo=zzm(),
                     parametros=_agregacao("contagem:*")),
        # 3ª vez na mesma consulta, com outro parâmetro: o backend manda para a descoberta.
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[0, 1, 2], objetivo=zzm(),
                     parametros=_agregacao("contagem_distinta:ZZM_COD por ZZM_TIPO")),
        _cadastro,
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[1, 2],
                     objetivo=ConsultaModelo.objects.get(tabela="ZZB").codigo,
                     parametros=_agregacao("contagem_distinta:ZZB_COD por ZZB_SITUAC categorias D,T")),
    ]
    p = Operador(roteiro, revisor_pela_evidencia)
    msg, _ = conversas.enviar(user, conversas.nova_individual(user), PEDIDO, p)
    assert any(e.tabelas_para_aprender for e in p.entradas), "a 3ª tentativa equivalente virou descoberta"
    assert ConsultaModelo.objects.filter(tabela="ZZB").exists()
    assert msg.texto, msg.texto


def test_pergunta_reservada_cobranca_duas_medidas_por_filial(novo_usuario, politica, agregacao_definida):
    """Pergunta reservada até o fim da implementação: valor e quantidade de movimentos bancários, por filial, num
    período, sem receita pronta. Usuário com duas filiais."""
    from iativa.accounts.models import Department
    dep = Department.objects.get(codigo="financeiro-cobranca")
    u = novo_usuario("cob-154", dep)
    politica(u, dep, consultar=True, filiais=["01", "02"])
    politica(u, Department.objects.get(codigo="ti"), administrar=True)
    pedido = "Quanto entrou e quantos movimentos bancários tivemos de 2020 a 2026, separado por filial?"
    entregas = [{"descricao": "Valor total dos movimentos bancários por filial", "tipo": "calculo", "metrica": "soma"},
                {"descricao": "Quantidade de movimentos bancários por filial", "tipo": "calculo", "metrica": "contagem"}]
    p = Operador([
        _d(acao="aprender_tabela", alvo="movimentos bancários"),
        _d(acao="preparar_consulta", objetivo="", parametros=[]),  # fora da etapa
        _d(acao="criar_consulta", entrega="calculo", atende=[0, 1],
           parametros=[{"codigo": "filial-analise", "valor": "TODAS"},
                       {"codigo": "periodo-analise", "valor": "01/01/2020..31/12/2026"},
                       {"codigo": "agregacao", "valor": "soma:E5_VALOR, contagem:* por filial"}],
           consulta={"tabela": "SE5", "nome": "Movimentos bancarios", "colunas": ["E5_DATA", "E5_TIPODOC", "E5_VALOR"],
                     "campo_data": "E5_DATA"}),
    ], lambda plan: _aprovado(), entregas=entregas)
    msg, res = conversas.enviar(u, conversas.nova_individual(u), pedido, p)
    assert res is not None and res.ok, msg.texto
    assert res.colunas[0] == "Filial" and len(res.linhas) == 2, (res.colunas, len(res.linhas))
    assert any("Soma" in c or "Valor" in c for c in res.colunas) and any("Quantidade" in c for c in res.colunas), res.colunas
    assert res.evidencia["alcance"]["filiais_autorizadas"] == 2 and res.evidencia["entidade"].startswith("SE5")


def test_nome_do_codigo_pelo_titulo_de_descricao():
    from types import SimpleNamespace as N
    from iativa.query.nomes import _campo_nome
    tqy = N(alias="TQY", chave=("TQY_FILIAL", "TQY_STATUS"), campos=[
        N(nome="TQY_STATUS", titulo="Cod Status", tipo="C", consultavel=True),
        N(nome="TQY_DESTAT", titulo="Desc Status", tipo="C", consultavel=True),
        N(nome="TQY_CATBEM", titulo="Categ. Bem", tipo="C", consultavel=True)])
    assert _campo_nome(tqy, "TQY_STATUS", lambda a, c: True) == "TQY_DESTAT"


@pytest.fixture
def familias(modulos):
    """Família dos bens num cadastro ligado pelo SX9; nem todo bem do cadastro é módulo."""
    with sqlite3.connect(modulos) as db:
        db.execute("ALTER TABLE ZZB010 ADD COLUMN ZZB_FAMI TEXT")
        db.execute("""INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TITULO,X3_DESCRIC,X3_TIPO,X3_TAMANHO,X3_CBOX)
                      VALUES ('ZZB','ZZB_FAMI','Familia do bem','Familia do bem','C',2,'')""")
        db.execute("""INSERT INTO SX2010 (X2_CHAVE,X2_ARQUIVO,X2_NOME,X2_MODO,X2_MODOUN,X2_MODOEMP)
                      VALUES ('ZZF','ZZF010','Familias de bens','E','E','E')""")
        db.execute("INSERT INTO SIX010 (INDICE,ORDEM,CHAVE) VALUES ('ZZF','1','ZZF_FILIAL+ZZF_COD')")
        db.execute("CREATE TABLE ZZF010 (ZZF_FILIAL TEXT, ZZF_COD TEXT, ZZF_NOME TEXT, D_E_L_E_T_ TEXT DEFAULT ' ')")
        for campo, titulo, tam in (("ZZF_FILIAL", "Filial", 2), ("ZZF_COD", "Familia", 2), ("ZZF_NOME", "Nome", 30)):
            db.execute("""INSERT INTO SX3010 (X3_ARQUIVO,X3_CAMPO,X3_TITULO,X3_DESCRIC,X3_TIPO,X3_TAMANHO,X3_CBOX)
                          VALUES ('ZZF',?,?,?,'C',?,'')""", (campo, titulo, titulo, tam))
        db.executemany("INSERT INTO ZZF010 VALUES (?,?,?,' ')", [
            ("01", "F1", "MODULO HABITACIONAL"), ("01", "F2", "MÓDULO SANITÁRIO"), ("01", "F3", "FERRAMENTA")])
        db.execute("INSERT INTO SX9010 (X9_DOM, X9_IDENT, X9_CDOM, X9_EXPDOM, X9_EXPCDOM) "
                   "VALUES ('ZZF', '001', 'ZZB', 'ZZF_COD', 'ZZB_FAMI')")
        db.execute("UPDATE ZZB010 SET ZZB_FAMI = CASE WHEN ZZB_COD IN ('M00001','M00002','M00003') THEN 'F1' ELSE 'F2' END")
        db.executemany("INSERT INTO ZZB010 VALUES (?,?,?,?,?,?)", [
            ("01", "B00001", "FURADEIRA", "D", " ", "F3"), ("01", "B00002", "ESMERILHADEIRA", "D", " ", "F3")])
    return modulos


def test_termo_do_pedido_no_cadastro_de_dominio_so_por_contagem(familias, novo_usuario, politica):
    user = _usuario(novo_usuario, politica, "comercial")
    sonda = agente._termos_em_cadastros(user, "ZZB", PEDIDO)
    assert set(sonda) == {"ZZB_FAMI"}, sonda
    assert sonda["ZZB_FAMI"]["campo_nome"] == "ZZF_NOME"
    assert sonda["ZZB_FAMI"]["contagens"] == {"DESLO": 0, "DISPO": 0, "MODUL": 2}  # acento não impede
    campos = {c["campo"]: c for c in agente._campos_para_modelo(user, "ZZB", PEDIDO)["campos"]}
    assert campos["ZZB_FAMI"]["termos_do_pedido_no_cadastro"]["contagens"]["MODUL"] == 2
    assert "FERRAMENTA" not in str(sonda) and "HABITACIONAL" not in str(sonda)  # nenhum dado do ERP


def test_entidade_restrita_pela_familia_conta_so_modulos(familias, novo_usuario, politica):
    user = _usuario(novo_usuario, politica, "comercial")
    p = Operador([
        _d(acao="aprender_tabela", alvo="cadastro de modulos"),
        lambda e: _d(acao="criar_consulta", entrega="calculo", atende=[0],
                     parametros=_agregacao("contagem_distinta:ZZB_COD") + [
                         {"codigo": "filtro-campo", "valor": "ZZF_NOME~MODUL"}],
                     consulta={"tabela": "ZZB", "nome": "Modulos por familia", "colunas": ["ZZB_COD", "ZZB_SITUAC"],
                               "juncoes": [{"tabela": "ZZF", "colunas": ["ZZF_NOME"]}]}),
    ], lambda plan: _aprovado(), entregas=ENTREGAS[:1])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "quantos módulos tem hoje na ativa?", p)
    assert _ok(res), msg.texto
    assert "6" in _texto(res) and "8" not in _texto(res), _texto(res)  # 6 módulos, não os 8 bens


def test_primeiro_plano_conta_todos_os_bens_revisao_pede_so_modulos(familias, novo_usuario, politica):
    """M155: a revisão lê na evidência que a contagem não restringiu a entidade e diz o problema concreto; a
    segunda tentativa (outro critério: a família) conta só os módulos."""
    user = _usuario(novo_usuario, politica, "comercial")

    def revisor(plan):
        e = plan["resposta_montada"][0]["evidencia"]
        if not any("palavra iniciada por M?D?L" in s for f in e["filtros_aplicados"] for s in f["significado"]):
            return {"natureza": "dados", "entregas": [{"descricao": ENTREGAS[0]["descricao"], "tipo": "calculo",
                                                       "metrica": "contagem"}],
                    "resumo": "A consulta contou todos os bens e ainda não identificou quais são módulos."}
        return _aprovado()

    cad = lambda: ConsultaModelo.objects.get(tabela="ZZB").codigo  # noqa: E731
    p = Operador([
        _d(acao="aprender_tabela", alvo="cadastro de modulos"),
        _d(acao="criar_consulta", entrega="calculo", atende=[0], parametros=_agregacao("contagem_distinta:ZZB_COD"),
           consulta={"tabela": "ZZB", "nome": "Bens cadastrados", "colunas": ["ZZB_COD", "ZZB_SITUAC"],
                     "juncoes": [{"tabela": "ZZF", "colunas": ["ZZF_NOME"]}]}),
        lambda e: _d(acao="preparar_consulta", entrega="calculo", atende=[0], objetivo=cad(),
                     parametros=_agregacao("contagem_distinta:ZZB_COD") + [
                         {"codigo": "filtro-campo", "valor": "ZZF_NOME~MODUL"}]),
    ], revisor, entregas=ENTREGAS[:1])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "quantos módulos tem hoje na ativa?", p)
    assert _ok(res), msg.texto
    assert "6" in _texto(res) and "8" not in _texto(res), _texto(res)
    obs = [o.get("motivo", "") for e in p.entradas for o in (e.observacoes or [])]
    assert any("não identificou quais são módulos" in m for m in obs), "o problema concreto chega ao modelo"


def test_modulo_com_duas_movimentacoes_conta_uma_vez(modulos, novo_usuario, politica):
    """Duas movimentações do mesmo módulo (histórico) contam um módulo só, pela chave da unidade."""
    with sqlite3.connect(modulos) as db:
        db.execute("DELETE FROM ZZM010")
        db.executemany("INSERT INTO ZZM010 VALUES (?,?,?,?,?,?)", [
            ("01", "M00005", "001", "20261101", "T", " "), ("01", "M00005", "002", "20261120", "T", " "),
            ("01", "M00004", "001", "20261110", "T", " ")])
    user = _usuario(novo_usuario, politica, "comercial")
    p = Operador([
        _criar("ZZM", "contagem_distinta:ZZM_COD", [0], "Modulos transferidos", ["ZZM_COD", "ZZM_TIPO"]),
    ], lambda plan: _aprovado(), entregas=ENTREGAS[:1])
    msg, res = conversas.enviar(user, conversas.nova_individual(user), "quantos módulos tiveram transferência?", p)
    assert _ok(res), msg.texto
    assert res.totais.medidas[0].valor == 2  # 3 movimentações, 2 módulos


def test_mesma_consulta_com_outro_nome_nao_e_tentativa_nova():
    a = validar(_d(acao="criar_consulta", consulta={"tabela": "ZZB", "nome": "Modulos", "colunas": ["ZZB_COD"]}), [], [])
    b = validar(_d(acao="criar_consulta", consulta={"tabela": "ZZB", "nome": "Bens", "descricao": "x",
                                                    "colunas": ["ZZB_COD"]}), [], [])
    assert agente._assinatura(a) == agente._assinatura(b)


def test_literal_do_termo_tolerante_passa_no_sql_server():
    from iativa.query.executor import embutir_literais
    from iativa.query.plano import padrao_palavra
    assert padrao_palavra("módulo") == "M_D_L_"
    assert "'M_D_L_%'" in embutir_literais("SELECT 1 WHERE X LIKE ?", ("M_D_L_%",))


def test_pergunta_de_definicao_sem_o_diagnostico():
    falta = ("A resposta montada não atende: x. Como corrigir: A contagem por status cobre o total, mas as categorias "
             "não estão comprovadas. Quais status compõem disponível e quais compõem deslocado?")
    assert agente.pergunta_de_definicao(falta) == "Quais status compõem disponível e quais compõem deslocado?"


def _bloco_por_cadastro():
    return [{"titulo": "Módulos por status", "evidencia": {
        "agrupado_por": ["T9_STATUS (Status Bem)"], "categorias_pedidas": [],
        "classificacao_pelo_dominio": [{"campo": "T9_STATUS (Status Bem)",
                                        "dominio": ["códigos do cadastro TQY (ver nomes_ao_lado)"]}]}}]


def test_categorias_por_tabela_de_cadastro_viram_levantamento_e_pergunta():
    from iativa.agent import revisao
    contrato = [{"descricao": d, "tipo": "calculo", "metrica": "contagem"} for d in (
        "Quantidade total de módulos hoje na empresa", "Quantidade de módulos disponíveis hoje",
        "Quantidade de módulos deslocados hoje")]
    falta = revisao.classificacao_sem_definicao(contrato, _bloco_por_cadastro(), PEDIDO)
    pergunta = agente.pergunta_de_definicao(falta)
    assert pergunta.startswith("Na contagem por status bem") and "“disponíveis”" in pergunta and "“deslocados”" in pergunta
    # Com a definição já dada na conversa, a correção pede a contagem filtrada (sem perguntar de novo).
    com = revisao.classificacao_sem_definicao(contrato, _bloco_por_cadastro(),
                                              PEDIDO + "\nEsclarecimento da pessoa sobre “x”: em trânsito")
    assert com and not agente.pergunta_de_definicao(com) and "filtro-campo" in com
    # Uma entrega só, ou categorias do domínio do dicionário, não disparam.
    assert not revisao.classificacao_sem_definicao(contrato[:1], _bloco_por_cadastro(), PEDIDO)
    estatico = _bloco_por_cadastro()
    estatico[0]["evidencia"]["categorias_pedidas"] = ["Disponivel"]
    assert not revisao.classificacao_sem_definicao(contrato, estatico, PEDIDO)


def test_levantamento_agrupado_depende_de_aprovar_a_entidade(monkeypatch):
    from types import SimpleNamespace
    from iativa.agent import revisao

    contrato = [{"descricao": d, "tipo": "calculo", "metrica": "contagem"} for d in (
        "Quantidade total de módulos hoje na empresa", "Quantidade de módulos disponíveis hoje",
        "Quantidade de módulos deslocados hoje")]
    monkeypatch.setattr(revisao, 'descrever', lambda _: [{**_bloco_por_cadastro()[0], "medida_calculada": "contagem"}])
    modelo = SimpleNamespace(planejar=lambda _: {
        "entregas": [contrato[0]], "resumo": "O levantamento contou todos os bens. Quais deles são módulos?"})
    falta = revisao.revisar(None, modelo, PEDIDO, contrato, None)
    assert not isinstance(falta, revisao.ClassificacaoPendente)
    modelo.planejar = lambda _: {"entregas": []}
    assert isinstance(revisao.revisar(None, modelo, PEDIDO, contrato, None), revisao.ClassificacaoPendente)
    monkeypatch.setattr(agente.prazo, 'restante', lambda: 5)
    assert revisao.revisar(None, modelo, PEDIDO, contrato, None) == revisao.INDISPONIVEL
