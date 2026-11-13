"""Regressões da revisão M38: escopo, limites, cancelamento e entrega por evidência."""
import pytest
from iativa.agent import agente, objetivos as coord, tarefas, diagnostico
from iativa.agent.models import Objetivo, SituacaoObjetivo as SO, Tarefa
from iativa.agent.provedores import FalhaModelo
from iativa.manutencao.models import Incidente
from tests.test_objetivos import base as rh_base, _enviar, _listar, _contar, _investigar, _d, PEDIDO, Espiao
from tests.test_diagnostico import base, faturamento, executar

pytestmark = pytest.mark.django_db


def test_todas_filiais_nao_exige_verificar_pessoas(rh_base):
    m,r,p,c=_enviar(rh_base,'Conte os funcionários demitidos em todas as filiais',[
        _contar(rh_base,entrega='calculo')])
    assert r is not None and r.ok and p.chamadas==1
    assert not Incidente.objects.exists() and not Tarefa.objects.exists()
    assert not Objetivo.objects.exclude(situacao=SO.CUMPRIDO).exists()


@pytest.mark.parametrize('texto',['Liste todos os funcionários','Confira o total de todas as filiais',
    'Mostre os títulos de todas as empresas','Some o valor em todas as filiais'])
def test_escopo_nao_e_verificacao(texto):
    assert coord.exigencia(texto)[0]!=coord.VERIFICACAO


def test_diagnostico_tecnico_retoma_pedido_apos_correcao(faturamento,monkeypatch):
    original=diagnostico.Auditor.ler
    def quebrar(self,plano):
        raise RuntimeError('falha sintética')
    monkeypatch.setattr(diagnostico.Auditor,'ler',quebrar)
    m,r,_,_=executar(faturamento)
    o=Objetivo.objects.get();inc=Incidente.objects.get()
    assert not r.completo and o.situacao==SO.MANUTENCAO and o.incidente_id==inc.pk
    from tests.test_objetivos import _publicar
    _publicar(inc,monkeypatch)
    monkeypatch.setattr(diagnostico.Auditor,'ler',original)
    from tests.test_diagnostico import consulta
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:Espiao([consulta(faturamento)]))
    assert coord.continuar_proximo()
    o.refresh_from_db()
    assert o.situacao==SO.CUMPRIDO and o.retomado


@pytest.mark.parametrize('provedor',['chatgpt','claude-code'])
def test_falha_antes_primeira_acao_respeita_limite(rh_base,settings,monkeypatch,provedor):
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _enviar(rh_base,PEDIDO,[_listar(rh_base)])
    o=Objetivo.objects.get()
    class Indisponivel:
        nome=provedor
        chamadas=0
        def decidir(self,entrada):
            self.chamadas+=1
            raise FalhaModelo('indisponível no teste',temporaria=True)
    p=Indisponivel()
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:p)
    for _ in range(2):
        Objetivo.objects.filter(pk=o.pk).update(lease_ate=None)
        assert coord.continuar_proximo()
    o.refresh_from_db()
    assert o.situacao==SO.IMPEDIDO and o.continuacoes==2 and p.chamadas==2
    assert not coord.continuar_proximo()


def test_reivindicacao_expirada_com_orcamento_esgotado_nao_chama_modelo(rh_base,settings):
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _enviar(rh_base,PEDIDO,[_listar(rh_base)])
    o=Objetivo.objects.get()
    Objetivo.objects.filter(pk=o.pk).update(continuacoes=2,lease_ate=None)
    assert not coord.continuar_proximo()
    o.refresh_from_db()
    assert o.situacao==SO.IMPEDIDO and o.execucao==''


def test_nova_mensagem_durante_worker_para_antes_da_acao(rh_base,settings,monkeypatch):
    from iativa.chat.models import Mensagem
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _,_,_,c=_enviar(rh_base,PEDIDO,[_listar(rh_base)])
    class NovaMensagem(Espiao):
        def decidir(self,entrada):
            Mensagem.objects.create(conversa=c,papel='usuario',autor=rh_base['u'],texto='Pare esse pedido. Agora quero outra coisa.')
            return super().decidir(entrada)
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:NovaMensagem([_investigar()]))
    assert coord.continuar_proximo()
    assert not Tarefa.objects.exists()
    assert Mensagem.objects.filter(conversa=c).latest('id').papel=='usuario'
    assert Objetivo.objects.get().situacao==SO.SUPERADO


def test_tarefa_precisa_concluir_para_cumprir_objetivo(rh_base):
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    o=Objetivo.objects.get();t=Tarefa.objects.get()
    assert o.situacao==SO.TAREFA and o.tarefa_id==t.pk and t.situacao=='pendente'
    assert tarefas.processar_proxima()
    o.refresh_from_db();t.refresh_from_db()
    assert t.situacao=='concluida' and t.cobertura['lista_completa']
    assert o.situacao==SO.CUMPRIDO and o.melhor_evidencia==coord.VERIFICACAO


def test_cancelar_tarefa_nao_deixa_objetivo_cumprido(rh_base):
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    t=Tarefa.objects.get();tarefas.cancelar(rh_base['u'],t.pk)
    assert Objetivo.objects.get().situacao==SO.IMPEDIDO
    assert not tarefas.processar_proxima()


def test_falha_tecnica_worker_nao_deixa_concessao_pendurada(rh_base,settings,monkeypatch):
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _enviar(rh_base,PEDIDO,[_listar(rh_base)])
    class Quebrado:
        nome='chatgpt'
        def decidir(self,entrada):
            raise RuntimeError('segredo sintético que não deve aparecer na conversa')
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:Quebrado())
    assert coord.continuar_proximo()
    o=Objetivo.objects.get()
    assert o.situacao==SO.MANUTENCAO and o.incidente_id and o.continuacoes==1 and o.execucao==''
    from iativa.chat.models import Mensagem
    assert not Mensagem.objects.filter(texto__contains='segredo sintético').exists()

def test_migracao_preserva_vinculo_legado_sem_concluir_tarefa_pendente(rh_base):
    import importlib
    from django.apps import apps
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    o=Objetivo.objects.get();t=Tarefa.objects.get()
    Objetivo.objects.filter(pk=o.pk).update(tarefa=None,situacao=SO.CUMPRIDO)
    modulo=importlib.import_module('iativa.agent.migrations.0009_objetivo_tarefa_alter_objetivo_situacao')
    modulo.vincular_tarefas(apps,None)
    o.refresh_from_db()
    assert o.tarefa_id==t.pk and o.situacao==SO.TAREFA


def test_compatibilidade_aceita_so_estado_sem_operacoes_no_banco():
    from django.db import migrations, models
    from iativa.core.management.commands.verificar_migracoes import avaliar_operacao
    from types import SimpleNamespace
    estado=migrations.SeparateDatabaseAndState(database_operations=[],state_operations=[
        migrations.AlterField('objetivo','situacao',models.CharField(max_length=20,choices=[('x','X')]))])
    assert avaliar_operacao(SimpleNamespace(),estado)==''
    destrutiva=migrations.SeparateDatabaseAndState(database_operations=[migrations.RemoveField('objetivo','situacao')])
    assert avaliar_operacao(SimpleNamespace(),destrutiva)
    import importlib
    modulo=importlib.import_module('iativa.agent.migrations.0009_objetivo_tarefa_alter_objetivo_situacao')
    assert all(avaliar_operacao(modulo, op)=='' for op in modulo.Migration.operations)


def test_cancelamento_de_tarefa_por_worker_antigo_e_sincronizado(rh_base):
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    Tarefa.objects.update(situacao='cancelada',cancelar_solicitado=True)
    coord.sincronizar_pendentes()
    assert Objetivo.objects.get().situacao==SO.IMPEDIDO


def test_correcao_de_tarefa_reutiliza_mesma_tarefa_e_conclui_objetivo(rh_base,monkeypatch):
    from iativa.agent import investigacao as inv
    from tests.test_objetivos import _publicar
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    t=Tarefa.objects.get()
    t.limites={**t.limites,'tentativas':1,'chamadas_modelo':0};t.save()
    original=inv.reconstruir_lista
    def quebrar(*a,**kw):
        raise RuntimeError('erro sintético')
    monkeypatch.setattr(inv,'reconstruir_lista',quebrar)
    assert tarefas.processar_proxima()
    o=Objetivo.objects.get();inc=Incidente.objects.get()
    assert o.situacao==SO.MANUTENCAO and o.incidente_id==inc.pk
    _publicar(inc,monkeypatch)
    o.refresh_from_db()
    assert o.situacao==SO.TAREFA and o.retomado and Tarefa.objects.count()==1
    monkeypatch.setattr(inv,'reconstruir_lista',original)
    assert tarefas.processar_proxima()
    o.refresh_from_db()
    assert o.situacao==SO.CUMPRIDO and Tarefa.objects.count()==1


def test_execucao_antiga_nao_sobrescreve_novo_dono(rh_base,settings,monkeypatch):
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _enviar(rh_base,PEDIDO,[_listar(rh_base)])
    class OutroDono(Espiao):
        def decidir(self,entrada):
            Objetivo.objects.update(execucao='outro-dono')
            return super().decidir(entrada)
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:OutroDono([_investigar()]))
    assert coord.continuar_proximo()
    assert Objetivo.objects.get().execucao=='outro-dono' and not Tarefa.objects.exists()

def test_mensagem_depois_de_agendar_tarefa_cancela_antes_de_publicar(rh_base,settings,monkeypatch):
    from iativa.chat.models import Mensagem
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _,_,_,c=_enviar(rh_base,PEDIDO,[_listar(rh_base)])
    original=agente._agir
    def agir(*a,**kw):
        saida=original(*a,**kw)
        Mensagem.objects.create(conversa=c,papel='usuario',autor=rh_base['u'],texto='Pare esse pedido.')
        return saida
    monkeypatch.setattr(agente,'_agir',agir)
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:Espiao([_investigar()]))
    assert coord.continuar_proximo()
    assert Tarefa.objects.get().situacao=='cancelada'
    assert Objetivo.objects.get().situacao==SO.SUPERADO
    assert Mensagem.objects.filter(conversa=c).latest('id').papel=='usuario'


@pytest.mark.parametrize('texto',['Confira o total de funcionários em todas as filiais',
                                 'Verifique quantos funcionários foram demitidos'])
def test_conferencia_de_quantidade_continua_sendo_calculo(texto):
    assert coord.exigencia(texto)[0]!=coord.VERIFICACAO

def test_todas_filiais_nao_desliga_guardiao_de_anuncios_na_continuacao(rh_base):
    _,_,_,c=_enviar(rh_base,'Liste os funcionários demitidos',[_listar(rh_base)])
    m,r,p,_=_enviar(rh_base,'Agora conte em todas as filiais',[
        _d(resposta='Vou contar os funcionários agora.'),_contar(rh_base)],c=c)
    assert p.chamadas==2 and r.ok
    assert 'Vou contar' not in m.texto and not Incidente.objects.exists()

def test_verificacao_concluida_nao_substitui_calculo_pedido(rh_base):
    _,_,_,c=_enviar(rh_base,'Liste os funcionários',[_listar(rh_base)])
    d=_investigar();d['entrega']='calculo'
    _enviar(rh_base,'Quantos foram demitidos?', [d],c=c)
    o=Objetivo.objects.get()
    assert o.exigencia==coord.CALCULO and o.situacao==SO.TAREFA
    assert tarefas.processar_proxima()
    o.refresh_from_db()
    assert o.situacao==SO.CONTINUAR and o.melhor_evidencia==coord.VERIFICACAO

def test_tarefa_que_falha_novamente_apos_correcao_nao_fica_pendurada(rh_base,monkeypatch):
    from iativa.agent import investigacao as inv
    from tests.test_objetivos import _publicar
    _enviar(rh_base,PEDIDO,[_listar(rh_base),_investigar()])
    t=Tarefa.objects.get();t.limites={**t.limites,'tentativas':1,'chamadas_modelo':0};t.save()
    def quebrar(*a,**kw):
        raise RuntimeError('falha sintética')
    monkeypatch.setattr(inv,'reconstruir_lista',quebrar)
    assert tarefas.processar_proxima()
    inc=Incidente.objects.get();_publicar(inc,monkeypatch)
    assert tarefas.processar_proxima()
    assert Objetivo.objects.get().situacao==SO.IMPEDIDO
    assert not tarefas.processar_proxima()


def test_erro_apos_registrar_resultado_encerra_execucao_apesar_do_rollback(rh_base,settings,monkeypatch):
    from iativa.chat.models import Mensagem
    settings.IATIVA_AGENTE_CICLO={'passos':1,'tempo_s':60,'espera_s':0,'continuacoes':2}
    _enviar(rh_base,PEDIDO,[_listar(rh_base)])
    monkeypatch.setattr(agente,'obter_provedor',lambda usuario=None:Espiao([_investigar()]))
    original=Mensagem.objects.create
    chamadas=[]
    def gravar(*a,**kw):
        chamadas.append(1)
        if len(chamadas)==1:
            raise RuntimeError('erro sintético ao gravar mensagem')
        return original(*a,**kw)
    monkeypatch.setattr(Mensagem.objects,'create',gravar)
    assert coord.continuar_proximo()
    o=Objetivo.objects.get()
    assert o.situacao==SO.MANUTENCAO and o.incidente_id and o.execucao==''
    assert not Tarefa.objects.exists()
