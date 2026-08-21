from __future__ import annotations

import pytest

from tvbt.indicators import definitions, resolve


def compute(algorithm_id: str, columns: dict[str, list[float]], **parameters: object):
    resolved = resolve(algorithm_id)
    assert resolved is not None
    return resolved[1](columns, parameters)


def test_definitions_are_immutable_and_complete() -> None:
    available = {definition["algorithm_id"]: definition for definition in definitions()}
    assert set(available) == {"ma", "macd", "atr", "boll"}
    for definition in available.values():
        assert definition["source_hash"].startswith("sha256:")
        assert definition["causal"] is True
        assert definition["input_schema"] == "bars.v1"
    assert available["macd"]["algorithm_version"] == "1.1.0"


def test_ma_golden_values_and_warmup() -> None:
    result = compute("ma", {"close": [1, 2, 3, 4, 5]}, period=3, source="close")
    assert result["ma"] == [None, None, 2.0, 3.0, 4.0]


def test_macd_golden_values() -> None:
    values = [float(value) for value in range(1, 16)]
    result = compute(
        "macd",
        {"close": values},
        fast_period=3,
        slow_period=5,
        signal_period=2,
        source="close",
    )
    assert result["macd"][:5] == [None] * 5
    assert result["macd"][5] == pytest.approx(0.7678755144)
    assert result["signal"][5] == pytest.approx(0.70996227709)
    assert result["histogram"][5] == pytest.approx(0.11582647462)


def test_atr_wilder_golden_values() -> None:
    columns = {
        "high": [10, 12, 13, 15, 14],
        "low": [8, 9, 11, 12, 11],
        "close": [9, 11, 12, 13, 12],
    }
    result = compute("atr", columns, period=3)["atr"]
    assert result[:2] == [None, None]
    assert result[2:] == pytest.approx([7 / 3, 23 / 9, 73 / 27])


def test_boll_population_standard_deviation_golden_values() -> None:
    result = compute(
        "boll",
        {"close": [1, 2, 3, 4, 5]},
        period=3,
        standard_deviations=2.0,
        source="close",
    )
    assert result["middle"] == [None, None, 2.0, 3.0, 4.0]
    deviation = 2.0 * (2.0 / 3.0) ** 0.5
    assert result["upper"][2:] == pytest.approx([2 + deviation, 3 + deviation, 4 + deviation])
    assert result["lower"][2:] == pytest.approx([2 - deviation, 3 - deviation, 4 - deviation])


@pytest.mark.parametrize(
    ("algorithm_id", "parameters"),
    [
        ("ma", {"period": 3, "source": "close"}),
        (
            "macd",
            {"fast_period": 3, "slow_period": 5, "signal_period": 2, "source": "close"},
        ),
        ("atr", {"period": 3}),
        (
            "boll",
            {"period": 3, "standard_deviations": 2.0, "source": "close"},
        ),
    ],
)
def test_prefix_invariance(algorithm_id: str, parameters: dict[str, object]) -> None:
    columns = {
        "open": [float(value) for value in range(1, 31)],
        "high": [float(value + 2) for value in range(1, 31)],
        "low": [float(value - 1) for value in range(1, 31)],
        "close": [float(value + value % 3) for value in range(1, 31)],
    }
    prefix = {name: values[:20] for name, values in columns.items()}
    short = compute(algorithm_id, prefix, **parameters)
    full = compute(algorithm_id, columns, **parameters)
    assert {name: values[:20] for name, values in full.items()} == short
