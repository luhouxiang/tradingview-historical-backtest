from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "docs" / "chan-single-scope-profiles"


def _load(name: str) -> dict[str, object]:
    return json.loads((PROFILE_DIR / name).read_text(encoding="utf-8"))


def test_single_scope_profile_has_one_explicit_execution_profile() -> None:
    value = _load("profiles.json")
    profiles = value["profiles"]
    enabled = [item for item in profiles if item["status"] == "enabled"]
    assert [item["profile_id"] for item in enabled] == ["chan108_single_scope_v1"]
    assert enabled[0]["execution_eligible"] is True
    strict = next(item for item in profiles if item["profile_id"].startswith("chan108_lesson54"))
    assert strict["status"] == "research_only"
    assert strict["execution_eligible"] is False
    assert all(item["enabled"] is False for item in value["undefined_rules"])


def test_profile_fixes_boundaries_range_members_and_causal_order() -> None:
    profile = _load("profiles.json")["profiles"][0]
    assert profile["third_point"]["touch_is_third_point"] is True
    assert profile["third_point"]["first_completed_return_only"] is True
    assert profile["segment_range"]["member_boundary"].endswith("inclusive")
    assert profile["segment_range"]["adjacent_segment_members_excluded"] is True
    assert profile["causal_time"]["ordering"] == (
        "endpoint_bar_index <= confirmed_at_bar_index <= known_at_bar_index"
    )


def test_counterexample_catalog_covers_required_semantic_edges() -> None:
    cases = _load("counterexamples.json")["cases"]
    case_ids = {item["case_id"] for item in cases}
    assert {
        "third_buy_touch",
        "third_sell_touch",
        "unfinished_return",
        "lost_first_return_eligibility",
        "segment_internal_extreme",
        "causal_time_separation",
    } <= case_ids
    range_case = next(item for item in cases if item["case_id"] == "segment_internal_extreme")
    assert range_case["expected"]["range_high_i64"] > max(
        range_case["input"]["start_price_i64"], range_case["input"]["end_price_i64"]
    )
