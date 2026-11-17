"""Planejamento sem dados do ERP: o modelo classifica; o backend autoriza e verifica."""
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, setores_autorizados
from iativa.accounts.models import Department
from iativa.memory.validadores import contem_cpf_cnpj
from .decisao import EntradaAgente
from .models import ContextoPersistente, NotaAgente
from .provedores import FalhaModelo


def autorizar_fonte(user, provedor, alias):
    """Inferência por metadados do dicionário, sem linhas nem mapeamento manual de tabelas."""
    import hashlib
    import json
    from .models import FonteSetorial, ConsultaModelo
    from iativa.metadata.catalogo import abrir_catalogo
    with abrir_catalogo() as cat:
        t = cat.tabelas([alias], com_campos=True).get(alias)
    if t is None:
        raise PermissaoNegada('Não consegui confirmar o domínio da fonte de dados.')
    descricao = {'tabela': alias, 'descricao': t.nome,
                 'campos': [{'nome': c.nome, 'titulo': c.titulo, 'descricao': c.descricao}
                            for c in t.campos[:40]]}
    usos = []
    for cm in ConsultaModelo.objects.filter(situacao='aprovada').order_by('pk').iterator():
        if alias == cm.tabela or alias in {j['tabela'] for j in cm.spec.get('juncoes', [])}:
            usos.append({'nome': cm.nome, 'descricao': cm.descricao, 'fonte_principal': cm.tabela,
                         'colunas': [c['rotulo'] for c in cm.spec.get('colunas', [])][:20]})
            if len(usos) == 12:
                break
    descricao['usos_aprovados'] = usos
    setores = list(Department.objects.filter(ativo=True).order_by('codigo').values('codigo', 'nome'))
    assinatura = hashlib.sha256(json.dumps([descricao, setores], sort_keys=True, default=str).encode()).hexdigest()
    fonte = FonteSetorial.objects.filter(tabela=alias, assinatura=assinatura).first()
    if fonte is None:
        if provedor is None:
            from .provedores import obter_provedor
            provedor = obter_provedor(usuario=user)
        permitidos = list(Department.objects.filter(pk__in=setores_autorizados(user, Permissao.CONSULTAR)).values_list('codigo', flat=True))
        e = EntradaAgente('Classifique o assunto da fonte descrita no dicionário, sem executar consultas.',
            timezone.localdate().isoformat(), [], [], None,
            planejamento={'modo': 'classificar_fonte', 'fonte': descricao,
                          'setores_disponiveis': setores, 'autorizados': permitidos})
        a = provedor.planejar(e)
        if (not isinstance(a, dict) or a.get('natureza') != 'dados' or type(a.get('confianca')) not in (int, float)
                or not .85 <= a['confianca'] <= 1 or not isinstance(a.get('setores'), list) or not a['setores']
                or any(not isinstance(s, str) for s in a['setores'])
                or not set(a['setores']) <= {s['codigo'] for s in setores}):
            raise PermissaoNegada('A fonte ainda não tem um domínio confirmado. Não executei a consulta.')
        fonte, _ = FonteSetorial.objects.update_or_create(tabela=alias, defaults={
            'assinatura': assinatura, 'setores': sorted(set(a['setores'])), 'confianca': a['confianca']})
    permitidos = set(Department.objects.filter(pk__in=setores_autorizados(user, Permissao.CONSULTAR)).values_list('codigo', flat=True))
    if not permitidos & set(fonte.setores):
        raise PermissaoNegada('Essa fonte pertence a outro setor. Não executei a consulta; solicite acesso à Diretoria ou TI.')
    dominios = list(Department.objects.filter(codigo__in=permitidos & set(fonte.setores), ativo=True))
    for cm in ConsultaModelo.objects.filter(tabela=alias, situacao='aprovada'):
        cm.setores.add(*dominios)


def contextos(user, conversa, texto):
    from .memoria import _radicais
    permitidos = set(Department.objects.filter(pk__in=setores_autorizados(user, Permissao.CONSULTAR)).values_list('codigo', flat=True))
    registros = ContextoPersistente.objects.filter(usuario=user).select_related('conversa').order_by('-atualizado_em')[:40]
    alvo = _radicais(texto)
    registros = [r for r in registros if set(r.setores) <= permitidos and not r.conversa.encerrada_em
                 and (r.conversa.usuario_id == user.pk or r.conversa.setor_id in setores_autorizados(user, Permissao.CONSULTAR))]
    registros.sort(key=lambda r: (r.conversa_id == conversa.pk, len(alvo & _radicais(r.resumo)), r.atualizado_em), reverse=True)
    return [{'conversa': r.conversa_id, 'origem': r.mensagem_id, 'resumo': r.resumo, 'setores': r.setores}
            for r in registros[:5]]


def preparar(user, conversa, texto, provedor, historico):
    setores = list(Department.objects.filter(ativo=True).values('codigo', 'nome'))
    permitidos = list(Department.objects.filter(pk__in=setores_autorizados(user, Permissao.CONSULTAR), ativo=True)
                      .values_list('codigo', flat=True))
    entrada = EntradaAgente(texto, timezone.localdate().isoformat(), historico, [], None,
        planejamento={'modo': 'classificar', 'setores_disponiveis': setores, 'autorizados': permitidos,
                      'contextos_recuperados': contextos(user, conversa, texto)})
    try:
        analise = (provedor.planejar(entrada) if hasattr(provedor, 'planejar')
                   else provedor.decidir(entrada).get('analise'))
    except (AttributeError, TypeError, ValueError, IndexError) as exc:
        raise FalhaModelo('Não foi possível classificar o pedido com segurança.') from exc
    if not isinstance(analise, dict) or analise.get('natureza') not in ('dados', 'geral', 'fora', 'ambigua'):
        raise FalhaModelo('Classificação ausente ou inválida.')
    confianca, alvos = analise.get('confianca'), analise.get('setores')
    if type(confianca) not in (int, float) or not 0 <= confianca <= 1 or not isinstance(alvos, list) or len(alvos) > 12:
        raise FalhaModelo('Classificação sem confiança ou setores válidos.')
    if any(not isinstance(s, str) for s in alvos):
        raise FalhaModelo('Setores inválidos.')
    if analise['natureza'] == 'dados' and (not alvos or not set(alvos) <= {s['codigo'] for s in setores}):
        raise ValidationError('De qual setor são os dados que você deseja consultar?')
    if analise['natureza'] == 'ambigua' or confianca < .85:
        raise ValidationError('Preciso esclarecer o assunto antes de consultar: qual informação você deseja obter?')
    if not set(alvos) <= set(permitidos):
        raise PermissaoNegada('Seu usuário não tem autorização para os setores envolvidos neste pedido. Solicite acesso à Diretoria ou TI.')
    entregas = analise.get('entregas', [])
    tipos = {'resposta', 'consulta', 'calculo', 'verificacao', 'investigacao', 'historico'}
    metricas = {'', 'comparacao', 'contagem', 'dias_uteis', 'dias_corridos', 'meses', 'soma', 'media', 'diferenca', 'percentual', 'projecao'}
    if not isinstance(entregas, list) or len(entregas) > 6 or any(
        not isinstance(e, dict) or e.get('tipo') not in tipos or e.get('metrica', '') not in metricas
        or not isinstance(e.get('descricao'), str) or len(e['descricao']) > 200 for e in entregas):
        raise FalhaModelo('Entregas inválidas.')
    from . import admissoes, entregas, comparacao
    return comparacao.planejar(texto, entregas.planejar(texto, admissoes.planejar(texto, {**analise, 'setores': sorted(set(alvos)), 'contextos': entrada.planejamento['contextos_recuperados']})))


def conferir(analise, decisao, saida):
    """Uma tabela não comprova um cálculo; descrições não comprovam métricas."""
    from .objetivos import evidencia, cumpre
    ev = evidencia(decisao, saida)
    res = saida.resultado
    faltantes = []
    for item in analise.get('entregas', []):
        if item.get('campos_pedidos'):
            from .entregas import faltam
            if not getattr(res,'ok',False) or faltam(getattr(res,'colunas',[]),item['campos_pedidos']):
                faltantes.append(item['descricao'])
                continue
        if item.get('detalhe_admissoes'):
            from .admissoes import tem_detalhes
            if not tem_detalhes(res):
                faltantes.append(item['descricao'])
                continue
        historico_com_calculo = (item['tipo'] == 'historico' and ev == 'calculo'
                                and (saida.ficha or {}).get('tipo') == 'historico_cargo')
        if not cumpre(item['tipo'], ev) and not historico_com_calculo:
            faltantes.append(item['descricao'])
        elif item.get('metrica') and getattr(res, 'medida_calculada', '') != item['metrica']:
            # Agregações no banco já comprovam soma/média, mas não dias ou projeção.
            mapa = {'soma': 'SUM', 'media': 'AVG', 'contagem': 'COUNT'}
            medidas = getattr(getattr(res, 'totais', None), 'medidas', [])
            if item['metrica'] not in mapa or not any(m.funcao == mapa[item['metrica']] for m in medidas):
                faltantes.append(item['descricao'])
    return faltantes


def registrar_entregas(acompanhamento, decisao, saida):
    contrato = acompanhamento.contrato
    indices = decisao.atende or ((0,) if len(contrato) == 1 else ())
    for indice in indices:
        if (indice < len(contrato) and indice not in acompanhamento.cumpridas
                and not conferir({'entregas': [contrato[indice]]}, decisao, saida)):
            acompanhamento.cumpridas.append(indice)


def guardar(user, conversa, mensagem_id, texto, analise, saida):
    if not mensagem_id or (saida.ficha or {}).get('tipo') in ('fora_escopo', 'bloqueada', 'falha_modelo'):
        return
    resumo = analise.get('resumo', '')
    if isinstance(resumo, str) and resumo.strip() and len(resumo) <= 1000 and not contem_cpf_cnpj(resumo) and not re.search(r'R\$|\b\d{6,}\b', resumo):
        ContextoPersistente.objects.update_or_create(usuario=user, conversa=conversa,
            defaults={'mensagem_id': mensagem_id, 'resumo': resumo, 'setores': analise['setores']})
    from . import memoria
    candidatos = analise.get('memorias', [])
    if not isinstance(candidatos, list):
        return
    for candidato in candidatos[:4]:
        if not isinstance(candidato, dict):
            continue
        nota, trecho, tipo = candidato.get('texto'), candidato.get('trecho'), candidato.get('tipo')
        if (not isinstance(nota, str) or not isinstance(trecho, str) or not trecho.strip() or trecho not in texto
                or len(nota) > 300 or len(trecho) > 300 or tipo not in ('preferencia', 'termo', 'correcao')
                or contem_cpf_cnpj(nota) or contem_cpf_cnpj(trecho) or re.search(r'R\$\s*\d|\b\d{6,}\b', nota + ' ' + trecho)):
            continue
        # Somente uma preferência explicitamente durável pode ser ativada automaticamente.
        explicita = bool(re.search(r'\b(sempre|prefiro|preferência|preferencia|daqui para frente|lembre|guarde)\b', trecho, re.I))
        if not explicita or NotaAgente.objects.filter(usuario=user, texto=nota, ativo=True).exists():
            continue
        try:
            apresentacao = tipo == 'preferencia' and bool(re.search(r'curt|resum|tabela|formato|respost|casas decimais|orden|valores? em reais|moeda|R\$', trecho, re.I))
            if apresentacao:
                n = memoria.lembrar(user, nota, alcance='usuario', tipo=tipo)
            else:
                n = NotaAgente.objects.create(texto=nota, tipo=tipo, escopo='usuario', usuario=user,
                    criado_por=user, situacao='proposta', excecao_de=memoria.regra_corporativa(user, nota))
        except (ValidationError, PermissaoNegada):
            continue
        n.origem_mensagem_id = mensagem_id
        n.setores_origem = analise['setores']
        n.setor_origem = Department.objects.filter(codigo__in=analise['setores']).order_by('pk').first()
        n.confianca, n.evidencia, n.automatica = analise['confianca'], trecho, True
        if not apresentacao:
            n.situacao, n.aprovado_por, n.aprovado_em = 'proposta', None, None
        n.save()
        saida.ficha.setdefault('memorias_capturadas', []).append({'id': n.pk, 'situacao': n.situacao})
