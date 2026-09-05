from __future__ import annotations

from dataclasses import dataclass

from tvbt.algorithms import definitions as algorithm_definitions
from tvbt.auxiliary.price_gap import classify_price_gaps, definition


@dataclass(frozen=True)
class Bar:
    bar_index: int
    timestamp_utc: int
    high_i64: int
    low_i64: int
    close_i64: int


def bar(index: int, low: int, high: int) -> Bar:
    return Bar(index, index * 60_000, high, low, (low + high) // 2)


def test_definition_is_discoverable_and_non_trading() -> None:
    value = definition()
    assert value["algorithm_id"] == "aux_price_gap_lifecycle"
    assert any(item["algorithm_id"] == value["algorithm_id"] for item in algorithm_definitions())
    assert all(output["name"].startswith("aux_") for output in value["outputs"])


def test_up_gap_forms_partially_fills_and_completely_fills_causally() -> None:
    events = classify_price_gaps(
        [bar(0, 95, 100), bar(1, 105, 110), bar(2, 104, 108), bar(3, 100, 106)]
    )
    assert [event.event_type for event in events] == [
        "aux_price_gap_formed",
        "aux_price_gap_partially_filled",
        "aux_price_gap_filled",
    ]
    assert [(event.lower_i64, event.upper_i64) for event in events] == [(100, 105)] * 3
    assert [event.fill_extreme_i64 for event in events] == [105, 104, 100]
    assert [event.known_at_bar_index for event in events] == [1, 2, 3]
    assert all(event.details()["execution_allowed"] is False for event in events)


def test_touch_is_not_a_gap_down_gap_is_mirrored_and_missing_input_becomes_unknown() -> None:
    assert classify_price_gaps([bar(0, 95, 100), bar(1, 100, 105)]) == []
    down = classify_price_gaps([bar(0, 100, 105), bar(1, 90, 95), bar(2, 92, 96), bar(4, 93, 97)])
    assert [event.event_type for event in down] == [
        "aux_price_gap_formed",
        "aux_price_gap_partially_filled",
        "aux_price_gap_unknown",
    ]
    assert down[-1].reason_code == "AUX_PRICE_GAP_INPUT_DISCONTINUITY"


def test_prefixes_never_publish_future_gap_lifecycle_events() -> None:
    values = [bar(0, 95, 100), bar(1, 105, 110), bar(2, 104, 108), bar(3, 100, 106)]
    complete = classify_price_gaps(values)
    for length in range(1, len(values) + 1):
        assert classify_price_gaps(values[:length]) == [
            event for event in complete if event.known_at_bar_index < length
        ]
