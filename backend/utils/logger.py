"""Structured, secret-redacting logging with separate category log files."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"

LOG_CATEGORIES = {
    "app": "app.log",
    "ssh": "ssh.log",
    "axl": "axl.log",
    "commands": "commands.log",
    "collector": "collector.log",
    "ai": "ai.log",
    "errors": "errors.log",
}

_SENSITIVE_ENV_VARS = (
    "CUCM_PASSWORD",
    "CUCM_CLI_PASSWORD",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
)


class SecretRedactingFilter(logging.Filter):
    """Filter that redacts sensitive credentials from log messages and args."""

    def __init__(self, extra_secrets: Optional[Iterable[str]] = None):
        super().__init__()
        self._extra_secrets = [s for s in (extra_secrets or []) if s]

    def _collect_secrets(self) -> list[str]:
        secrets = list(self._extra_secrets)
        for var in _SENSITIVE_ENV_VARS:
            val = os.getenv(var, "")
            if val and val != "<SET_LOCALLY>" and len(val) >= 3:
                secrets.append(val)
        return secrets

    def _redact(self, text: str, secrets: list[str]) -> str:
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED]")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = self._collect_secrets()
        if not secrets:
            return True
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg, secrets)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(a, secrets) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: self._redact(v, secrets) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True


_CONFIGURED_LOGGERS: dict[str, logging.Logger] = {}
_SHARED_ERROR_HANDLER: Optional[RotatingFileHandler] = None


def _get_error_handler(formatter: logging.Formatter, redactor: SecretRedactingFilter) -> RotatingFileHandler:
    global _SHARED_ERROR_HANDLER
    if _SHARED_ERROR_HANDLER is None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOGS_DIR / LOG_CATEGORIES["errors"],
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.ERROR)
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
        _SHARED_ERROR_HANDLER = handler
    return _SHARED_ERROR_HANDLER


def get_logger(name: str, category: str = "app") -> logging.Logger:
    """Return a configured logger writing to its dedicated category log and errors.log."""
    cache_key = f"{category}:{name}"
    if cache_key in _CONFIGURED_LOGGERS:
        return _CONFIGURED_LOGGERS[cache_key]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logger = logging.getLogger(f"cucm_monitor.{category}.{name}")
    logger.setLevel(log_level)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        redactor = SecretRedactingFilter()

        filename = LOG_CATEGORIES.get(category, LOG_CATEGORIES["app"])
        file_handler = RotatingFileHandler(
            LOGS_DIR / filename,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redactor)
        logger.addHandler(console_handler)

        logger.addHandler(_get_error_handler(formatter, redactor))

    _CONFIGURED_LOGGERS[cache_key] = logger
    return logger
