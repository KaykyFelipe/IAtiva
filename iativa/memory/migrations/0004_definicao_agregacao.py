"""Cálculo (opcional) nas consultas aprendidas: somar, contar, média, maior, menor, por grupo, top N."""
from django.db import migrations

DADOS = {
    "nome": "Cálculo",
    "descricao": "Conta a fazer sobre a consulta, no formato FUNCAO:CAMPO (funções: soma, media, contagem, "
                 "maximo, minimo; várias separadas por vírgula), opcionalmente 'por CAMPO' (ou 'por mes', "
                 "'por ano') e 'top N'. Ex.: 'soma:F2_VALBRUT por F2_CLIENTE top 10'. Opcional: só quando a "
                 "pessoa pede total, média, ranking ou agrupamento.",
    "tipo": "texto",
    "contem_dado_pessoal": False,
}


def criar(apps, schema_editor):
    ParameterDefinition = apps.get_model("iativa_memory", "ParameterDefinition")
    ParameterDefinition.objects.get_or_create(codigo="agregacao", defaults=DADOS)


class Migration(migrations.Migration):
    dependencies = [("iativa_memory", "0003_definicao_filtro_campo")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
