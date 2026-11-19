"""Planejamento sem dados do ERP: o modelo classifica; o backend autoriza e verifica."""
from contextvars import ContextVar
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from iativa.accounts.authz import Permissao, PermissaoNegada, setores_autorizados
from iativa.accounts.models import Department
from iativa.memory.validadores import contem_cpf_cnpj
from .decisao import EntradaAgente
from .models import ContextoPersistente, NotaAgente
from .provedores import FalhaModelo


provedor_do_pedido = ContextVar("provedor_do_pedido", default=None)


class FonteInconclusiva(ValueError):
    """A descoberta pode continuar, mas esta fonte ainda não pode ser executada."""


def autorizar_fonte(user, provedor, alias, *, conversa=None, transversais=None):
    """Inferência por metadados do dicionário, sem linhas nem mapeamento manual de tabelas."""
    import hashlib
    import json
    from .models import FonteSetorial, ConsultaModelo
    from iativa.metadata.catalogo import abrir_catalogo
    with abrir_catalogo() as cat:
        t = cat.tabelas([alias], com_campos=True).get(alias)
    if t is None:
        raise FonteInconclusiva(f'A tabela {alias} não foi confirmada no catálogo físico local.')
    campos = [c for c in t.campos if c.consultavel]
    relevantes = [c for c in campos if c.nome in t.chave or c.tipo == "D" or c.personalizado]
    selecionados = list(dict.fromkeys([*campos[:25], *relevantes[:50], *campos[:40]]))
    descricao = {'tabela': alias, 'descricao': t.nome, 'chave_local': list(t.chave),
                 'campos': [{'nome': c.nome, 'titulo': c.titulo, 'descricao': c.descricao}
                            for c in selecionados]}
    usos = []
    for cm in ConsultaModelo.objects.filter(situacao='aprovada').order_by('pk').iterator():
        if cm.spec.get("leitura_transversal"):
            continue
        if alias == cm.tabela or alias in {j['tabela'] for j in cm.spec.get('juncoes', [])}:
            usos.append({'nome': cm.nome, 'descricao': cm.descricao, 'fonte_principal': cm.tabela,
                         'setores_da_receita': sorted(cm.setores.values_list('codigo', flat=True)),
                         'colunas': [c['rotulo'] for c in cm.spec.get('colunas', [])][:20]})
            if len(usos) == 12:
                break
    descricao['usos_aprovados'] = usos
    setores = list(Department.objects.filter(ativo=True).order_by('codigo').values('codigo', 'nome'))
    assinatura = hashlib.sha256(json.dumps([descricao, setores], sort_keys=True, default=str).encode()).hexdigest()
    fonte = FonteSetorial.objects.filter(tabela=alias, assinatura=assinatura).first()
    if fonte is None or fonte.confianca == 0:
        if provedor is None:
            from .provedores import obter_provedor
            provedor = provedor_do_pedido.get() or obter_provedor(usuario=user)
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
            from iativa.accounts.authz import leitura_de_todos_os_setores
            if (transversais is not None and conversa is not None and conversa.tipo == "individual"
                    and conversa.usuario_id == user.pk and permitidos and leitura_de_todos_os_setores(user, [alias])):
                transversais.add(alias)
                return set(permitidos)
            raise FonteInconclusiva(f'O dicionário confirma {alias}, mas o setor da fonte ainda é inconclusivo.')
        fonte, _ = FonteSetorial.objects.update_or_create(tabela=alias, defaults={
            'assinatura': assinatura, 'setores': sorted(set(a['setores'])), 'confianca': a['confianca']})
    permitidos = set(Department.objects.filter(pk__in=setores_autorizados(user, Permissao.CONSULTAR)).values_list('codigo', flat=True))
    if not permitidos & set(fonte.setores):
        raise PermissaoNegada('Essa fonte pertence a outro setor. Não executei a consulta; solicite acesso à Diretoria ou TI.')

    return permitidos & set(fonte.setores)


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
    sem_setor = analise['natureza'] == 'dados' and (not alvos or not set(alvos) <= {s['codigo'] for s in setores})
    incerta = analise['natureza'] == 'ambigua' or confianca < .85
    if (incerta and analise['natureza'] == 'dados' and confianca >= .5 and alvos and set(alvos) <= set(permitidos)
            and _continua_assunto(conversa, alvos)):
        # M84: "tem observação do item X?" logo após listar solicitações de compra: o assunto é o da conversa.
        # Setor válido e autorizado + continuação do mesmo domínio: não pede esclarecimento.
        incerta = False
    if (sem_setor or (incerta and analise['natureza'] != 'fora')) and _explorar_dicionario(user, texto):
        # M82: assunto que nenhum setor cadastrado cobre (ex.: compras), mas o dicionário do Protheus tem tabela
        # para ele: a IA procura e aprende sozinha (aprender_tabela) em vez de pedir para a pessoa esclarecer.
        analise = {**analise, 'natureza': 'dados', 'confianca': max(confianca, .85),
                   'setores': sorted(set(permitidos)), 'explorar_dicionario': True,
                   'orientacao': ('Nenhuma consulta conhecida cobre este assunto, mas o dicionário do Protheus tem '
                                  'tabelas para ele. Use aprender_tabela com o assunto do pedido (ex.: "solicitações '
                                  'de compra") antes de perguntar qualquer coisa à pessoa.')}
        alvos = analise['setores']
        sem_setor = incerta = False
    if sem_setor:
        raise ValidationError('De qual setor são os dados que você deseja consultar?')
    if incerta:
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
    from . import contagem as admissoes, entregas, comparacao
    return comparacao.planejar(texto, entregas.planejar(texto, admissoes.planejar(texto, {**analise, 'setores': sorted(set(alvos)), 'contextos': entrada.planejamento['contextos_recuperados']}, user)))


def _continua_assunto(conversa, alvos) -> bool:
    """M84: a última consulta desta conversa é do mesmo setor que o pedido foi classificado."""
    s = conversa.solicitacoes.exclude(objetivo="").order_by("-id").first() if conversa is not None else None
    if s is None:
        return False
    codigo = getattr(s.setor, 'codigo', '')
    if codigo in alvos:
        return True
    from .contratos import OBJETIVOS
    obj = OBJETIVOS[s.objetivo] if s.objetivo in OBJETIVOS else None
    if obj is not None and obj.modelo_id:
        from .models import FonteSetorial
        fonte = FonteSetorial.objects.filter(tabela=obj.tabelas[0]).first()
        return fonte is not None and bool(set(fonte.setores) & set(alvos))
    return False


def _explorar_dicionario(user, texto) -> bool:
    """M82: o pedido fala de dados que o dicionário do ERP tem (tabela encontrada pelos termos do pedido) e a
    pessoa pode fazer a IA aprender consultas novas. Só estrutura do dicionário; nenhuma linha do ERP."""
    from .aprendizado import candidatos, pode_aprender_sozinho
    if not pode_aprender_sozinho(user) or len(texto.split()) < 3:
        return False
    import logging
    for tentativa in (1, 2):  # a conexão ao dicionário do ERP às vezes cai na 1ª tentativa
        try:
            return bool(candidatos(user, texto, limite=1, automatico=True))
        except Exception as exc:
            logging.getLogger(__name__).warning("busca no dicionário falhou (tentativa %s): %s", tentativa,
                                                type(exc).__name__)
    return False


def conferir(analise, decisao, saida, texto=""):
    """Uma tabela não comprova um cálculo; descrições não comprovam métricas."""
    from .objetivos import evidencia, cumpre
    ev = evidencia(decisao, saida, texto)
    res = saida.resultado
    faltantes = []
    for item in analise.get('entregas', []):
        if item.get('evento_mensal'):
            from .comparacao import comprova_evento_mensal
            if not comprova_evento_mensal(item, saida):
                faltantes.append(item['descricao'])
            continue
        if item.get('campos_pedidos'):
            from .entregas import faltam
            if not getattr(res,'ok',False) or faltam(getattr(res,'colunas',[]),item['campos_pedidos']):
                faltantes.append(item['descricao'])
                continue
        if item.get('detalhe_contagem'):
            from .contagem import tem_detalhes
            if not tem_detalhes(res):
                faltantes.append(item['descricao'])
                continue
        historico_com_calculo = (item['tipo'] == 'historico' and ev == 'calculo'
                                and (saida.ficha or {}).get('tipo') == 'historico_cargo')
        if not cumpre(item['tipo'], ev) and not historico_com_calculo:
            faltantes.append(item['descricao'])
        elif item.get('metrica') and getattr(res, 'medida_calculada', '') != item['metrica'] and not (
                # M108: contagem condicional por prazo entre duas datas também é contagem.
                item['metrica'] == 'contagem' and getattr(res, 'medida_calculada', '') == 'intervalo_entre_datas'):
            # Agregações no banco já comprovam soma/média, mas não dias ou projeção.
            mapa = {'soma': 'SUM', 'media': 'AVG', 'contagem': 'COUNT'}
            medidas = getattr(getattr(res, 'totais', None), 'medidas', [])
            if item['metrica'] not in mapa or not any(m.funcao == mapa[item['metrica']] for m in medidas):
                faltantes.append(item['descricao'])
    return faltantes


def divergencia(item, decisao, ano_civil=""):
    """M74 (etapa 2): a entrega pede um período ou uma filial; o passo usou outro? Devolve o motivo ou ''.
    Vale para qualquer consulta: "admitidos em agosto" não é cumprida por uma consulta de julho, nem
    "faturamento de Cuiabá" por todas as filiais. A comparação tem os próprios períodos (não entra aqui)."""
    if item.get('comparacao'):
        return ''
    from datetime import date

    from django.utils import timezone

    from iativa.accounts.filiais import NOMES, TODAS
    from .periodo import _norm, periodo_da_mensagem
    params = dict(decisao.parametros)
    desc = item.get('descricao', '')
    motivos = []
    pedido = periodo_da_mensagem(desc, timezone.localdate())
    if ano_civil and re.search(rf'\bem {ano_civil}\b', _norm(desc)):
        pedido = f'01/01/{ano_civil}..31/12/{ano_civil}'
    usado = params.get('periodo-analise', '')
    if pedido and usado and '..' in usado:
        def datas(p):
            a, b = p.split('..')
            return [date(int(x[6:10]), int(x[3:5]), int(x[:2])) for x in (a, b)]
        try:
            (pi, pf), (ui, uf) = datas(pedido), datas(usado)
        except (ValueError, IndexError):
            pi = None
        # Mesmo início; o fim pode ir até o fim do mês pedido (ex.: "este mês" até hoje ou até o dia 30).
        if pi is not None and (ui != pi or uf < pf or (uf.year, uf.month) != (pf.year, pf.month)):
            motivos.append(f'período pedido {pedido.replace("..", " a ")}, usado {usado.replace("..", " a ")}')
    t = _norm(desc)
    filiais = [c for c, n in NOMES.items() if re.search(rf'\b{re.escape(_norm(n))}\b', t) or c in t]
    usada = params.get('filial-analise', '')
    if filiais and usada and not set(re.split(r'[,; ]+', usada)) <= set(filiais) and usada != ','.join(filiais):
        motivos.append(f'filial pedida {", ".join(filiais)}, usada {"todas" if usada == TODAS else usada}')
    return '; '.join(motivos)


def registrar_entregas(acompanhamento, decisao, saida):
    from .objetivos import ano_admissoes_e_desligamentos

    contrato = acompanhamento.contrato
    ano_civil = ano_admissoes_e_desligamentos(acompanhamento.texto) if len(contrato) == 1 else ""
    mensais = tuple(i for i, item in enumerate(contrato) if item.get('evento_mensal'))
    indices = mensais or decisao.atende or ((0,) if len(contrato) == 1 else ())
    for indice in indices:
        if indice < len(contrato) and indice not in acompanhamento.cumpridas:
            motivo = divergencia(contrato[indice], decisao, ano_civil)
            if motivo:
                # Fica pendente e o modelo recebe o motivo para refazer com o critério certo.
                acompanhamento.divergencias = {**getattr(acompanhamento, 'divergencias', {}),
                                               contrato[indice]['descricao']: motivo}
                continue
        if (indice < len(contrato) and indice not in acompanhamento.cumpridas
                and not conferir({'entregas': [contrato[indice]]}, decisao, saida, acompanhamento.texto)):
            acompanhamento.cumpridas.append(indice)
            if saida.resultado is not None and not getattr(saida.resultado, 'consolidado', False):
                try:  # M68: título do bloco na resposta final com várias entregas
                    saida.resultado.titulo_entrega = contrato[indice]['descricao']
                except AttributeError:
                    pass


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
