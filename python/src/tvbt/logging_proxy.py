from __future__ import annotations

from typing import Any

from tvbt.logging_config import get_runtime_logger


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
