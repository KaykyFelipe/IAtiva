"""Roteiro reproduzível de avaliação do agente com o MODELO REAL (M22).

Os testes com `FakeProvider` provam o fluxo; este comando mede a interpretação: faz perguntas em
português ao provedor configurado e confere a resposta contra uma referência calculada direto no
ERP SINTÉTICO (nunca no Protheus). Nada fica gravado: cada caso roda numa transação desfeita.

Uso:  python manage.py avaliar_agente --usuario <login> --confirmar-envio
Exige: IATIVA_METADADOS_FONTE=sintetico, provedor de IA real e liberação de envio já em vigor.
"""
import sqlite3
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

CASOS = [
    {"id": "soma", "pergunta": "Some o valor de todos os títulos a receber da filial {f1}, sem filtro de período.",
     "espera": "soma", "filiais": 1},
    {"id": "quantidade", "pergunta": "Quantos títulos a receber existem na filial {f2}?", "espera": "quantidade",
     "filiais": 1, "filial": "f2"},
    {"id": "media-filiais", "pergunta": "Qual a média do valor dos títulos a receber considerando todas as filiais?",
     "espera": "media", "filiais": 0},
    {"id": "ranking", "pergunta": "Quais os 3 clientes com maior valor de títulos na filial {f1}?", "espera": "ranking",
     "filiais": 1},
    {"id": "vazio", "pergunta": "Some os títulos a receber emitidos em 1990 na filial {f1}.", "espera": "vazio",
     "filiais": 1},
    {"id": "sem-permissao", "pergunta": "Liste os títulos a receber da filial 99.", "espera": "negado", "filiais": 1},
]


class Command(BaseCommand):
    help = "Avalia o agente com o modelo real contra referências do ERP sintético (nada é gravado)."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", required=True)
        parser.add_argument("--confirmar-envio", action="store_true",
                            help="Confirma que as perguntas (sem dados do ERP) podem ir ao provedor de IA.")

    def _referencia(self, filiais):
        marcas = ",".join("?" for _ in filiais)
        with sqlite3.connect(settings.SYNTHETIC_ERP_PATH) as conn:
            soma, n = conn.execute(f"SELECT SUM(E1_VALOR), COUNT(*) FROM SE1010 WHERE E1_FILIAL IN ({marcas}) "
                                   "AND D_E_L_E_T_ = ' '", filiais).fetchone()
        return Decimal(str(soma or 0)).quantize(Decimal("0.01")), n

    def _conferir(self, caso, res, msg, filiais):
        espera = caso["espera"]
        if espera == "negado":
            return res is None, "sem resultado, com explicação" if res is None else "devolveu dados"
        if espera == "vazio":
            ok = res is not None and res.ok and not res.linhas
            return ok, "nenhum registro" if ok else "esperava resultado vazio"
        if res is None or not res.ok or res.totais is None:
            return False, f"sem resultado: {msg.texto[:160]}"
        soma, n = self._referencia(filiais)
        q = Decimal("0.01")
        if espera == "quantidade":
            return res.totais.registros == n, f"registros {res.totais.registros} (referência {n})"
        funcao = {"soma": "SUM", "ranking": "SUM", "media": "AVG"}[espera]
        medida = next((m for m in res.totais.medidas if m.funcao == funcao and m.valor is not None), None)
        if medida is None:
            return False, f"o cálculo pedido ({funcao}) não foi feito"
        alvo = soma if funcao == "SUM" else (soma / n).quantize(q)
        ok = Decimal(medida.valor).quantize(q) == alvo and res.totais.registros == n
        if espera == "ranking":
            ok = ok and len(res.linhas) == 3
        return ok, f"{medida.rotulo} = {medida.texto} (referência {alvo}); registros {res.totais.registros}/{n}"

    def _cenario_vinculos(self, user, provedor):
        """M25: lista de desligados → pergunta com um exemplo de possível readmissão → a IA precisa
        investigar TODOS (tarefa), não só o exemplo. Referência: generator.FUNCIONARIOS."""
        from iativa.agent import tarefas
        from iativa.agent.models import Tarefa
        from iativa.chat import conversas
        from iativa.synthetic_erp.generator import FUNCIONARIOS

        lista = {n for _, _, n, _, _, dem, _, _, _, exc in FUNCIONARIOS if not exc and "20260901" <= dem <= "20261112"}
        with transaction.atomic():
            try:
                c = conversas.nova_individual(user)
                _, res = conversas.enviar(user, c, "Liste os funcionários desligados entre 01/09/2026 e 12/11/2026 em "
                                          "todas as filiais, com nome, admissão e demissão.", provedor)
                if res is None or not res.ok or len(res.linhas) != len(lista):
                    transaction.set_rollback(True)
                    return False, "a lista inicial não veio completa"
                msg, _ = conversas.enviar(user, c, "Você consegue verificar se algum deles teve o cargo trocado? O BRUNO "
                                          "SINTETICO LIMA aparece como demitido, mas acho que foi readmitido porque era "
                                          "estagiário e foi efetivado. Quero confirmar se todos que estão aí foram "
                                          "demitidos mesmo.", provedor)
                t = Tarefa.objects.filter(conversa=c).first()
                if t is None:
                    transaction.set_rollback(True)
                    return False, f"não virou tarefa da lista inteira: {msg.texto[:160]}"
                tarefas.executar(tarefas.reivindicar())
                t.refresh_from_db()
                concl = tarefas.conclusao(user, t)
                nomes = {it.reg.nome for it in concl.itens}
                bruno = next((it for it in concl.itens if it.reg.nome.startswith("BRUNO")), None)
                ok = (t.situacao == "concluida" and nomes == lista and concl.cobertura["sem_classificacao"] == 0
                      and bruno is not None and bruno.classe in ("novo_vinculo_posterior", "mesmo_nome")
                      and "efetivado" not in bruno.texto.lower())
                detalhe = (f"situação {t.situacao}; cobertura {concl.cobertura['verificados']}+"
                           f"{concl.cobertura['inconclusivos']} de {len(lista)}; exemplo: {bruno.classe if bruno else '-'}")
            except Exception as exc:
                ok, detalhe = False, f"erro: {type(exc).__name__}"
            transaction.set_rollback(True)
        return ok, detalhe

    def handle(self, *args, **opts):
        from iativa.accounts.authz import Permissao, filiais_autorizadas, setor_do_usuario
        from iativa.agent.provedores import obter_provedor
        from iativa.chat import conversas

        if settings.IATIVA_METADADOS.get("fonte") != "sintetico":
            raise CommandError("A avaliação só roda contra o ERP sintético (IATIVA_METADADOS_FONTE=sintetico).")
        if not opts["confirmar_envio"]:
            raise CommandError("Use --confirmar-envio: as perguntas do roteiro serão enviadas ao provedor de IA.")
        user = get_user_model().objects.get(username=opts["usuario"])
        setor = setor_do_usuario(user)
        if setor is None:
            raise CommandError("Usuário sem setor.")
        autorizadas = sorted(filiais_autorizadas(user, setor))
        if len(autorizadas) < 2:
            raise CommandError("O usuário precisa de pelo menos 2 filiais autorizadas no ERP sintético.")
        provedor = obter_provedor(usuario=user)
        if provedor.nome == "teste":
            raise CommandError("Provedor de teste configurado: a avaliação exige o modelo real.")
        nomes = {"f1": autorizadas[0], "f2": autorizadas[1]}
        aprovados = 0
        for caso in CASOS:
            filiais = autorizadas if not caso["filiais"] else [nomes[caso.get("filial", "f1")]]
            with transaction.atomic():
                c = conversas.nova_individual(user)
                try:
                    msg, res = conversas.enviar(user, c, caso["pergunta"].format(**nomes), provedor)
                    ok, detalhe = self._conferir(caso, res, msg, filiais)
                except Exception as exc:  # um caso não derruba o roteiro
                    ok, detalhe = False, f"erro: {type(exc).__name__}"
                transaction.set_rollback(True)  # nada do roteiro fica gravado
            aprovados += ok
            self.stdout.write(f"[{'OK' if ok else 'FALHOU'}] {caso['id']}: {detalhe}")
        ok, detalhe = self._cenario_vinculos(user, provedor)
        aprovados += ok
        self.stdout.write(f"[{'OK' if ok else 'FALHOU'}] vinculos-lista-inteira: {detalhe}")
        total = len(CASOS) + 1
        self.stdout.write(f"{aprovados}/{total} casos conferidos com a referência. Permissão usada: "
                          f"{Permissao.CONSULTAR}; provedor: {provedor.nome}.")
        if aprovados != total:
            raise CommandError("Avaliação com falhas.")
