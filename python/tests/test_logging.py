from __future__ import annotations

import json
from pathlib import Path

from tvbt.logging_config import configure_logging


def test_source_points_at_business_caller(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.ndjson"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=1024 * 1024, backup_count=9, project_root=Path.cwd()
    )
    logger.info("test.event", "hello")
    runtime.close()
    event = json.loads(log_path.read_text(encoding="utf-8"))
    assert event["source_file"].endswith("test_logging.py")
    assert event["source_line"] > 0


def test_rotation_compresses_and_caps_backups(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.ndjson"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=512, backup_count=9, project_root=Path.cwd()
    )
    for index in range(300):
        logger.info("test.rotation", "x" * 120, {"sequence": index})
    runtime.close()
    files = list(tmp_path.glob("strategy.ndjson*"))
    assert len(files) <= 10
    assert any(path.suffix == ".gz" for path in files)


def test_context_fields_are_top_level(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy.ndjson"
    logger, runtime = configure_logging(
        log_path, level="INFO", max_bytes=1024, backup_count=9, project_root=Path.cwd()
    )
    logger.info("test.event", "hello", {"trace_id": "trace-1", "custom": "value"})
    runtime.close()
    event = json.loads(log_path.read_text(encoding="utf-8"))
    assert event["trace_id"] == "trace-1"
    assert event["fields"]["custom"] == "value"
