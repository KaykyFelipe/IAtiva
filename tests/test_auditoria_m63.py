"""M63: defeitos reproduzidos na auditoria de 17/11/2026 (entradas sintéticas)."""
import pytest

from iativa.query.validador import ConsultaRecusada, validar

PERMITIDAS = {"SE1010", "SA1010"}
JOIN = ("LEFT JOIN SA1010 AS a1 ON a1.A1_FILIAL = ? AND a1.A1_COD = e1.E1_CLIENTE AND a1.D_E_L_E_T_ = ' '")


@pytest.mark.parametrize("onde", [
    "e1.D_E_L_E_T_ = ' ' AND (e1.E1_FILIAL = ? OR 1 = 1)",          # filial dentro de OR sempre verdadeiro
    "e1.D_E_L_E_T_ = '*' AND e1.E1_FILIAL = ?",                       # exclusão lógica com valor errado
    "NOT (e1.D_E_L_E_T_ = ' ') AND e1.E1_FILIAL = ?",                 # negação
    "e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = e1.E1_FILIAL",            # autoigualdade
    "(e1.D_E_L_E_T_ = ' ' OR e1.E1_NUM = '1') AND e1.E1_FILIAL = ?",  # exclusão dentro de OR
    "e1.D_E_L_E_T_ = ' ' AND a1.A1_FILIAL = ?",                       # filial de outra tabela
])
def test_validador_exige_restricao_efetiva(onde):
    with pytest.raises(ConsultaRecusada):
        validar(f"SELECT TOP 10 e1.E1_NUM AS c0 FROM SE1010 AS e1 {JOIN} WHERE {onde}", PERMITIDAS, 10)


def test_validador_recusa_filtro_da_juncao_dentro_de_or():
    sql = ("SELECT TOP 10 e1.E1_NUM AS c0 FROM SE1010 AS e1 LEFT JOIN SA1010 AS a1 ON (a1.A1_FILIAL = ? OR 1 = 1) "
           "AND a1.D_E_L_E_T_ = ' ' WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ?")
    with pytest.raises(ConsultaRecusada):
        validar(sql, PERMITIDAS, 10)


def test_validador_recusa_top_zero():
    with pytest.raises(ConsultaRecusada):
        validar(f"SELECT TOP 0 e1.E1_NUM AS c0 FROM SE1010 AS e1 {JOIN} WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ?",
                PERMITIDAS, 10)


def test_validador_aceita_a_forma_do_compilador():
    validar(f"SELECT TOP 10 e1.E1_NUM AS c0 FROM SE1010 AS e1 {JOIN} WHERE e1.D_E_L_E_T_ = ' ' AND e1.E1_FILIAL = ? "
            "AND (e1.E1_TIPO = 'NF' OR e1.E1_TIPO = 'FT')", PERMITIDAS, 10)


# --- 3. coluna parecida não prova o dado ----------------------------------------------------------

def test_valor_original_nao_e_saldo_nem_nome_abreviado_e_nome_completo():
    from iativa.agent.entregas import faltam
    assert faltam(["Valor original"], ["saldo"]) == ["saldo"]
    assert faltam(["Nome abreviado"], ["nome"]) == ["nome"]
    assert faltam(["Nome abreviado"], ["nome_completo"]) == ["nome_completo"]
    assert faltam(["Saldo em aberto"], ["saldo"]) == []
    # O campo do dicionário decide quando conhecido: rótulo "Valor" com E1_SALDO é saldo; "Saldo" com E1_VALOR não.
    assert faltam([{"campo": "E1_SALDO", "rotulo": "Valor"}], ["saldo"]) == []
    assert faltam([{"campo": "E1_VALOR", "rotulo": "Saldo"}], ["saldo"]) == ["saldo"]
    assert faltam([{"campo": "RA_NOME", "rotulo": "Nome"}], ["nome_completo"]) == ["nome_completo"]
    assert faltam([{"campo": "RA_NOMECMP", "rotulo": "Nome complet"}], ["nome_completo"]) == []


# --- 4. ativos x inativos -------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("liste os funcionários ativos com nome e função", True),
    ("liste os funcionários inativos com nome", False),
    ("quais funcionários desligados, com nome", False),
    ("funcionários que não estão ativos, com nome", False),
    ("liste os funcionários com nome", False),
])
def test_ativos_nao_casa_inativos(texto, esperado):
    from iativa.agent.entregas import quer_ativos
    assert quer_ativos(texto) is esperado


# --- 2. pedidos mistos não perdem entregas ----------------------------------------------------------

def test_lista_com_calculo_preserva_o_calculo():
    from iativa.agent import entregas
    analise = {"entregas": [{"descricao": "Listar clientes", "tipo": "consulta", "metrica": ""},
                            {"descricao": "Diferença entre os vencimentos", "tipo": "calculo", "metrica": "diferenca"}]}
    texto = "Liste os clientes e calcule a diferença entre os vencimentos"
    assert [e["tipo"] for e in entregas.planejar(texto, analise)["entregas"]] == ["consulta", "calculo"]


def test_comparacao_preserva_outra_pergunta_da_mensagem():
    from iativa.agent import comparacao
    analise = {"entregas": [{"descricao": "Comparar faturamento de agosto e setembro", "tipo": "calculo", "metrica": ""},
                            {"descricao": "Listar funcionários admitidos com nome", "tipo": "consulta", "metrica": ""}]}
    texto = "compare o faturamento de agosto com setembro e liste os funcionários admitidos com nome"
    out = comparacao.planejar(texto, analise)["entregas"]
    assert any(e.get("comparacao") for e in out)
    assert any(e["tipo"] == "consulta" and "funcion" in e["descricao"].lower() for e in out)
