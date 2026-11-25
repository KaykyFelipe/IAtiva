"""Run the installed Gemini CLI's native manual OAuth flow on a private PTY."""
import errno
import json
import os
import re
import select
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from . import conexoes, gemini_cli
from .provedores import FalhaModelo

_JANELA = 300
_LOCK = RLock()
_FLUXOS = {}


def credencial_pronta(caminho):
    try:
        credencial = json.loads(caminho.read_text(encoding='utf8'))
        return isinstance(credencial, dict) and bool(credencial.get('refresh_token'))
    except (OSError, ValueError):
        return False


def link_oficial(texto):
    for candidate in re.findall(r'https://accounts\.google\.com/[^\s\x1b]+', texto):
        try:
            url = urlsplit(candidate)
            params = parse_qs(url.query)
            if (url.netloc == 'accounts.google.com' and url.path in ('/o/oauth2/v2/auth', '/o/oauth2/auth')
                    and params.get('redirect_uri') == ['https://codeassist.google.com/authcode']
                    and params.get('state') and params.get('code_challenge')):
                return candidate
        except ValueError:
            continue
    return ''


@dataclass
class Fluxo:
    user: object
    sessao: str
    projeto: str
    id: str = field(default_factory=lambda: str(uuid4()))
    fase: str = 'iniciando'
    mensagem: str = 'Preparando o login oficial do Google…'
    url: str = ''
    terminal: int | None = None

    def estado(self):
        with _LOCK:
            return {'id': self.id, 'fase': self.fase, 'mensagem': self.mensagem, 'url': self.url,
                    'pendente': self.fase in ('iniciando', 'aguardando', 'confirmando')}

    def finalizar(self, fase, mensagem):
        self.fase, self.mensagem, self.url = fase, mensagem, ''

    def rodar(self):
        buffer = ''
        try:
            import pty
            with TemporaryDirectory(prefix='iativa-gemini-login-') as pasta:
                root = Path(pasta)
                cmd, cwd, env = gemini_cli.preparar_cli(root, self.projeto)
                master, slave = pty.openpty()
                try:
                    try:
                        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                                start_new_session=True)
                    finally:
                        os.close(slave)
                    try:
                        with _LOCK:
                            self.terminal = master
                        ini = time.monotonic()
                        limite = ini + _JANELA
                        while time.monotonic() < limite:
                            with _LOCK:
                                if not self.estado()['pendente']:
                                    return
                                if self.fase == 'iniciando' and time.monotonic() - ini > 30:
                                    break
                                creds = root / '.gemini'
                                pendente = self.fase == 'confirmando'
                                if pendente and credencial_pronta(creds / gemini_cli.ARQUIVOS_OAUTH[0]):
                                    gemini_cli.salvar_conexao(self.user, creds, self.projeto)
                                    self.finalizar('conectado', 'Conta Google conectada.')
                                    return
                            if not select.select([master], [], [], .25)[0]:
                                continue
                            try:
                                dados = os.read(master, 16384)
                            except OSError as exc:
                                if exc.errno != errno.EIO:
                                    raise
                                dados = b''
                            if not dados:
                                break
                            buffer = (buffer + dados.decode('utf-8', errors='replace'))[-65536:]
                            with _LOCK:
                                if self.fase == 'iniciando' and 'Enter the authorization code:' in buffer:
                                    url = link_oficial(buffer)
                                    if url:
                                        self.url, self.fase = url, 'aguardando'
                                        self.mensagem = 'Autorize no Google e cole o código exibido.'
                                if pendente and 'Failed to authenticate' in buffer:
                                    break
                    finally:
                        with _LOCK:
                            self.terminal = None
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait()
                finally:
                    os.close(master)
        except (ImportError, OSError, ValueError, FalhaModelo):
            pass
        with _LOCK:
            if self.estado()['pendente']:
                self.finalizar('falhou', 'O Google não confirmou a conexão. Confira a conta e o projeto e tente novamente.')


def iniciar(user, sessao, projeto):
    gemini_cli.arquivo(user)  # valida o acesso ao perfil
    projeto = projeto.strip()
    if projeto and not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', projeto):
        raise FalhaModelo('Informe o ID do projeto Google Cloud ou deixe o campo em branco.')
    with _LOCK:
        atual = _FLUXOS.get(user.pk)
        if atual and atual.estado()['pendente']:
            if atual.sessao != sessao:
                raise FalhaModelo('Já existe um login em andamento em outra sessão sua.')
            return atual.estado()
        fluxo = Fluxo(user, sessao, projeto)
        _FLUXOS[user.pk] = fluxo
        Thread(target=fluxo.rodar, daemon=True).start()
        return fluxo.estado()


def obter(user, sessao, fluxo_id):
    fluxo = _FLUXOS.get(user.pk)
    if not fluxo or fluxo.id != fluxo_id or fluxo.sessao != sessao:
        raise FalhaModelo('Este login expirou ou pertence a outra sessão. Conecte novamente.')
    return fluxo


def estado(user, sessao, fluxo_id):
    with _LOCK:
        return obter(user, sessao, fluxo_id).estado()


def concluir(user, sessao, fluxo_id, codigo):
    if not re.fullmatch(r'[A-Za-z0-9._~/-]{8,1024}', codigo):
        raise FalhaModelo('Cole o código completo exibido pelo Google.')
    with _LOCK:
        fluxo = obter(user, sessao, fluxo_id)
        if fluxo.fase != 'aguardando' or fluxo.terminal is None:
            raise FalhaModelo('Este login já foi utilizado ou expirou.')
        try:
            os.write(fluxo.terminal, (codigo + '\n').encode('ascii'))
        except OSError:
            fluxo.finalizar('falhou', 'O Gemini CLI encerrou o login. Conecte novamente.')
        else:
            fluxo.finalizar('confirmando', 'Confirmando a autorização com o Google…')
        return fluxo.estado()


def cancelar(user, sessao, fluxo_id):
    with _LOCK:
        fluxo = obter(user, sessao, fluxo_id)
        if fluxo.estado()['pendente']:
            fluxo.finalizar('cancelado', 'Conexão cancelada.')
        return fluxo.estado()


def desconectar(user, sessao):
    with _LOCK:
        fluxo = _FLUXOS.get(user.pk)
        if fluxo and fluxo.estado()['pendente']:
            if fluxo.sessao != sessao:
                raise FalhaModelo('Cancele o login na sessão que o iniciou antes de desconectar.')
            fluxo.finalizar('cancelado', 'Conexão cancelada.')
        gemini_cli.desconectar(user)
