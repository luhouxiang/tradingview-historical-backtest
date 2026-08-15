from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import queue
import shutil
import sys
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


class CurrentStdoutHandler(logging.StreamHandler):
    def __init__(self) -> None:
        super().__init__(sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)

    def flush(self) -> None:
        try:
            self.stream = sys.stdout
            super().flush()
        except ValueError:
            return


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as input_file, gzip.open(destination, "wb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    os.remove(source)


@dataclass
class LoggingRuntime:
    listener: logging.handlers.QueueListener
    records: queue.Queue[logging.LogRecord]
    handlers: list[logging.Handler]
    degraded: bool

    def flush(self) -> None:
        self.records.join()
        for handler in self.handlers:
            handler.flush()

    def close(self) -> None:
        self.flush()
        self.listener.stop()


class StructuredLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        _stacklevel: int = 3,
    ) -> None:
        self._log(logging.DEBUG, event_or_message, message, fields, _stacklevel)

    def info(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        _stacklevel: int = 3,
    ) -> None:
        self._log(logging.INFO, event_or_message, message, fields, _stacklevel)

    def warning(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        _stacklevel: int = 3,
    ) -> None:
        self._log(logging.WARNING, event_or_message, message, fields, _stacklevel)

    def error(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        _stacklevel: int = 3,
    ) -> None:
        self._log(logging.ERROR, event_or_message, message, fields, _stacklevel)

    def _log(
        self,
        level: int,
        event_or_message: str,
        message: str | dict[str, Any] | None,
        fields: dict[str, Any] | None,
        stacklevel: int,
    ) -> None:
        event, text, resolved_fields = _resolve_log_arguments(event_or_message, message, fields)
        self._logger.log(
            level,
            text,
            extra={"event": event, "fields": resolved_fields},
            stacklevel=stacklevel,
        )


def _resolve_log_arguments(
    event_or_message: str,
    message: str | dict[str, Any] | None,
    fields: dict[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    if isinstance(message, dict) and fields is None:
        return "", event_or_message, message
    if message is None:
        return "", event_or_message, fields or {}
    return event_or_message, message, fields or {}


def configure_logging(
    path: Path,
    *,
    level: str,
    max_bytes: int,
    backup_count: int,
    project_root: Path,
    console: bool = True,
    logger_name: str = "tvbt",
) -> tuple[StructuredLogger, LoggingRuntime]:
    records: queue.Queue[logging.LogRecord] = queue.Queue()
    logger = logging.getLogger(logger_name)
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
    handlers: list[logging.Handler] = [handler]
    if console:
        console_handler = CurrentStdoutHandler()
        console_handler.setFormatter(FixedTextFormatter(project_root))
        handlers.append(console_handler)
    listener = logging.handlers.QueueListener(records, *handlers, respect_handler_level=True)
    listener.start()
    return StructuredLogger(logger), LoggingRuntime(listener, records, handlers, degraded)
