from __future__ import annotations

from typing import Any

from tvbt.logging_config.logger import StructuredLogger


class NullStructuredLogger:
    """No-op logger used when code is called outside the Python service process."""

    def debug(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        return

    def info(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        return

    def warning(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        return

    def error(self, event: str, message: str, fields: dict[str, Any] | None = None) -> None:
        return


_runtime_logger: StructuredLogger | NullStructuredLogger = NullStructuredLogger()


def set_runtime_logger(logger: StructuredLogger) -> None:
    """Register the process-wide structured logger for lower-level modules."""
    global _runtime_logger
    _runtime_logger = logger


def clear_runtime_logger() -> None:
    """Reset the process logger to no-op, mainly for isolated tests."""
    global _runtime_logger
    _runtime_logger = NullStructuredLogger()


def get_runtime_logger() -> StructuredLogger | NullStructuredLogger:
    """Return the process logger.

    Usage from any Python business module:

    ```python
    from tvbt.logging_config import get_runtime_logger

    get_runtime_logger().info("event.name", "human readable message", {"job_id": job_id})
    ```

    Tests and one-off scripts that do not configure logging receive a no-op logger.
    """
    return _runtime_logger
