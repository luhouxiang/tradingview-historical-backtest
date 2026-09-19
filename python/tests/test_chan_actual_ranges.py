from __future__ import annotations

from tvbt.chan.engine import Fractal, LineObject
from tvbt.chan.zn import classify_zn_components


def line(
    index: int,
    start_price: int,
    end_price: int,
    *,
    range_low: int | None = None,
    range_high: int | None = None,
) -> LineObject:
    direction = "up" if end_price > start_price else "down"
    start = Fractal(
        f"f-{index}-start",
        "bottom" if direction == "up" else "top",
        index,
        index * 10,
        index * 60_000,
        start_price,
        index * 10,
        index * 10,
    )
    end = Fractal(
        f"f-{index}-end",
        "top" if direction == "up" else "bottom",
        index + 1,
        index * 10 + 5,
        index * 60_000 + 30_000,
        end_price,
        index * 10 + 5,
        index * 10 + 5,
    )
    return LineObject(
        f"segment-{index}",
        start,
        end,
        direction,
        index * 10 + 5,
        index * 10 + 5,
        range_low,
        range_high,
        index * 10 if range_low is not None else None,
        index * 10 + 3 if range_high is not None else None,
        "constituent_bi_union_v1" if range_low is not None else "endpoint_extrema_v1",
    )


def test_zn_uses_actual_range_midpoint() -> None:
    values = [
        line(0, 120, 100, range_low=100, range_high=140),
        line(1, 130, 110, range_low=105, range_high=130),
        line(2, 105, 125, range_low=105, range_high=125),
    ]
    observations = classify_zn_components(
        core_low_i64=110,
        core_high_i64=125,
        components=values,
    )
    assert observations[0].range_high_i64 == 140
    assert observations[0].zn_twice_i64 == 240


def test_line_actual_range_must_contain_structural_endpoints() -> None:
    try:
        line(0, 100, 120, range_low=105, range_high=120)
    except ValueError as error:
        assert "contain both structural endpoints" in str(error)
    else:
        raise AssertionError("invalid actual range was accepted")
