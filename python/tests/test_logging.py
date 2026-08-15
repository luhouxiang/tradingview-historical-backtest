from __future__ import annotations

import re
from pathlib import Path

from tvbt.logging_config import configure_logging


def test_source_points_at_business_caller(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.log"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=1024 * 1024, backup_count=9, project_root=Path.cwd()
    )
    logger.info("test.event", "hello")
    runtime.close()
    line = log_path.read_text(encoding="utf-8").strip()
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]"
        r"\[INFO\]\[python/tests/test_logging.py\]\[\d{3,}\] test\.event hello$",
        line,
    )


def test_rotation_compresses_and_caps_backups(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.log"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=512, backup_count=9, project_root=Path.cwd()
    )
    for index in range(300):
        logger.info("test.rotation", "x" * 120, {"sequence": index})
    runtime.close()
    files = list(tmp_path.glob("strategy.log*"))
    assert len(files) <= 10
    assert any(path.suffix == ".gz" for path in files)


def test_fields_are_appended_to_text_message(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.log"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=1024, backup_count=9, project_root=Path.cwd()
    )
    logger.info("test.event", "hello", {"trace_id": "trace-1", "custom": "value"})
    runtime.close()
    line = log_path.read_text(encoding="utf-8").strip()
    assert "test.event hello" in line
    assert '"trace_id":"trace-1"' in line
    assert '"custom":"value"' in line


def test_multiline_messages_repeat_the_same_prefix(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.log"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=1024, backup_count=9, project_root=Path.cwd()
    )
    logger.error("test.multiline", "first\nsecond")
    runtime.close()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    prefix_pattern = re.compile(
        r"^(\[[^\]]+\]\[ERROR\]\[python/tests/test_logging.py\]\[\d{3,}\] )"
    )
    first = prefix_pattern.match(lines[0])
    second = prefix_pattern.match(lines[1])
    assert first is not None
    assert second is not None
    assert first.group(1) == second.group(1)
    assert lines[0].endswith("test.multiline first")
    assert lines[1].endswith("second")

