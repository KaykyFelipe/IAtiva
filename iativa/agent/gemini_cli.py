"""Gemini CLI para atendimento, com credencial isolada por usuário."""
from contextlib import contextmanager
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock

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


ESFORCOS = (('MINIMAL', 'Mínimo'), ('LOW', 'Baixo'), ('MEDIUM', 'Médio'), ('HIGH', 'Alto'))
NIVEIS = {
    'gemini-3.1-pro-preview': ('LOW', 'MEDIUM', 'HIGH'),
    'gemini-3-pro-preview': ('LOW', 'HIGH'),
    'gemini-3.8-flash': ('LOW', 'MEDIUM', 'HIGH'),
    'gemini-3-flash-preview': ('MINIMAL', 'LOW', 'MEDIUM', 'HIGH'),
    'gemini-3.5-flash-lite': ('MINIMAL', 'LOW', 'MEDIUM', 'HIGH'),
}
ORCAMENTOS = {'LOW': 1024, 'MEDIUM': 4096, 'HIGH': 8192}
CATALOGO = Path('/tmp/iativa-gemini-modelos.json')


def niveis(modelo):
    if modelo in ('gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite'):
        return tuple(ORCAMENTOS)
    return NIVEIS.get(modelo.removesuffix('-customtools'), ())


def cartao(user):
    config = configuracao(user)
    modelo = config.get('modelo', '')
    conectado = ativo(user)
    modelos = {}
    if conectado:
        try:
            for item in json.loads(CATALOGO.read_text(encoding='utf-8')):
                ident = item.get('id', '')
                if ident.startswith('gemini-'):
                    modelos[ident] = item.get('displayName') or ident
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if modelo and modelo != 'auto':
            modelos.setdefault(modelo, modelo)
    return {'ativo': conectado, 'modelo': modelo, 'esforco': config.get('esforco', ''),
            'esforcos': ESFORCOS,
            'modelos': [{'id': ident, 'nome': nome, 'niveis': ' '.join(niveis(ident))}
                        for ident, nome in modelos.items()]}


ARQUIVOS_OAUTH = ('oauth_creds.json', 'google_accounts.json')
_TRAVA_LOCAL = RLock()


@contextmanager
def trava(user):
    path = arquivo(user)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _TRAVA_LOCAL:
        if os.name == 'nt':
            yield
        else:
            import fcntl
            fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)


def versao_credencial(pasta):
    try:
        info = (pasta / ARQUIVOS_OAUTH[0]).stat()
        return info.st_ino, info.st_mtime_ns, info.st_size
    except FileNotFoundError:
        return None


def pasta_oauth(user):
    return arquivo(user).with_suffix('')


def ativo(user):
    return (pasta_oauth(user) / ARQUIVOS_OAUTH[0]).is_file()


def preparar_cli(root, projeto='', modelo='', esforco=''):
    workspace = root / 'workspace'
    workspace.mkdir(mode=0o700, exist_ok=True)
    nativo = root / '.gemini'
    nativo.mkdir(mode=0o700, exist_ok=True)
    politica = root / 'deny.toml'
    politica.write_text('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n', encoding='utf-8')
    config = nativo / 'settings.json'
    preferencias = {
        'security': {'auth': {'selectedType': 'oauth-personal'}, 'disableYoloMode': True},
        'general': {'enableAutoUpdate': False, 'maxAttempts': 1},
        'telemetry': {'enabled': False, 'logPrompts': False},
        'tools': {'core': ['__iativa_sem_ferramentas__']},
    }
    if esforco:
        pensamento = ({'thinkingBudget': ORCAMENTOS[esforco]} if modelo.startswith('gemini-2.5-')
                      else {'thinkingLevel': esforco})
        preferencias['modelConfigs'] = {'customOverrides': [{
            'match': {'model': modelo},
            'modelConfig': {'generateContentConfig': {'thinkingConfig': pensamento}},
        }]}
    config.write_text(json.dumps(preferencias), encoding='utf-8')
    ambiente = {
        'PATH': os.defpath + os.pathsep + '/usr/local/bin',
        'HOME': str(root), 'GEMINI_CLI_HOME': str(root), 'XDG_CACHE_HOME': str(root),
        'TMPDIR': str(root), 'NO_BROWSER': 'true', 'TERM': 'dumb',
        'GEMINI_FORCE_FILE_STORAGE': 'true',
    }
    if projeto:
        ambiente['GOOGLE_CLOUD_PROJECT'] = projeto
    cmd = ['gemini', '--admin-policy', str(politica), '--extensions', 'none', '--approval-mode', 'default']
    return cmd, workspace, ambiente


def copiar_credenciais(origem, destino, *, existentes=False):
    """Transfere apenas os arquivos nativos de login, com gravação privada e atômica."""
    destino.mkdir(mode=0o700, exist_ok=True)
    for nome in ARQUIVOS_OAUTH:
        arquivo = origem / nome
        alvo = destino / nome
        if not arquivo.is_file() or (existentes and not alvo.is_file()):
            continue
        with NamedTemporaryFile(dir=destino, delete=False) as tmp:
            temp = Path(tmp.name)
            tmp.write(arquivo.read_bytes())
        try:
            temp.replace(alvo)
        finally:
            temp.unlink(missing_ok=True)


def _executar(user, modelo, prompt, esforco=''):
    _exigir_envio_liberado()
    restante = prazo.restante()
    if restante is not None and restante <= 0:
        raise prazo.PrazoEsgotado()
    timeout = float(settings.IATIVA_AGENTE.get('timeout_s', 90))
    if restante is not None:
        timeout = min(timeout, restante)
    origem = pasta_oauth(user)
    if not ativo(user):
        raise FalhaModelo('Conecte sua conta Google em Conexões de IA.')
    with TemporaryDirectory(prefix='iativa-gemini-') as pasta:
        root = Path(pasta)
        with trava(user):
            inicial = versao_credencial(origem)
            if inicial is None:
                raise FalhaModelo('Conecte sua conta Google em Conexões de IA.')
            cmd, workspace, ambiente = preparar_cli(root, configuracao(user).get('projeto', ''), modelo, esforco)
            temporaria = root / '.gemini'
            copiar_credenciais(origem, temporaria)
            copia = versao_credencial(temporaria)
        cmd.extend(['--output-format', 'json', '--prompt', 'Responda ao pedido recebido pela entrada padrão.'])
        if modelo:
            cmd.extend(['--model', modelo])
        try:
            processo = subprocess.run(cmd, cwd=workspace, env=ambiente, input=prompt, encoding='utf-8',
                                       capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise FalhaModelo('O Gemini CLI excedeu o prazo.', temporaria=True) from None
        except OSError:
            raise FalhaModelo('O Gemini CLI está indisponível no servidor.', temporaria=True) from None
        if versao_credencial(temporaria) not in (None, copia):
            with trava(user):
                if versao_credencial(origem) == inicial:
                    copiar_credenciais(temporaria, origem, existentes=True)
        if processo.returncode:
            raise FalhaModelo('O Gemini CLI recusou a consulta. Confira a conexão Google, o modelo e a cota da conta.',
                              temporaria=True)
        try:
            dados = json.loads(processo.stdout)
            resposta = dados['response']
            if dados.get('stats', {}).get('tools', {}).get('totalCalls', 0) or not isinstance(resposta, str):
                raise ValueError
            return resposta
        except (TypeError, KeyError, AttributeError, ValueError):
            raise FalhaModelo('O Gemini CLI devolveu uma resposta inválida.') from None


def salvar_conexao(user, origem, projeto):
    with trava(user):
        copiar_credenciais(origem, pasta_oauth(user))
        conexoes.gravar_json(arquivo(user), {'projeto': projeto, 'modelo': ''})


def salvar_modelo(user, modelo, esforco=''):
    modelo = modelo.strip()
    if modelo and not re.fullmatch(r'(?:auto|pro|flash|flash-lite|gemini-[a-z0-9.-]{1,72})', modelo):
        raise FalhaModelo('Informe um ID Gemini válido ou deixe o modelo padrão.')
    if esforco and esforco not in niveis(modelo):
        raise FalhaModelo('Escolha um esforço compatível com o modelo selecionado.')
    config = configuracao(user)
    if modelo == config.get('modelo', '') and esforco == config.get('esforco', ''):
        return
    _executar(user, modelo, 'Responda somente OK.', esforco)
    with trava(user):
        if not ativo(user) or config != configuracao(user):
            raise FalhaModelo('A conexão Gemini mudou. Verifique o modelo novamente.')
        conexoes.gravar_json(arquivo(user), {**config, 'modelo': modelo, 'esforco': esforco})


def desconectar(user):
    with trava(user):
        for nome in ARQUIVOS_OAUTH:
            (pasta_oauth(user) / nome).unlink(missing_ok=True)
        arquivo(user).unlink(missing_ok=True)
        if conexoes.preferido(user) == 'gemini':
            conexoes.arquivo(user, 'atendimento').unlink(missing_ok=True)


class GeminiCLIProvider(LLMProvider):
    nome = 'gemini-cli'

    def __init__(self, user):
        self.user = user
        config = configuracao(user)
        self.modelo = config.get('modelo', '')
        self.esforco = config.get('esforco', '')

    def _gerar(self, prompt, schema, _validar, _objetivos, _parametros):
        prompt += '\nResponda apenas com um objeto JSON neste esquema, sem Markdown:\n' + json.dumps(schema, ensure_ascii=False)
        texto = _executar(self.user, self.modelo, prompt, self.esforco).strip()
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
