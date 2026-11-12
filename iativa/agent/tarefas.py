"""Executor de tarefas longas do agente (M25).

Uma tarefa é criada na requisição do chat e executada pelo worker (`manage.py executar_tarefas`),
fora do processo web: fechar a página não interrompe nada.

Controle de execução:
- reivindicação por compare-and-swap no banco (`execucao` + `lease_ate`): dois workers nunca
  executam a mesma tarefa; um worker que morre perde a vez quando o lease vence;
- a cada operação o executor renova o lease e confere se ainda é o dono, se houve pedido de
  cancelamento, o prazo total, os limites e as permissões;
- as evidências ficam só na memória desta execução. Se outra execução assume (reinício), elas
  não existem mais: as etapas de dados voltam a pendente e a lista é consultada de novo.

Persistido: plano, etapas, contagens de cobertura, consumo e códigos de situação. Nunca dados do ERP.
"""
import logging
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from iativa.audit.servicos import registrar

from . import investigacao as inv
from . import prazo
from .models import SituacaoTarefa as S, Tarefa
from .provedores import FalhaModelo, obter_provedor

logger = logging.getLogger(__name__)

ETAPAS = [
    ("permissoes", "Conferir permissões e os critérios da lista", []),
    ("lista", "Consultar de novo a lista original", ["permissoes"]),
    ("relacionados", "Buscar outros vínculos de todas as pessoas, em lote", ["lista"]),
    ("classificar", "Comparar datas, situação e cargo de cada pessoa", ["relacionados"]),
    ("cobertura", "Conferir se todos da lista foram verificados", ["classificar"]),
]
DADOS = ("lista", "relacionados", "classificar", "cobertura")  # dependem de evidências em memória
CRITERIO = ("Todos os integrantes da lista original verificados e classificados com base nas evidências, ou "
            "marcados como inconclusivos com o motivo.")


def limites() -> dict:
    return {"prazo_s": 900, "chamadas_modelo": 4, "consultas": 600, "tentativas": 3, "registros": 2000,
            "lote": 40, "lease_s": 90, **getattr(settings, "IATIVA_TAREFAS", {})}


class PerdeuExecucao(RuntimeError):
    """Outra execução assumiu a tarefa (lease vencido): esta para sem gravar nada."""


def criar(user, conversa, base, proposta: dict, plano: dict, objetivo: str, criterios: list) -> Tarefa:
    return Tarefa.objects.create(
        usuario=user, conversa=conversa, solicitacao_base=base, objetivo=objetivo[:300], proposta=proposta,
        plano=plano, criterio_conclusao=CRITERIO, limites=limites(),
        escopo={"consulta": base.objetivo, "criterios": criterios},
        etapas=[{"id": i, "nome": n, "depende": d, "estado": "pendente", "tentativas": 0, "evidencia": ""}
                for i, n, d in ETAPAS],
        consumo={"consultas": 0, "chamadas_modelo": 0}, progresso="Na fila para começar.",
    )


def cancelar(user, tarefa_id) -> Tarefa:
    t = Tarefa.objects.get(pk=tarefa_id, usuario=user)
    if t.terminal:
        return t
    Tarefa.objects.filter(pk=t.pk).update(cancelar_solicitado=True)
    # Ainda na fila: cancela na hora; em execução: o worker para antes da próxima operação.
    Tarefa.objects.filter(pk=t.pk, situacao=S.PENDENTE).update(
        situacao=S.CANCELADA, progresso="Cancelada antes de começar.", concluido_em=timezone.now())
    t.refresh_from_db()
    return t


def reivindicar() -> Tarefa | None:
    agora = timezone.now()
    lease = timedelta(seconds=int(limites()["lease_s"]))
    candidatas = Tarefa.objects.filter(Q(situacao=S.PENDENTE) | Q(situacao=S.EXECUTANDO, lease_ate__lt=agora)).order_by("id")
    for t in candidatas[:5]:
        nova = uuid.uuid4().hex
        n = Tarefa.objects.filter(pk=t.pk, execucao=t.execucao, situacao=t.situacao).filter(
            Q(lease_ate__isnull=True) | Q(lease_ate__lt=agora) | Q(situacao=S.PENDENTE)).update(
            execucao=nova, situacao=S.EXECUTANDO, lease_ate=agora + lease, execucoes=F("execucoes") + 1)
        if n == 1:
            t.refresh_from_db()
            return t
    return None


class _Controle(inv.Controle):
    def __init__(self, tarefa: Tarefa):
        super().__init__(tarefa.limites)
        self.t = tarefa
        self.consultas = int(tarefa.consumo.get("consultas", 0))

    def renovar(self, **campos):
        lease = timezone.now() + timedelta(seconds=int(self.limites.get("lease_s", 90)))
        n = Tarefa.objects.filter(pk=self.t.pk, execucao=self.t.execucao).update(lease_ate=lease, **campos)
        if n != 1:
            raise PerdeuExecucao()

    def checar(self):
        if Tarefa.objects.filter(pk=self.t.pk, cancelar_solicitado=True).exists():
            raise inv.Cancelada()
        self.renovar()
        super().checar()

    def antes_de_consultar(self):
        super().antes_de_consultar()
        self.t.consumo = {**self.t.consumo, "consultas": self.consultas}
        self.renovar(consumo=self.t.consumo)

    def progresso(self, texto: str):
        self.t.progresso = texto[:300]
        self.renovar(progresso=self.t.progresso)

    def corrigir(self, texto: str):
        """Correção feita sozinha: fica visível no cartão (só texto operacional, sem dados do ERP)."""
        correcoes = (list(self.t.consumo.get("correcoes", [])) + [texto[:200]])[-8:]
        self.t.consumo = {**self.t.consumo, "correcoes": correcoes}
        self.renovar(consumo=self.t.consumo)
        self.progresso("Corrigindo: " + texto)


def _salvar(t: Tarefa, **campos):
    for k, v in campos.items():
        setattr(t, k, v)
    n = Tarefa.objects.filter(pk=t.pk, execucao=t.execucao).update(**campos)
    if n != 1:
        raise PerdeuExecucao()


def _etapa(t, eid, **mud):
    etapas = [dict(e, **mud) if e["id"] == eid else e for e in t.etapas]
    _salvar(t, etapas=etapas)


def _proxima(t):
    feitas = {e["id"] for e in t.etapas if e["estado"] == "concluida"}
    return next((e for e in t.etapas if e["estado"] in ("pendente", "em_andamento")
                 and set(e["depende"]) <= feitas), None)


def _replanejar(t, motivo: str) -> bool:
    """Falha corrigível no plano (campo que não existe, tipo errado): o modelo escolhe outros campos,
    dentro do limite de chamadas. Devolve False se não houver como."""
    from datetime import date

    from .agente import PARAMETROS_APRENDIDOS, _interpretar
    from .decisao import EntradaAgente

    usadas = int(t.consumo.get("chamadas_modelo", 0))
    if usadas >= int(t.limites.get("chamadas_modelo", 4)):
        return False
    base = t.solicitacao_base
    lista = inv.contexto_para_modelo(t.usuario, base)
    if lista is None:
        return False
    _salvar(t, consumo={**t.consumo, "chamadas_modelo": usadas + 1}, progresso="Ajustando o plano: " + motivo[:200])
    entrada = EntradaAgente(
        mensagem=t.objetivo, hoje=date.today().isoformat(), historico=[], objetivos=[], solicitacao_atual=None,
        lista_anterior=lista, observacoes=[{"acao": "investigar_lista", "situacao": "erro_parametros", "motivo": motivo}])
    try:
        d = _interpretar(obter_provedor(usuario=t.usuario), entrada, [], list(PARAMETROS_APRENDIDOS))
    except (FalhaModelo, prazo.PrazoEsgotado):
        return False
    if d.acao == "conversar" and d.resposta.strip().endswith("?"):
        _salvar(t, situacao=S.AGUARDANDO, motivo=d.resposta[:400], concluido_em=timezone.now())
        return True
    if d.acao != "investigar_lista" or not d.investigacao or d.investigacao == t.proposta:
        return False  # sem caminho novo: repetir o mesmo plano só repetiria a falha
    _salvar(t, proposta=d.investigacao)
    return True


def _fim(t, situacao, motivo=""):
    _salvar(t, situacao=situacao, motivo=motivo[:400], concluido_em=timezone.now(), lease_ate=None)


def executar(t: Tarefa):
    """Roda as etapas até concluir, precisar da pessoa, bater num bloqueio ou num limite."""
    lim = t.limites or limites()
    if t.execucoes > 1:
        # Reinício: a execução anterior morreu e levou as evidências; refaz o que depende delas.
        etapas = [dict(e, estado="pendente", evidencia="") if e["id"] in DADOS or e["estado"] == "em_andamento"
                  else e for e in t.etapas]
        _salvar(t, etapas=etapas, reconstruida=True, progresso="Retomando: a lista será consultada de novo.")
    if t.iniciado_em is None:
        _salvar(t, iniciado_em=timezone.now())
    restante = float(lim["prazo_s"]) - (timezone.now() - t.iniciado_em).total_seconds()
    ctl, ctx = _Controle(t), inv.Contexto()
    inicio = time.monotonic()
    user, base = t.usuario, t.solicitacao_base
    try:
        with prazo.definir(max(0.0, restante)):
            while True:
                ctl.checar()
                e = _proxima(t)
                if e is None:
                    break
                _etapa(t, e["id"], estado="em_andamento")
                ctl.progresso(e["nome"] + ".")
                try:
                    if e["id"] == "permissoes":
                        if base is None:
                            raise inv.InvestigacaoInvalida("A lista original não existe mais.")
                        _salvar(t, plano=inv.validar_plano(user, base, t.proposta))
                    elif e["id"] == "lista":
                        inv.reconstruir_lista(user, base, t.plano, ctx, ctl)
                    elif e["id"] == "relacionados":
                        inv.conferir_permissao(user, base)
                        inv.buscar_relacionados(user, base, t.plano, ctx, ctl)
                        if ctx.lote_usado and ctx.lote_usado != t.plano.get("lote"):
                            _salvar(t, plano={**t.plano, "lote": ctx.lote_usado})  # só o tamanho, sem dados
                    elif e["id"] == "classificar":
                        inv.classificar(t.plano, ctx)
                        cob = inv.cobertura(ctx)
                        ctl.progresso(f"Verificação concluída para {cob['verificados'] + cob['inconclusivos']} de "
                                      f"{cob['total']} pessoas.")
                    elif e["id"] == "cobertura":
                        cob = inv.cobertura(ctx)
                        _salvar(t, cobertura=cob)
                        if cob["sem_classificacao"]:
                            raise inv.LimiteAtingido(f"{cob['sem_classificacao']} pessoa(s) ficaram sem classificação.")
                        _salvar(t, conclusao=inv.para_gravar(inv.concluir(t.plano, ctx, refeita=False)))
                        if not cob["lista_completa"]:
                            raise inv.LimiteAtingido(
                                f"A lista passou do limite de {lim['registros']} registros: {cob['total']} verificados; "
                                "os demais ficaram de fora.")
                except inv.InvestigacaoInvalida as exc:
                    _etapa(t, e["id"], estado="pendente")
                    if _replanejar(t, str(exc)):
                        if t.situacao == S.AGUARDANDO:
                            return
                        t.etapas = [dict(x, estado="pendente", evidencia="") for x in t.etapas]
                        _salvar(t, etapas=t.etapas)
                        continue
                    _etapa(t, e["id"], estado="bloqueada")
                    return _fim(t, S.BLOQUEADA, str(exc))
                except Exception as exc:
                    if isinstance(exc, (inv.SemPermissao, inv.Cancelada, inv.LimiteAtingido, PerdeuExecucao,
                                        prazo.PrazoEsgotado)):
                        raise
                    from iativa.query.executor import tipo_falha

                    passageira = bool(tipo_falha(exc)) or isinstance(exc, (inv.Transitoria, ConnectionError, OSError))
                    o_que = str(exc) if isinstance(exc, inv.Transitoria) else (
                        "o ERP não respondeu" if passageira else f"erro inesperado ({type(exc).__name__})")
                    tent = e["tentativas"] + 1
                    maximo = int(lim.get("tentativas", 3))
                    _etapa(t, e["id"], estado="pendente", tentativas=tent)
                    if tent < maximo:
                        ctl.corrigir(f"{e['nome']}: {o_que}; tentando de novo ({tent + 1}/{maximo}).")
                        time.sleep(min(2 * tent, 6) if passageira else 0)
                        continue
                    if not passageira and _replanejar(t, f"A etapa “{e['nome']}” falhou {tent} vezes ({o_que}). "
                                                         "Escolha outros campos ou outro caminho."):
                        if t.situacao == S.AGUARDANDO:
                            return
                        ctl.corrigir(f"{e['nome']}: refiz o plano depois de {tent} falhas.")
                        t.etapas = [dict(x, estado="pendente", evidencia="", tentativas=0) for x in t.etapas]
                        _salvar(t, etapas=t.etapas)
                        continue
                    _etapa(t, e["id"], estado="bloqueada")
                    return _fim(t, S.FALHOU, f"{e['nome']}: {o_que}. Tentei {tent} vezes"
                                             + (" e pedi outro caminho ao modelo, sem solução" if not passageira else "")
                                             + "; nada foi concluído pela metade. Tente de novo em alguns minutos.")
                ev = f"memória da execução {t.execucao[:8]}" if e["id"] in DADOS else ""
                _etapa(t, e["id"], estado="concluida", evidencia=ev)
        cob = t.cobertura
        _fim(t, S.CONCLUIDA)
        _salvar(t, progresso=f"Concluída: {cob['total']} de {cob['total']} pessoas com classificação "
                             f"({cob['inconclusivos']} inconclusiva(s)).")
    except PerdeuExecucao:
        logger.info("tarefa %s: outra execução assumiu", t.pk)
        return
    except inv.Cancelada:
        _fim(t, S.CANCELADA, "Cancelada a pedido; nenhuma consulta nova foi feita depois disso.")
    except inv.SemPermissao as exc:
        _marcar_atual(t, "bloqueada")
        _fim(t, S.BLOQUEADA, str(exc))
    except (inv.LimiteAtingido, prazo.PrazoEsgotado) as exc:
        _marcar_atual(t, "bloqueada")
        cob = inv.cobertura(ctx) if ctx.itens else t.cobertura
        _salvar(t, cobertura=cob)
        _fim(t, S.LIMITE, str(exc) or "O prazo total da tarefa acabou.")
    except Exception as exc:  # sem detalhes: podem conter dados
        logger.warning("tarefa %s falhou: %s", t.pk, type(exc).__name__)
        try:
            _marcar_atual(t, "bloqueada")
            _fim(t, S.FALHOU, f"Erro inesperado ({type(exc).__name__}).")
        except PerdeuExecucao:
            return
    finally:
        ctx.itens.clear()
        ctx.relacionados.clear()
    _avisar(t, int((time.monotonic() - inicio) * 1000))


def _marcar_atual(t, estado):
    atual = next((e for e in t.etapas if e["estado"] == "em_andamento"), None)
    if atual is not None:
        _etapa(t, atual["id"], estado=estado)


MENSAGENS = {
    S.CONCLUIDA: "Verificação concluída: {feitos} de {total} pessoas da lista com classificação ({inc} inconclusiva(s)).",
    S.LIMITE: "Parei antes de terminar: {motivo} Cobertura: {feitos} de {total}.",
    S.BLOQUEADA: "Não consegui concluir a verificação: {motivo}",
    S.FALHOU: "A verificação falhou: {motivo}",
    S.CANCELADA: "Verificação cancelada.",
    S.AGUARDANDO: "Preciso de uma informação para continuar: {motivo}",
}


def _avisar(t, duracao_ms):
    """Mensagem no histórico só com contagens e situação (sem nomes nem valores)."""
    from iativa.chat.models import Mensagem, Papel

    t.refresh_from_db()
    cob = t.cobertura or {}
    texto = MENSAGENS.get(t.situacao, "").format(
        feitos=cob.get("verificados", 0) + cob.get("inconclusivos", 0), total=cob.get("total", 0),
        inc=cob.get("inconclusivos", 0), motivo=t.motivo)
    if texto:
        Mensagem.objects.create(conversa=t.conversa, papel=Papel.IA, texto=texto[:4000],
                                ficha={"tipo": "tarefa_fim", "tarefa": t.pk})
    registrar("consulta_executada", "solicitacao", usuario=t.usuario, setor=t.conversa.setor,
              entidade_id=t.solicitacao_base_id, estado_novo=f"tarefa_{t.situacao}",
              resultado="ok" if t.situacao == S.CONCLUIDA else "erro", duracao_ms=duracao_ms,
              linhas=cob.get("total", 0))


def processar_proxima() -> bool:
    t = reivindicar()
    if t is None:
        return False
    executar(t)
    return True


def conclusao(user, t: Tarefa) -> dict:
    """Conclusão detalhada para a tela. M29: fica gravada na tarefa desde a verificação (decisão do
    Diego) e é mostrada sem consultar o ERP de novo. Tarefas antigas, sem conclusão gravada, são
    verificadas de novo uma vez e passam a guardá-la."""
    if t.usuario_id != user.pk:
        raise inv.SemPermissao("Tarefa de outra pessoa.")
    if t.conclusao:
        return t.conclusao
    lim = {**(t.limites or limites())}
    with prazo.definir(float(lim["prazo_s"])):
        ctl = inv.Controle(lim)
        plano = {**inv.validar_plano(user, t.solicitacao_base, t.proposta), "lote": (t.plano or {}).get("lote")}
        c = inv.investigar(user, t.solicitacao_base, plano, ctl)
    if t.cobertura and t.cobertura.get("total") != c.cobertura["total"]:
        c.limitacoes.insert(0, f"A lista tinha {t.cobertura.get('total')} pessoas na verificação e agora tem "
                               f"{c.cobertura['total']}: a base mudou nesse meio-tempo.")
    dados = inv.para_gravar(c)
    Tarefa.objects.filter(pk=t.pk).update(conclusao=dados)
    return dados
