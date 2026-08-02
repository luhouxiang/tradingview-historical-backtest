from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from tvbt.chan.engine import ChanEngine, ChanParameters, Fractal, LineObject, RawBar, _centers


def bar(index: int, high: int, low: int) -> RawBar:
    return RawBar(index, 1_700_000_000_000 + index * 60_000, high, low, (high + low) // 2)


def wave_bars(count: int = 25) -> list[RawBar]:
    levels = [0, 1, 2, 3, 4, 3, 2, 1]
    result = []
    for index in range(count):
        cycle = index % 8
        level = levels[cycle]
        result.append(bar(index, level * 10 + 5, level * 10))
    return result


def engine() -> ChanEngine:
    return ChanEngine(ChanParameters(checkpoint_interval=4))


def line(index: int, start_price: int, end_price: int) -> LineObject:
    direction = "up" if end_price > start_price else "down"
    start = Fractal(
        f"f-{index}",
        "bottom" if direction == "up" else "top",
        index,
        index,
        index * 60_000,
        start_price,
        index,
        index,
    )
    end = Fractal(
        f"f-{index + 1}",
        "top" if direction == "up" else "bottom",
        index + 1,
        index + 1,
        (index + 1) * 60_000,
        end_price,
        index + 1,
        index + 1,
    )
    return LineObject(f"bi-{index}", start, end, direction, index + 1, index + 1)


def test_reference_inclusion_merges_in_the_established_direction() -> None:
    runtime = engine()
    runtime.update(bar(0, 10, 0))
    runtime.update(bar(1, 12, 2))
    runtime.update(bar(2, 11, 3))
    assert len(runtime.included) == 2
    merged = runtime.included[-1]
    assert merged.direction == "up"
    assert (merged.high_i64, merged.low_i64) == (12, 3)
    assert merged.high_raw_index == 1
    assert merged.low_raw_index == 2
    assert merged.source_raw_indices == [1, 2]


def test_reference_fractal_is_sealed_by_the_right_independent_bar() -> None:
    runtime = engine()
    values = [0, 1, 2, 3, 4, 3, 2, 1]
    for index, value in enumerate(values[:5]):
        runtime.update(bar(index, value * 10 + 5, value * 10))
    assert runtime.fractals == []
    runtime.update(bar(5, values[5] * 10 + 5, values[5] * 10))
    assert len(runtime.fractals) == 1
    fractal = runtime.fractals[0]
    assert fractal.fractal_type == "top"
    assert fractal.bar_index == 4
    assert fractal.confirmed_at_bar_index == 5


def test_reference_extremes_build_alternating_bi_and_confirmed_center() -> None:
    runtime = engine()
    for item in wave_bars(40):
        runtime.update(item)
    rows = runtime.result_rows()
    confirmed_bi = [item for item in rows["bi"] if item["confirmed"]]
    assert len(confirmed_bi) >= 3
    assert [item["direction"] for item in confirmed_bi[:3]] == ["down", "up", "down"]
    assert all(item["end_bar_index"] - item["start_bar_index"] + 1 >= 5 for item in confirmed_bi)
    assert rows["zhongshu"]
    center = rows["zhongshu"][0]
    assert center["zd_i64"] < center["zg_i64"]
    assert center["status"] in {"confirmed", "extended", "left"}
    assert center["leave_direction"] in {None, "up", "down"}
    assert center["known_at_bar_index"] >= center["confirmed_at_bar_index"]


def test_kline_chart_reference_complex_endpoint_sequence_is_exact() -> None:
    # First 60 AOL9 bars form the same close-range and containment cases used for
    # the cross-repository golden comparison against kline-chart/c_bi.py.
    high_low = [
        (2716, 2702),
        (2715, 2711),
        (2712, 2709),
        (2711, 2706),
        (2713, 2708),
        (2713, 2711),
        (2713, 2710),
        (2712, 2709),
        (2713, 2711),
        (2712, 2710),
        (2714, 2711),
        (2713, 2711),
        (2713, 2710),
        (2712, 2709),
        (2712, 2709),
        (2710, 2708),
        (2711, 2709),
        (2711, 2709),
        (2712, 2710),
        (2712, 2710),
        (2712, 2710),
        (2712, 2710),
        (2711, 2710),
        (2711, 2709),
        (2710, 2709),
        (2710, 2709),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2710, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2711, 2710),
        (2728, 2708),
        (2725, 2719),
        (2723, 2721),
        (2723, 2713),
        (2719, 2714),
        (2719, 2717),
        (2722, 2717),
        (2730, 2721),
        (2725, 2723),
        (2725, 2723),
        (2725, 2721),
        (2726, 2721),
    ]
    runtime = engine()
    for index, (high, low) in enumerate(high_low):
        runtime.update(bar(index, high, low))
    rows = runtime.result_rows()["bi"]
    assert [
        (item["start_bar_index"], item["end_bar_index"], item["direction"], item["confirmed"])
        for item in rows
    ] == [
        (3, 10, "up", True),
        (10, 23, "down", True),
        (23, 55, "up", False),
    ]
    assert all(left["end_bar_index"] == right["start_bar_index"] for left, right in pairwise(rows))
    assert all(left["direction"] != right["direction"] for left, right in pairwise(rows))


def test_reference_center_revisions_extend_then_leave_up() -> None:
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        line(5, 8, 4),
        line(6, 4, 12),
        line(7, 12, 9),
    ]
    assert _centers(lines[:5]) == []
    _, confirmed, _ = _centers(lines[:6])[0]
    _, extended, _ = _centers(lines[:7])[0]
    _, left, known_at = _centers(lines)[0]
    assert confirmed["status"] == "confirmed"
    assert (confirmed["zd_i64"], confirmed["zg_i64"]) == (2, 8)
    assert extended["status"] == "extended"
    assert left["status"] == "left"
    assert left["leave_direction"] == "up"
    assert left["confirmed_at_bar_index"] == 6
    assert known_at == 8


def test_center_requires_fourth_participating_line_to_return_to_seed_rectangle() -> None:
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 12),
        line(5, 12, 11),
    ]
    assert _centers(lines) == []


def test_centers_do_not_share_participating_lines_or_time_ranges() -> None:
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        line(5, 8, 4),
        line(6, 4, 12),
        line(7, 12, 9),
        line(8, 9, 20),
        line(9, 20, 12),
        line(10, 12, 18),
        line(11, 18, 14),
    ]
    centers = [payload for _, payload, _ in _centers(lines)]
    assert len(centers) == 2
    assert centers[0]["end_bar_index"] < centers[1]["start_bar_index"]


def test_center_revision_known_at_never_precedes_formation() -> None:
    lines = [
        line(0, 15, 20),
        line(1, 20, 0),
        line(2, 0, 10),
        line(3, 10, 2),
        line(4, 2, 8),
        replace(line(5, 8, 4), confirmed_at_bar_index=20, known_at_bar_index=20),
        line(6, 4, 12),
        line(7, 12, 9),
    ]
    _, center, known_at = _centers(lines)[0]
    assert center["confirmed_at_bar_index"] == 20
    assert known_at == 20


def test_chan_event_stream_is_prefix_invariant_for_multiple_cutoffs() -> None:
    bars = wave_bars(30)
    full = engine()
    for item in bars:
        full.update(item)
    full_rows = [event.row() for event in full.emitter.events]
    for cutoff in (8, 12, 20, 27):
        prefix = engine()
        for item in bars[:cutoff]:
            prefix.update(item)
        expected = [row for row in full_rows if row["known_at_bar_index"] < cutoff]
        assert [event.row() for event in prefix.emitter.events] == expected


def test_engine_state_restore_matches_uninterrupted_events_and_objects() -> None:
    bars = wave_bars(30)
    full = engine()
    for item in bars:
        full.update(item)

    prefix = engine()
    for item in bars[:17]:
        prefix.update(item)
    prefix_events = [event.row() for event in prefix.emitter.events]
    restored = ChanEngine.from_state(prefix.export_state())
    for item in bars[17:]:
        restored.update(item)
    combined = [*prefix_events, *(event.row() for event in restored.emitter.events)]
    assert combined == [event.row() for event in full.emitter.events]
    assert restored.result_rows() == full.result_rows()
