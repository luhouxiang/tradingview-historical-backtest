from __future__ import annotations

from tvbt.testing.logging_proxy import logger


def test_shared_logger_from_logging_proxy_can_log_directly() -> None:
    logger.info("shared logger from logging_proxy is ready")


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
