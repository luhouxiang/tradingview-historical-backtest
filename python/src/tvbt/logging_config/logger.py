from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import queue
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import struct_time
from typing import Any

LOG_METADATA_FIELDS = {
    "timestamp",
    "level",
    "event",
    "message",
    "source_file",
    "source_line",
    "source_function",
}


def format_fixed_text_entry(
    *,
    timestamp: datetime,
    level: str,
    source_file: str,
    source_line: int,
    event: str,
    message: str,
    fields: dict[str, Any] | None = None,
) -> str:
    timestamp_text = (
        timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        + f".{timestamp.microsecond // 1000:03d}"
    )
    prefix = f"[{timestamp_text}][{level}][{source_file}][{source_line:03d}] "
    parts = [value for value in (event, message) if value]
    if fields:
        parts.append(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    content = " ".join(parts)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(prefix + line for line in normalized.split("\n"))


class FixedTextFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float | None) -> struct_time:
        return time.localtime() if timestamp is None else time.localtime(timestamp)

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root.resolve()

    def format(self, record: logging.LogRecord) -> str:
        path = Path(record.pathname).resolve()
        try:
            source_file = path.relative_to(self.project_root).as_posix()
        except ValueError:
            source_file = path.name
        event = getattr(record, "event", "logging.message")
        fields = getattr(record, "fields", None)
        return format_fixed_text_entry(
            timestamp=datetime.fromtimestamp(record.created).astimezone(),
            level=record.levelname,
            source_file=source_file,
            source_line=record.lineno,
            event=event if isinstance(event, str) else "logging.message",
            message=record.getMessage(),
            fields=fields if isinstance(fields, dict) else None,
        )


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as input_file, gzip.open(destination, "wb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    os.remove(source)


@dataclass
class LoggingRuntime:
    listener: logging.handlers.QueueListener
    degraded: bool

    def close(self) -> None:
        self.listener.stop()


class StructuredLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        self._logger.debug(message, extra={"event": event, "fields": fields or {}}, stacklevel=2)

    def info(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        self._logger.info(message, extra={"event": event, "fields": fields or {}}, stacklevel=2)

    def warning(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        self._logger.warning(message, extra={"event": event, "fields": fields or {}}, stacklevel=2)

    def error(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        self._logger.error(message, extra={"event": event, "fields": fields or {}}, stacklevel=2)


def configure_logging(
    path: Path,
    *,
    level: str,
    max_bytes: int,
    backup_count: int,
    project_root: Path,
) -> tuple[StructuredLogger, LoggingRuntime]:
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    logger = logging.getLogger("tvbt")
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(logging.handlers.QueueHandler(records))
    degraded = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        rotating = handler
        assert isinstance(rotating, logging.handlers.RotatingFileHandler)
        rotating.namer = lambda name: name + ".gz"
        rotating.rotator = _gzip_rotator
    except OSError:
        handler = logging.NullHandler()
        degraded = True
    handler.setFormatter(FixedTextFormatter(project_root))
    listener = logging.handlers.QueueListener(records, handler, respect_handler_level=True)
    listener.start()
    return StructuredLogger(logger), LoggingRuntime(listener, degraded)
