from __future__ import annotations

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
    return ChanEngine(ChanParameters(min_stroke_atr=0, checkpoint_interval=4))


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


def test_reference_fractal_waits_for_an_extra_independent_sealing_bar() -> None:
    runtime = engine()
    values = [0, 1, 2, 3, 4, 3, 2, 1]
    for index, value in enumerate(values[:7]):
        runtime.update(bar(index, value * 10 + 5, value * 10))
    assert runtime.fractals == []
    runtime.update(bar(7, values[7] * 10 + 5, values[7] * 10))
    assert len(runtime.fractals) == 1
    fractal = runtime.fractals[0]
    assert fractal.fractal_type == "top"
    assert fractal.bar_index == 4
    assert fractal.confirmed_at_bar_index == 7


def test_reference_extremes_build_alternating_bi_and_three_bi_center() -> None:
    runtime = engine()
    for item in wave_bars(25):
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


def test_reference_center_revisions_extend_then_leave_up() -> None:
    lines = [
        line(0, 0, 10),
        line(1, 10, 2),
        line(2, 2, 8),
        line(3, 8, 4),
        line(4, 4, 12),
        line(5, 12, 9),
    ]
    _, confirmed, _ = _centers(lines[:3])[0]
    _, extended, _ = _centers(lines[:5])[0]
    _, left, known_at = _centers(lines)[0]
    assert confirmed["status"] == "confirmed"
    assert extended["status"] == "extended"
    assert left["status"] == "left"
    assert left["leave_direction"] == "up"
    assert left["confirmed_at_bar_index"] == known_at == 6


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
