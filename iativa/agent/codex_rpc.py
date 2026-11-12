"""Cliente stdio do app-server oficial. Sem HTTP próprio de OAuth ou cópia de tokens."""
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
from collections import deque

from .claude_code import ENV_PERMITIDO
from .provedores import FalhaModelo


def resolver_cli(configurado='codex'):
    path = shutil.which(configurado)
    if path and Path(path).suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        return path
    candidate = Path(configurado).expanduser()
    if candidate.is_absolute() and candidate.is_file() and candidate.suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        return str(candidate)
    if configurado == 'codex' and os.name == 'nt':
        root = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex/node_modules/@openai'
        paths = list(root.glob('codex-win32-*/vendor/*/bin/codex.exe'))
        if paths:
            return str(paths[0])
    raise FalhaModelo('Codex CLI não encontrado neste ambiente. Instale a CLI oficial no servidor.')


def ambiente(pasta):
    root = Path(pasta)
    if not root.is_absolute():
        raise FalhaModelo('O diretório da conexão ChatGPT deve ser absoluto.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_PERMITIDO}
    env.update(CODEX_HOME=str(root), NO_COLOR='1', RUST_LOG='off')
    return env


class AppServer:
    def __init__(self, pasta, cli='codex', timeout=25):
        self.tmp = TemporaryDirectory(prefix='iativa-codex-')
        self.cwd, self.proc = self.tmp.name, None
        self.fila, self.eventos = Queue(maxsize=100), deque()
        self.serial, self.timeout = 0, timeout
        config = {'cli_auth_credentials_store': 'file', 'forced_login_method': 'chatgpt',
                  'log_dir': self.cwd, 'history.persistence': 'none', 'analytics.enabled': False,
                  'features.shell_tool': False, 'features.multi_agent': False, 'features.apps': False,
                  'features.plugins': False, 'features.hooks': False, 'features.code_mode': False,
                  'features.browser_use': False, 'features.computer_use': False,
                  'features.skill_search': False, 'features.memories': False, 'web_search': 'disabled'}
        config['model_provider'] = 'openai'
        args = [resolver_cli(cli), 'app-server', '--listen', 'stdio://']
        for k, v in config.items():
            args += ['-c', f'{k}={json.dumps(v)}']
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self.cwd, env=ambiente(pasta), shell=False,
                start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.leitor = Thread(target=self._ler, daemon=True)
            self.leitor.start()
            self.request('initialize', {'clientInfo': {'name': 'iativa', 'version': '0.1.0'},
                                       'capabilities': {'experimentalApi': True}})
            self.enviar({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise FalhaModelo('Não foi possível iniciar o Codex oficial. Confira a instalação da CLI.') from None

    def _ler(self):
        try:
            while linha := self.proc.stdout.readline(1_000_001):
                if len(linha) > 1_000_000:
                    break
                try:
                    msg = json.loads(linha)
                    if not isinstance(msg, dict):
                        break
                    self.fila.put(msg, timeout=1)
                except (ValueError, Full):
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.fila.put(None, timeout=1)
            except Full:
                pass

    def enviar(self, msg):
        try:
            self.proc.stdin.write((json.dumps(msg) + '\n').encode())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise FalhaModelo('A conexão com o Codex foi encerrada.') from None

    def _receber(self, timeout):
        try:
            msg = self.fila.get(timeout=max(0.01, timeout))
        except Empty:
            raise FalhaModelo('Codex excedeu o tempo limite.', temporaria=True) from None
        if msg is None:
            raise FalhaModelo('Codex encerrou a conexão antes de concluir.')
        # O atendimento não aprova comandos, ferramentas, rede ou MCP solicitados pelo modelo.
        if 'id' in msg and 'method' in msg:
            self.enviar({'id': msg['id'], 'error': {'code': -32601, 'message': 'Operação não autorizada pelo atendimento'}})
            return {}
        return msg

    def request(self, method, params=None, timeout=None):
        self.serial += 1
        id_ = self.serial
        self.enviar({'id': id_, 'method': method, 'params': params or {}})
        fim = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < fim:
            msg = self._receber(fim - time.monotonic())
            if msg.get('id') == id_:
                if 'error' in msg:
                    raise FalhaModelo('O Codex recusou a operação. Confira login, modelo e acesso da conta.')
                return msg.get('result', {})
            if msg.get('method'):
                if len(self.eventos) >= 100:
                    raise FalhaModelo('O Codex excedeu o limite de eventos.')
                self.eventos.append(msg)
        raise FalhaModelo('Codex excedeu o tempo limite.', temporaria=True)

    def evento(self, timeout):
        return self.eventos.popleft() if self.eventos else self._receber(timeout)

    def autenticado(self):
        account = self.request('account/read', {'refreshToken': False}).get('account')
        if account is None:
            return False
        if not isinstance(account, dict) or account.get('type') != 'chatgpt':
            raise FalhaModelo('Use Entrar com ChatGPT. Chave de API e autenticação alternativa não são aceitas aqui.')
        return True

    def close(self):
        if self.proc:
            if self.proc.poll() is None:
                try:
                    if os.name != 'nt':
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    else:
                        self.proc.terminate()
                    self.proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    if os.name != 'nt':
                        try:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                        except OSError:
                            pass
                    else:
                        self.proc.kill()
                    self.proc.wait(timeout=3)
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream:
                    stream.close()
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
