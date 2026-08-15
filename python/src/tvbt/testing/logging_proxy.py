from __future__ import annotations

import atexit
import tempfile
from pathlib import Path
from typing import Any

from tvbt.logging_config import configure_logging, get_runtime_logger, set_runtime_logger

_logger, _runtime = configure_logging(
    Path(tempfile.mkdtemp(prefix="tvbt-test-logs-")) / "strategy.log",
    level="INFO",
    max_bytes=1024 * 1024,
    backup_count=9,
    project_root=Path.cwd(),
    logger_name="tvbt.tests",
)
set_runtime_logger(_logger)
atexit.register(_runtime.close)


class RuntimeLoggerProxy:
    def debug(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        get_runtime_logger().debug(event_or_message, message, fields, _stacklevel=4)

    def info(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        get_runtime_logger().info(event_or_message, message, fields, _stacklevel=4)

    def warning(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        get_runtime_logger().warning(event_or_message, message, fields, _stacklevel=4)

    def error(
        self,
        event_or_message: str,
        message: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        get_runtime_logger().error(event_or_message, message, fields, _stacklevel=4)


logger = RuntimeLoggerProxy()
structured_logger = _logger
