"""Structured logging that accepts only reviewed, non-sensitive fields."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Iterable
from typing import Any


_ALLOWED_FIELDS = frozenset(
    {
        "request_id",
        "trace_id",
        "job_id",
        "account_id_masked",
        "provider",
        "operation",
        "error_class",
        "duration_ms",
        "queue_wait_ms",
        "bytes_in",
        "bytes_out",
        "result_count",
        "cache_state",
        "retries",
    }
)
_SENSITIVE_MARKERS = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "credential",
    "database_url",
    "body",
    "filename",
    "attachment",
    "recipient",
    "message_id",
)
_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s]+")


def mask_identifier(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= 8:
        return normalized[:2] + "***"
    return normalized[:4] + "***" + normalized[-4:]


def _safe_message(value: object) -> str:
    message = str(value or "log event").replace("\x00", " ").strip()[:160]
    lowered = message.casefold()
    if _URL_PATTERN.search(message) or any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "redacted log event"
    return message or "log event"


def _safe_field(key: str, value: object) -> object | None:
    normalized = str(key or "").strip().casefold()
    if normalized not in _ALLOWED_FIELDS:
        return None
    if normalized == "account_id_masked":
        return mask_identifier(str(value or ""))
    if normalized in {"duration_ms", "queue_wait_ms"}:
        return round(max(float(value or 0.0), 0.0), 3)
    if normalized in {"bytes_in", "bytes_out", "result_count", "retries"}:
        return max(int(value or 0), 0)
    return str(value or "").replace("\x00", "")[:191]


class SafeJsonFormatter(logging.Formatter):
    """Render only low-cardinality reviewed fields as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.casefold(),
            "component": str(getattr(record, "component", record.name))[:96],
            "message": _safe_message(record.getMessage()),
        }
        raw_fields = getattr(record, "safe_fields", {})
        if isinstance(raw_fields, dict):
            for key, value in raw_fields.items():
                safe = _safe_field(str(key), value)
                if safe is not None:
                    payload[str(key)] = safe
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SafeLogger:
    """Small logger facade that never forwards arbitrary keyword fields."""

    def __init__(self, logger: logging.Logger, component: str) -> None:
        self._logger = logger
        self.component = str(component or "flymail")[:96]

    def _emit(self, level: int, message: object, fields: dict[str, object]) -> None:
        reviewed = {
            key: value
            for key, value in fields.items()
            if str(key).casefold() in _ALLOWED_FIELDS
        }
        self._logger.log(
            level,
            _safe_message(message),
            extra={"component": self.component, "safe_fields": reviewed},
        )

    def debug(self, message: object, **fields: object) -> None:
        self._emit(logging.DEBUG, message, fields)

    def info(self, message: object, **fields: object) -> None:
        self._emit(logging.INFO, message, fields)

    def warning(self, message: object, **fields: object) -> None:
        self._emit(logging.WARNING, message, fields)

    def error(self, message: object, **fields: object) -> None:
        self._emit(logging.ERROR, message, fields)


def get_safe_logger(
    component: str,
    *,
    handlers: Iterable[logging.Handler] | None = None,
    level: int = logging.INFO,
) -> SafeLogger:
    logger = logging.getLogger(f"flymail.safe.{component}")
    logger.propagate = False
    logger.setLevel(level)
    if handlers is not None:
        logger.handlers[:] = list(handlers)
    elif not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(SafeJsonFormatter())
        logger.addHandler(handler)
    for handler in logger.handlers:
        if handler.formatter is None:
            handler.setFormatter(SafeJsonFormatter())
    return SafeLogger(logger, component)


__all__ = ["SafeJsonFormatter", "SafeLogger", "get_safe_logger", "mask_identifier"]
