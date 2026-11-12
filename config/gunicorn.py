"""Servidor de aplicação (M31): gunicorn com UM processo e várias threads.

Um processo só porque o login do Claude e o "Pensando…" ao vivo ficam na memória do processo
(docs/CLAUDE_CODE.md). Threads atendem várias consultas ao mesmo tempo.

Escuta em 0.0.0.0 só dentro do container (exceção T6); fora dele, só em 127.0.0.1. A porta do
host continua publicada apenas em 127.0.0.1 (proxy no compose.yaml).
"""
import os
from pathlib import Path

_container = os.environ.get("IATIVA_ALLOW_CONTAINER_BIND") == "1" and Path("/.dockerenv").exists()
bind = "0.0.0.0:8000" if _container else "127.0.0.1:8000"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("IATIVA_THREADS", "16"))
timeout = 600  # consultas longas do agente; o prazo real é o do ciclo (IATIVA_AGENTE_CICLO)
graceful_timeout = int(os.environ.get("IATIVA_DRENAGEM_S", "900"))  # SIGTERM: termina o que já começou
keepalive = 5
accesslog = None  # sem log de acesso: URLs podem carregar parâmetros
errorlog = "-"
loglevel = "warning"
