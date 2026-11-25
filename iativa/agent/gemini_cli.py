"""Gemini CLI para atendimento, com credencial isolada por usuário."""
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings

from . import conexoes, prazo
from .decisao import DecisaoInvalida, esquema, validar
from .provedores import FalhaDecisao, FalhaModelo, INSTRUCOES_AGENTE, LLMProvider, _codigos, _contexto_agente, _exigir_envio_liberado


def arquivo(user):
    return conexoes.arquivo(user, 'atendimento').with_name(f'gemini-atendimento-{user.pk}.json')


def configuracao(user):
    try:
        valor = json.loads(arquivo(user).read_text(encoding='utf-8'))
        return valor if isinstance(valor, dict) else {}
    except (OSError, ValueError):
        return {}


def cartao(user):
    config = configuracao(user)
    return {'ativo': bool(config.get('chave')), 'modelo': config.get('modelo', '')}


def _executar(chave, modelo, prompt):
    _exigir_envio_liberado()
    restante = prazo.restante()
    if restante is not None and restante <= 0:
        raise prazo.PrazoEsgotado()
    timeout = float(settings.IATIVA_AGENTE.get('timeout_s', 90))
    if restante is not None:
        timeout = min(timeout, restante)
    with TemporaryDirectory(prefix='iativa-gemini-') as pasta:
        root = Path(pasta)
        workspace = root / 'workspace'
        workspace.mkdir()
        politica = root / 'deny.toml'
        politica.write_text('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n', encoding='utf-8')
        config = root / 'settings.json'
        config.write_text(json.dumps({
            'security': {'auth': {'selectedType': 'gemini-api-key'}, 'disableYoloMode': True},
            'general': {'enableAutoUpdate': False, 'maxAttempts': 1},
            'telemetry': {'enabled': False, 'logPrompts': False},
            'tools': {'core': ['__iativa_sem_ferramentas__']},
        }), encoding='utf-8')
        ambiente = {
            'PATH': os.defpath + os.pathsep + '/usr/local/bin',
            'HOME': pasta, 'GEMINI_CLI_HOME': pasta, 'XDG_CACHE_HOME': pasta,
            'TMPDIR': pasta, 'CI': 'true', 'GEMINI_API_KEY': chave,
            'GEMINI_CLI_SYSTEM_SETTINGS_PATH': str(config),
        }
        cmd = ['gemini', '--admin-policy', str(politica), '--extensions', 'none', '--output-format', 'json',
               '--approval-mode', 'default', '--prompt', 'Responda ao pedido recebido pela entrada padrão.']
        if modelo:
            cmd.extend(['--model', modelo])
        try:
            processo = subprocess.run(cmd, cwd=workspace, env=ambiente, input=prompt, encoding='utf-8',
                                       capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise FalhaModelo('O Gemini CLI excedeu o prazo.', temporaria=True) from None
        except OSError:
            raise FalhaModelo('O Gemini CLI está indisponível no servidor.', temporaria=True) from None
        if processo.returncode:
            raise FalhaModelo('O Gemini CLI recusou a consulta. Confira a chave, o modelo e a cota no Google AI Studio.',
                              temporaria=True)
        try:
            dados = json.loads(processo.stdout)
            resposta = dados['response']
            if dados.get('stats', {}).get('tools', {}).get('totalCalls', 0) or not isinstance(resposta, str):
                raise ValueError
            return resposta
        except (TypeError, KeyError, AttributeError, ValueError):
            raise FalhaModelo('O Gemini CLI devolveu uma resposta inválida.') from None


def conectar(user, chave, modelo):
    path = arquivo(user)
    chave = chave.strip() or configuracao(user).get('chave', '')
    modelo = modelo.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', chave):
        raise FalhaModelo('Informe uma chave válida do Google AI Studio.')
    if modelo and not re.fullmatch(r'gemini-[a-z0-9.-]{1,72}', modelo):
        raise FalhaModelo('Informe o ID de um modelo Gemini ou deixe o modelo padrão.')
    _executar(chave, modelo, 'Responda somente OK.')
    conexoes.gravar_json(path, {'chave': chave, 'modelo': modelo})


def desconectar(user):
    arquivo(user).unlink(missing_ok=True)
    if conexoes.preferido(user) == 'gemini':
        conexoes.arquivo(user, 'atendimento').unlink(missing_ok=True)


class GeminiCLIProvider(LLMProvider):
    nome = 'gemini-cli'

    def __init__(self, user):
        self.config = configuracao(user)
        self.modelo = self.config.get('modelo', '')

    def _gerar(self, prompt, schema, _validar, _objetivos, _parametros):
        if not self.config.get('chave'):
            raise FalhaModelo('Reconecte o Gemini em Conexões de IA.')
        prompt += '\nResponda apenas com um objeto JSON neste esquema, sem Markdown:\n' + json.dumps(schema, ensure_ascii=False)
        texto = _executar(self.config['chave'], self.modelo, prompt).strip()
        if texto.startswith('```json') and texto.endswith('```'):
            texto = texto[7:-3].strip()
        try:
            valor = json.loads(texto)
        except ValueError:
            raise FalhaDecisao('formato') from None
        if not isinstance(valor, dict):
            raise FalhaDecisao('formato')
        return valor

    def decidir(self, entrada):
        objetivos, parametros = _codigos(entrada)
        bruto = self._gerar(INSTRUCOES_AGENTE + '\n' + _contexto_agente(entrada),
                           esquema(objetivos, parametros, entrada.acoes_permitidas), validar, objetivos, parametros)
        try:
            validar(bruto, objetivos, parametros)
        except DecisaoInvalida as exc:
            raise FalhaDecisao(exc) from None
        return bruto
