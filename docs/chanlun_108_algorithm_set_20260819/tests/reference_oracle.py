#!/usr/bin/env python3
"""Small dependency-free oracle for the synthetic Chanlun fixtures.

This is a contract checker, not a production trading implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]


def merge_inclusion(payload: Dict[str, Any]) -> Dict[str, int]:
    a, b = payload["a"], payload["b"]
    if payload["direction"] == "up":
        return {"high": max(a["high"], b["high"]), "low": max(a["low"], b["low"])}
    if payload["direction"] == "down":
        return {"high": min(a["high"], b["high"]), "low": min(a["low"], b["low"])}
    raise ValueError("direction must be up or down")


def detect_fractal(payload: Dict[str, Any]) -> Dict[str, Any]:
    left, mid, right = payload["bars"]
    is_top = (
        mid["high"] > left["high"]
        and mid["high"] > right["high"]
        and mid["low"] > left["low"]
        and mid["low"] > right["low"]
    )
    is_bottom = (
        mid["high"] < left["high"]
        and mid["high"] < right["high"]
        and mid["low"] < left["low"]
        and mid["low"] < right["low"]
    )
    if is_top:
        return {"type": "top", "extreme": mid["high"]}
    if is_bottom:
        return {"type": "bottom", "extreme": mid["low"]}
    return {"type": None, "extreme": None}


def validate_bi(payload: Dict[str, Any]) -> Dict[str, Any]:
    start, end = payload["start"], payload["end"]
    start_members = set(start["member_indices"])
    end_members = set(end["member_indices"])
    if start_members & end_members:
        return {"valid": False, "direction": None, "reason": "endpoint_fractals_share_processed_bar"}

    all_indices = set(range(payload["processed_bar_count"]))
    independent = all_indices - start_members - end_members
    if not independent:
        return {"valid": False, "direction": None, "reason": "no_independent_processed_bar"}

    if start["type"] == end["type"]:
        return {"valid": False, "direction": None, "reason": "same_type_endpoints"}

    direction = "up" if start["type"] == "bottom" and end["type"] == "top" else "down"
    if payload["declared_endpoint_price"] != end["extreme"]:
        return {
            "valid": False,
            "direction": None,
            "reason": "bi_endpoint_must_equal_selected_fractal_extreme",
        }
    return {"valid": True, "direction": direction, "reason": None}


def classify_segment_base(payload: Dict[str, Any]) -> Dict[str, Any]:
    bi = payload["bi"]
    if len(bi) < 3 or len(bi) % 2 == 0:
        return {"valid_candidate": False, "overlap": None}
    first = bi[:3]
    low = max(item["low"] for item in first)
    high = min(item["high"] for item in first)
    if low > high:
        return {"valid_candidate": False, "overlap": None}
    return {"valid_candidate": True, "overlap": {"low": low, "high": high}}


def build_center(payload: Dict[str, Any]) -> Dict[str, Any]:
    components = payload["components"]
    if len(components) < 3:
        return {"accepted": False, "ZD": None, "ZG": None, "point_center": False, "reason": "insufficient_components"}
    if any(c["type"] == "bi" for c in components):
        return {"accepted": False, "ZD": None, "ZG": None, "point_center": False, "reason": "bi_cannot_form_standard_center"}
    if any(c.get("status") != "confirmed" for c in components):
        return {"accepted": False, "ZD": None, "ZG": None, "point_center": False, "reason": "unfinished_component"}
    if len({c["level"] for c in components}) != 1:
        return {"accepted": False, "ZD": None, "ZG": None, "point_center": False, "reason": "mixed_levels"}
    if len({c["type"] for c in components}) != 1:
        return {"accepted": False, "ZD": None, "ZG": None, "point_center": False, "reason": "mixed_component_types"}
    first = components[:3]
    zd = max(c["low"] for c in first)
    zg = min(c["high"] for c in first)
    if zd > zg:
        return {"accepted": False, "ZD": zd, "ZG": zg, "point_center": False, "reason": "no_common_overlap"}
    return {"accepted": True, "ZD": zd, "ZG": zg, "point_center": zd == zg, "reason": None}


def classify_movement(payload: Dict[str, Any]) -> Dict[str, str]:
    centers = payload["centers"]
    if len(centers) == 1:
        return {"class": "consolidation"}

    relations: List[str] = []
    for previous, current in zip(centers, centers[1:]):
        if current["DD"] > previous["GG"]:
            relations.append("up")
        elif current["GG"] < previous["DD"]:
            relations.append("down")
        else:
            relations.append("overlap")
    if all(r == "up" for r in relations):
        return {"class": "uptrend"}
    if all(r == "down" for r in relations):
        return {"class": "downtrend"}
    return {"class": "higher_level_center_candidate"}


def classify_third_point(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    if not payload["departure_completed"]:
        return {"signal": None, "reason": "departure_unfinished"}
    if not payload["return_completed"]:
        return {"signal": None, "reason": "return_unfinished"}
    if not payload["first_return"]:
        return {"signal": None, "reason": "not_first_return"}
    center = payload["center"]
    if payload["side"] == "buy":
        if payload["return_low"] >= center["ZG"]:
            return {"signal": "B3", "reason": None}
        return {"signal": None, "reason": "return_enters_center"}
    if payload["return_high"] <= center["ZD"]:
        return {"signal": "S3", "reason": None}
    return {"signal": None, "reason": "return_enters_center"}


def detect_trend_divergence(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload["same_level_center_count"] < 2:
        return {"divergence": False, "side": None, "reason": "fewer_than_two_centers"}
    if not payload["centers_nonoverlap"]:
        return {"divergence": False, "side": None, "reason": "centers_overlap"}
    if not payload["final_leg_new_extreme"]:
        return {"divergence": False, "side": None, "reason": "no_new_extreme"}
    if not payload["final_leg_completed"]:
        return {"divergence": False, "side": None, "reason": "final_leg_unfinished"}
    if payload["force_c"] >= payload["force_a"]:
        return {"divergence": False, "side": None, "reason": "force_not_weaker"}
    side = "bottom" if payload["direction"] == "down" else "top"
    return {"divergence": True, "side": side, "reason": None}


def validate_event_time(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not (payload["endpoint_time"] <= payload["confirmed_at"] <= payload["available_at"]):
        return {"valid": False, "reason": "invalid_object_time_order"}
    if payload["execution_time"] < payload["available_at"]:
        return {"valid": False, "reason": "execution_before_available"}
    return {"valid": True, "reason": None}


def monitor_zn(payload: Dict[str, Any]) -> Dict[str, Any]:
    a, b = payload["center"]["A"], payload["center"]["B"]
    z = (a + b) / 2
    zn = (payload["oscillation"]["low"] + payload["oscillation"]["high"]) / 2
    bias = "strong" if zn > z else "weak" if zn < z else "neutral"
    cross = "B" if zn > b else "A" if zn < a else None
    return {"Z": z, "Zn": zn, "bias": bias, "boundary_cross": cross}


EVALUATORS = {
    "inclusion_up": merge_inclusion,
    "inclusion_down": merge_inclusion,
    "fractal": detect_fractal,
    "bi_validation": validate_bi,
    "segment_base": classify_segment_base,
    "center_build": build_center,
    "movement_class": classify_movement,
    "third_point": classify_third_point,
    "trend_divergence": detect_trend_divergence,
    "event_time": validate_event_time,
    "Zn_monitor": monitor_zn,
}


def evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    evaluator = EVALUATORS[case["category"]]
    return evaluator(case["input"])


def load_cases() -> Iterable[Dict[str, Any]]:
    with (ROOT / "fixtures" / "structural_cases.json").open(encoding="utf-8") as handle:
        return json.load(handle)["cases"]


def main() -> int:
    failures = []
    cases = list(load_cases())
    for case in cases:
        actual = evaluate_case(case)
        if actual != case["expected"]:
            failures.append({"id": case["id"], "expected": case["expected"], "actual": actual})
    if failures:
        print(json.dumps({"status": "failed", "failures": failures}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "passed", "case_count": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
