from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COVERAGE_PATH = REPOSITORY_ROOT / "docs" / "chanlun-algorithm-coverage.json"
ALLOWED_STATUSES = {
    "implemented",
    "fixed_level_projection",
    "partial",
    "deferred",
}
REQUIRED_RECORD_KEYS = {
    "catalog_id",
    "status",
    "implemented_emits",
    "missing_emits",
    "implementation_refs",
    "test_refs",
    "chart_refs",
    "limitations",
    "next_milestone",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _assert_refs_exist(refs: list[str]) -> None:
    for reference in refs:
        path_text, separator, symbol = reference.partition("#")
        path = (REPOSITORY_ROOT / path_text).resolve()
        assert path.is_relative_to(REPOSITORY_ROOT), reference
        assert path.is_file(), reference
        if separator:
            assert symbol
            assert symbol in path.read_text(encoding="utf-8"), reference


def test_chan_108_coverage_matches_the_pinned_catalog_and_evidence() -> None:
    coverage = _load_json(COVERAGE_PATH)
    catalog_path = (REPOSITORY_ROOT / coverage["catalog_path"]).resolve()
    assert catalog_path.is_relative_to(REPOSITORY_ROOT)
    assert catalog_path.is_file()
    assert hashlib.sha256(catalog_path.read_bytes()).hexdigest() == coverage["catalog_sha256"]

    catalog = _load_json(catalog_path)
    assert catalog["default_research_only"] is True
    assert catalog["profit_guarantee"] is False
    catalog_algorithms = catalog["algorithms"]
    records = coverage["algorithms"]
    assert len(catalog_algorithms) == len(records) == 27
    assert [item["id"] for item in catalog_algorithms] == [
        record["catalog_id"] for record in records
    ]
    assert len({record["catalog_id"] for record in records}) == len(records)

    for catalog_item, record in zip(catalog_algorithms, records, strict=True):
        assert set(record) == REQUIRED_RECORD_KEYS, record["catalog_id"]
        assert record["status"] in ALLOWED_STATUSES

        implemented = record["implemented_emits"]
        missing = record["missing_emits"]
        expected_emits = catalog_item["emits"]
        assert len(implemented) == len(set(implemented))
        assert len(missing) == len(set(missing))
        assert set(implemented).isdisjoint(missing)
        assert set(implemented) | set(missing) == set(expected_emits), record["catalog_id"]
        assert len(implemented) + len(missing) == len(expected_emits)

        for key in ("implementation_refs", "test_refs", "chart_refs"):
            assert isinstance(record[key], list)
            assert all(isinstance(value, str) and value for value in record[key])
            _assert_refs_exist(record[key])

        status = record["status"]
        if status == "implemented":
            assert not missing
            assert record["implementation_refs"]
            assert record["test_refs"]
            assert record["chart_refs"]
            assert not record["limitations"]
            assert record["next_milestone"] is None
        elif status == "fixed_level_projection":
            assert not missing
            assert record["implementation_refs"]
            assert record["test_refs"]
            assert record["chart_refs"]
            assert record["limitations"]
            assert record["next_milestone"] is None
        elif status == "partial":
            assert implemented
            assert missing
            assert record["implementation_refs"]
            assert record["test_refs"]
            assert record["limitations"]
            assert record["next_milestone"]
        else:
            assert not implemented
            assert missing == expected_emits
            assert not record["implementation_refs"]
            assert not record["test_refs"]
            assert not record["chart_refs"]
            assert record["limitations"]
            assert record["next_milestone"]

    counts = Counter(record["status"] for record in records)
    assert coverage["summary"] == {
        "catalog_items": len(records),
        "implemented": counts["implemented"],
        "fixed_level_projection": counts["fixed_level_projection"],
        "partial": counts["partial"],
        "deferred": counts["deferred"],
        "catalog_complete": not (counts["partial"] or counts["deferred"]),
    }

    serialized = json.dumps(coverage, ensure_ascii=False).lower()
    assert "guaranteed_profit" not in serialized
    assert "guaranteed profit" not in serialized
    assert "保本" not in serialized
    assert "保收益" not in serialized
