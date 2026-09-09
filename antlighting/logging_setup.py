"""Logging with a rotating file handler and credential redaction.

Public share links embed credentials.  They are useful for diagnostics but must
not end up in a log file, so every record passes through a filter that masks
the userinfo portion of any URI.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from typing import Any

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

LOG_FILE = "antlighting.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

# ``vless://<uuid>@host`` -> ``vless://***@host``
_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>vless|vmess|trojan|ss|ssr|hysteria2|hy2|tuic|wireguard)://(?P<user>[^@\s/]{4,})@",
    re.IGNORECASE,
)
# ``password=...`` / ``pbk=...`` in free text
_KV_RE = re.compile(
    r"(?P<key>password|passwd|pbk|publicKey|privateKey|secretKey|token)(?P<sep>[=:]\s*)(?P<val>\S+)",
    re.IGNORECASE,
)


class RedactingFilter(logging.Filter):
    """Strip credentials from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        masked = redact(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def redact(text: str) -> str:
    """Mask credentials inside *text*."""
    if not text:
        return text
    text = _CREDENTIAL_RE.sub(lambda m: f"{m.group('scheme')}://***@", text)
    text = _KV_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}***", text)
    return text


def setup_logging(
    data_dir: str,
    level: str = "INFO",
    *,
    to_console: bool = False,
    filename: str = LOG_FILE,
) -> logging.Logger:
    """Configure the root ``antlighting`` logger.  Safe to call more than once."""
    logger = logging.getLogger("antlighting")
    logger.setLevel(LEVELS.get(str(level).upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    redactor = RedactingFilter()

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        os.makedirs(data_dir, exist_ok=True)
        file_handler: logging.Handler = logging.handlers.RotatingFileHandler(
            os.path.join(data_dir, filename),
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        file_handler = logging.NullHandler()
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(redactor)
        logger.addHandler(console)

    return logger


def read_log_tail(data_dir: str, lines: int = 300, filename: str = LOG_FILE) -> list[str]:
    path = os.path.join(data_dir, filename)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()[-lines:]
    except OSError:
        return []


def diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    """A shareable snapshot that contains no secrets."""
    return {
        "app": data.get("app"),
        "core": data.get("core"),
        "tunnel": data.get("tunnel"),
        "pool": data.get("pool"),
        "sources": data.get("sources"),
        "network": data.get("network"),
    }
