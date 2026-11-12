"""Atendimento com conta ChatGPT via Codex: decisões estruturadas, sem ferramentas locais."""
import json
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from . import codex_login, decisao, esquema, prazo
from .provedores import FalhaModelo, LLMProvider, INSTRUCOES, INSTRUCOES_AGENTE, _codigos, _catalogo, _renderizar, _contexto_agente


def schema_estrito(schema):
    s = dict(schema)
    if s.get('type') == 'object':
        requeridos = set(s.get('required', []))
        props = {}
        for key, value in s.get('properties', {}).items():
            p = schema_estrito(value)
            if key not in requeridos:
                p = {'anyOf': [p, {'type': 'null'}]}
            props[key] = p
        s.update(properties=props, required=list(props), additionalProperties=False)
    elif s.get('type') == 'array':
        s['items'] = schema_estrito(s['items'])
    return s


class CodexProvider(LLMProvider):
    nome = 'chatgpt'

    def __init__(self, usuario):
        if not settings.IATIVA_AGENTE.get('envio_liberado'):
            raise ImproperlyConfigured('Envio ao modelo não liberado pela configuração de privacidade.')
        if not codex_login.ativa(usuario):
            raise ImproperlyConfigured('Conecte a conta ChatGPT selecionada em Conexões IA.')
        self.usuario = usuario

    def _gerar(self, prompt, schema, validar, objetivos, parametros):
        if len(prompt.encode()) > 200_000:
            raise FalhaModelo('Contexto do ChatGPT excedeu o limite permitido.')
        tempo = prazo.limitar(int(settings.IATIVA_AGENTE.get('codex_timeout_s', 120)))
        fim = time.monotonic() + tempo
        pref = codex_login.preferencias(self.usuario, 'atendimento')
        with codex_login.rpc(self.usuario) as s:
            if not s.autenticado():
                codex_login.marcar(self.usuario, 'atendimento', False)
                raise FalhaModelo('Reconecte a conta ChatGPT em Conexões IA.')
            thread = s.request('thread/start', {'ephemeral': True, 'cwd': s.cwd, 'approvalPolicy': 'never',
                'sandbox': 'read-only', 'environments': [], 'dynamicTools': [], 'selectedCapabilityRoots': [],
                'modelProvider': 'openai', **({'model': pref['modelo']} if pref['modelo'] else {})},
                timeout=max(0.01, fim - time.monotonic()))
            s.request('turn/start', {'threadId': thread['thread']['id'],
                'input': [{'type': 'text', 'text': prompt}], 'approvalPolicy': 'never',
                'sandboxPolicy': {'type': 'readOnly', 'access': {'type': 'restricted',
                    'includePlatformDefaults': False, 'readableRoots': [s.cwd]}},
                'outputSchema': schema_estrito(schema),
                **({'effort': pref['esforco']} if pref['esforco'] else {})},
                timeout=max(0.01, fim - time.monotonic()))
            texto = ''
            for _ in range(10_000):
                if time.monotonic() >= fim:
                    raise FalhaModelo('ChatGPT excedeu o tempo limite.', temporaria=True)
                ev = s.evento(fim - time.monotonic())
                params = ev.get('params', {})
                if ev.get('method') in ('item/started', 'item/completed'):
                    item = params.get('item', {})
                    if item.get('type') in ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall'):
                        raise FalhaModelo('O atendimento tentou usar uma ferramenta não autorizada.')
                    if ev['method'] == 'item/completed' and item.get('type') == 'agentMessage':
                        texto = item.get('text', '')
                if ev.get('method') == 'turn/completed':
                    if params.get('turn', {}).get('status') != 'completed':
                        raise FalhaModelo('O ChatGPT não concluiu a resposta. Confira acesso, modelo ou limite da conta.')
                    try:
                        raw = json.loads(texto)
                        validar(raw, objetivos, parametros)
                    except (ValueError, TypeError, esquema.SaidaInvalida, decisao.DecisaoInvalida):
                        raise FalhaModelo('Resposta do ChatGPT fora do contrato permitido.') from None
                    return raw
            raise FalhaModelo('ChatGPT excedeu o limite de eventos.')

    def interpretar(self, entrada):
        objs, pars = _codigos(entrada)
        return self._gerar(INSTRUCOES + '\n' + _catalogo(entrada) + '\n' + _renderizar(entrada),
                           esquema.esquema_json(objs, pars), esquema.validar, objs, pars)

    def decidir(self, entrada):
        objs, pars = _codigos(entrada)
        return self._gerar(INSTRUCOES_AGENTE + '\n' + _contexto_agente(entrada),
                           decisao.esquema(objs, pars), decisao.validar, objs, pars)
