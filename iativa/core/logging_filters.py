"""Filtro de redação aplicado a todos os logs.

É uma defesa adicional: a regra principal é não registrar SQL, resultados, mensagens do chat
nem credenciais. Este filtro mascara o que escapar (ex.: mensagens de exceção de drivers).
"""
import logging
import re
import traceback

REDACTED = "[REDIGIDO]"

_PATTERNS = [
    # Comandos SQL: corta do comando até o fim do texto.
    (
        re.compile(
            r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|MERGE\s+INTO|EXEC(?:UTE)?\s|TRUNCATE\s+TABLE)[\s\S]*",
            re.IGNORECASE,
        ),
        "[SQL REDIGIDO]",
    ),
    # Credenciais em URLs: esquema://usuario:senha@host
    (re.compile(r"(://)[^/\s:@]+:[^@\s]+@"), r"\1" + REDACTED + "@"),
    # Pares chave=valor sensíveis (inclui strings de conexão ODBC: PWD=...).
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|senha|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)(?:bearer\s+)?[^\s;,'\"&]+"
        ),
        r"\1\2" + REDACTED,
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    # Query strings em linhas de acesso: GET /caminho?param=valor
    (re.compile(r"(/[^\s?\"]*)\?[^\s\"]*"), r"\1?" + REDACTED),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - nunca derrubar a aplicação por causa do log
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact(text)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
