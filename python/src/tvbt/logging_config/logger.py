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
from pathlib import Path
from time import struct_time
from typing import Any

CONTEXT_FIELDS = {
    "request_id",
    "trace_id",
    "job_id",
    "run_id",
    "replay_id",
    "dataset_id",
    "data_revision",
    "algorithm_id",
    "algorithm_version",
    "strategy_id",
    "strategy_version",
    "cache_key",
    "bar_index",
    "bar_time",
    "sequence",
    "stage_signal_id",
    "signal_id",
    "parent_signal_id",
    "duration_ms",
}


class NDJSONFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float | None) -> struct_time:
        return time.gmtime() if timestamp is None else time.gmtime(timestamp)

    def __init__(self, service: str, project_root: Path) -> None:
        super().__init__()
        self.service = service
        self.project_root = project_root.resolve()

    def format(self, record: logging.LogRecord) -> str:
        path = Path(record.pathname).resolve()
        try:
            source_file = path.relative_to(self.project_root).as_posix()
        except ValueError:
            source_file = path.name
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "event": getattr(record, "event", "logging.message"),
            "message": record.getMessage(),
            "source_file": source_file,
            "source_line": record.lineno,
            "source_function": record.funcName,
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            remaining = {}
            for key, value in fields.items():
                if key in CONTEXT_FIELDS:
                    payload[key] = value
                else:
                    remaining[key] = value
            if remaining:
                payload["fields"] = remaining
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    handler.setFormatter(NDJSONFormatter("python-engine", project_root))
    listener = logging.handlers.QueueListener(records, handler, respect_handler_level=True)
    listener.start()
    return StructuredLogger(logger), LoggingRuntime(listener, degraded)
