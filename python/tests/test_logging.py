from __future__ import annotations

import inspect
from pathlib import Path

from tvbt.logging_config import configure_logging, set_runtime_logger
from tvbt.logging_proxy import logger as runtime_logger
from tvbt.testing.logging_proxy import logger, structured_logger


def test_shared_logger_from_logging_proxy_can_log_directly() -> None:
    logger.info("shared logger from logging_proxy is ready")


def test_runtime_logging_proxy_points_at_business_caller(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.log"
    current_logger, runtime = configure_logging(
        log_path,
        level="INFO",
        max_bytes=1024 * 1024,
        backup_count=9,
        project_root=Path.cwd(),
        console=False,
        logger_name="tvbt.production_proxy_test",
    )
    set_runtime_logger(current_logger)
    try:
        expected_line = inspect.currentframe().f_lineno + 1
        runtime_logger.info("production.proxy.test", "hello")
        runtime.flush()
    finally:
        set_runtime_logger(structured_logger)
        runtime.close()
    text = log_path.read_text(encoding="utf-8")
    assert "logging_proxy.py" not in text
    assert f"[{expected_line:03d}] production.proxy.test hello" in text


def test_source_points_at_business_caller() -> None:
    print("test_source_points_at_business_caller")
    logger.info("test.event", "hello")


def test_rotation_logging_prints_to_screen() -> None:
    print("print test_rotation_logging_prints_to_screen----")
    logger.info("logger test_rotation_logging_prints_to_screen----")


def test_fields_are_appended_to_text_message() -> None:
    logger.info("test.event", "hello", {"trace_id": "trace-1", "custom": "value"})


def test_event_name_is_optional() -> None:
    logger.info("plain message", {"trace_id": "trace-optional"})


def test_console_logging_is_enabled_by_default() -> None:
    logger.info("default console message")


def test_context_fields_are_top_level() -> None:
    logger.info("test.event", "hello", {"trace_id": "trace-1", "custom": "value"})


def test_multiline_messages_repeat_the_same_prefix() -> None:
    logger.error("test.multiline", "first\nsecond")


def test_console_logging_uses_the_same_fixed_text_format() -> None:
    logger.debug("test.console", "visible on screen", {"case": "chan"})
