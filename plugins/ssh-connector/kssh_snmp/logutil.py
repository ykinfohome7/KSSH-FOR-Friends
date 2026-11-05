# GENERATED COPY of monitoring/logutil.py - edit the original, then run scripts/sync_plugin_snmp.py
"""Structured JSON logging with secret redaction.

Two layers of protection:
1. Any field whose *name* looks secret (password, key, token, community ...)
   is replaced with ``"[REDACTED]"``.
2. Any registered secret *value* is scrubbed from every string, including the
   message itself, so a secret that leaks into an exception text is still hidden.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(pass(word|phrase)?|secret|token|api[_-]?key|auth[_-]?key|priv[_-]?key|community|credential|authorization)",
    re.IGNORECASE,
)
# Field names that contain "auth"/"priv" but only hold algorithm names or env-var names.
_SAFE_KEYS = frozenset({
    "auth_protocol", "priv_protocol", "auth_env", "priv_env", "community_env",
    "security_level", "error_code",
})

_lock = threading.Lock()
_secret_values: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value so it is scrubbed from all log output."""
    if value and len(value) >= 4:
        with _lock:
            _secret_values.add(value)


def clear_registered_secrets() -> None:  # for tests
    with _lock:
        _secret_values.clear()


def scrub_text(text: str) -> str:
    with _lock:
        values = sorted(_secret_values, key=len, reverse=True)
    for value in values:
        if value in text:
            text = text.replace(value, REDACTED)
    return text


def redact(obj: Any) -> Any:
    """Return a deep copy of *obj* with secret-looking fields and values removed."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            skey = str(key)
            if skey.lower() not in _SAFE_KEYS and _SECRET_KEY.search(skey):
                out[skey] = REDACTED
            else:
                out[skey] = redact(value)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        # Exceptions are summarised by type only; backend texts may echo secrets.
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(redact(payload), sort_keys=True, default=str)


class _StderrHandler(logging.StreamHandler):
    """Writes to the *current* sys.stderr (so redirection and test capture work)."""

    def __init__(self):
        super().__init__()

    @property
    def stream(self):
        import sys
        return sys.stderr

    @stream.setter
    def stream(self, _value):
        pass


def get_logger(name: str = "kssh.monitor") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = _StderrHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})
