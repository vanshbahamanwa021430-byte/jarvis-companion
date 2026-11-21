"""Structured (JSON-lines) logging. Never log bodies, cookies, keys, or results."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, TextIO

LOGGER_NAME = "jarvis"
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        return json.dumps(entry, ensure_ascii=True, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{k}={v}" for k, v in vars(record).items()
            if k not in _RESERVED and not k.startswith("_")
        )
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"{stamp} {record.levelname:<7} {record.getMessage()}" + (f" {extras}" if extras else "")


def setup_logging(level: str = "INFO", fmt: str = "json", stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())
