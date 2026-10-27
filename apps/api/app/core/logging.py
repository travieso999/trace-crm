"""Structured JSON logging with secret redaction."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_REDACT_KEYS = re.compile(r"(token|secret|password|authorization|cookie|api_key|code_verifier)", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I)


def _scrub(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _REDACT_KEYS.search(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _BEARER.sub(r"\1[REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _scrub(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = _scrub(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **data) -> None:
    logger.log(level, msg, extra={"data": data} if data else None)
