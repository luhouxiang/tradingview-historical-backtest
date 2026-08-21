from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

Series = list[float | None]
Compute = Callable[[dict[str, list[float]], dict[str, Any]], dict[str, Series]]


def _source_hash() -> str:
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _definition(
    algorithm_id: str,
    name: str,
    parameter_schema: dict[str, Any],
    outputs: list[dict[str, str]],
    warmup_expression: str,
    compute: Compute,
    algorithm_version: str = "1.0.0",
) -> tuple[dict[str, Any], Compute]:
    return (
        {
            "kind": "indicator",
            "algorithm_id": algorithm_id,
            "algorithm_version": algorithm_version,
            "source_hash": _source_hash(),
            "name": name,
            "input_schema": "bars.v1",
            "parameter_schema": parameter_schema,
            "outputs": outputs,
            "warmup": {"kind": "formula", "expression": warmup_expression},
            "causal": True,
        },
        compute,
    )


def _sma(values: list[float], parameters: dict[str, Any]) -> dict[str, Series]:
    period = int(parameters["period"])
    result: Series = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            result[index] = running / period
    return {"ma": result}


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def _macd(values: list[float], parameters: dict[str, Any]) -> dict[str, Series]:
    fast = int(parameters["fast_period"])
    slow = int(parameters["slow_period"])
    signal_period = int(parameters["signal_period"])
    fast_values = _ema(values, fast)
    slow_values = _ema(values, slow)
    macd_values = [left - right for left, right in zip(fast_values, slow_values, strict=True)]
    signal_values = _ema(macd_values, signal_period)
    histogram = [
        2.0 * (left - right) for left, right in zip(macd_values, signal_values, strict=True)
    ]
    warmup = slow + signal_period - 2

    def masked(series: list[float]) -> Series:
        return [None if index < warmup else value for index, value in enumerate(series)]

    return {
        "macd": masked(macd_values),
        "signal": masked(signal_values),
        "histogram": masked(histogram),
    }


def _atr(columns: dict[str, list[float]], parameters: dict[str, Any]) -> dict[str, Series]:
    period = int(parameters["period"])
    high, low, close = columns["high"], columns["low"], columns["close"]
    tr: list[float] = []
    for index in range(len(close)):
        if index == 0:
            tr.append(high[index] - low[index])
        else:
            tr.append(
                max(
                    high[index] - low[index],
                    abs(high[index] - close[index - 1]),
                    abs(low[index] - close[index - 1]),
                )
            )
    result: Series = [None] * len(close)
    if len(tr) >= period:
        current = sum(tr[:period]) / period
        result[period - 1] = current
        for index in range(period, len(tr)):
            current = ((period - 1) * current + tr[index]) / period
            result[index] = current
    return {"atr": result}


def _boll(values: list[float], parameters: dict[str, Any]) -> dict[str, Series]:
    period = int(parameters["period"])
    standard_deviations = float(parameters["standard_deviations"])
    middle: Series = [None] * len(values)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    running = 0.0
    running_squares = 0.0
    for index, value in enumerate(values):
        running += value
        running_squares += value * value
        if index >= period:
            expired = values[index - period]
            running -= expired
            running_squares -= expired * expired
        if index < period - 1:
            continue
        mean = running / period
        variance = max(0.0, running_squares / period - mean * mean)
        deviation = math.sqrt(variance) * standard_deviations
        middle[index] = mean
        upper[index] = mean + deviation
        lower[index] = mean - deviation
    return {"middle": middle, "upper": upper, "lower": lower}


_SOURCE_PARAMETER = {
    "type": "string",
    "enum": ["open", "high", "low", "close"],
    "default": "close",
}

_REGISTRY: dict[str, tuple[dict[str, Any], Compute]] = {
    "ma": _definition(
        "ma",
        "Moving Average",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "period": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 20},
                "source": _SOURCE_PARAMETER,
            },
            "required": ["period", "source"],
        },
        [{"name": "ma", "display_name": "MA", "pane": "main", "series_type": "line"}],
        "period - 1",
        lambda columns, parameters: _sma(columns[str(parameters["source"])], parameters),
    ),
    "macd": _definition(
        "macd",
        "MACD",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fast_period": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 12},
                "slow_period": {"type": "integer", "minimum": 2, "maximum": 10000, "default": 26},
                "signal_period": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 9},
                "source": _SOURCE_PARAMETER,
            },
            "required": ["fast_period", "slow_period", "signal_period", "source"],
        },
        [
            {"name": "macd", "display_name": "MACD", "pane": "indicator", "series_type": "line"},
            {
                "name": "signal",
                "display_name": "Signal",
                "pane": "indicator",
                "series_type": "line",
            },
            {
                "name": "histogram",
                "display_name": "Histogram",
                "pane": "indicator",
                "series_type": "histogram",
            },
        ],
        "slow_period + signal_period - 2",
        lambda columns, parameters: _macd(columns[str(parameters["source"])], parameters),
        algorithm_version="1.1.0",
    ),
    "atr": _definition(
        "atr",
        "Average True Range",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "period": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 14}
            },
            "required": ["period"],
        },
        [{"name": "atr", "display_name": "ATR", "pane": "indicator", "series_type": "line"}],
        "period - 1",
        _atr,
    ),
    "boll": _definition(
        "boll",
        "Bollinger Bands",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "period": {"type": "integer", "minimum": 2, "maximum": 10000, "default": 20},
                "standard_deviations": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 100,
                    "default": 2.0,
                },
                "source": _SOURCE_PARAMETER,
            },
            "required": ["period", "standard_deviations", "source"],
        },
        [
            {"name": "upper", "display_name": "BOLL Upper", "pane": "main", "series_type": "line"},
            {
                "name": "middle",
                "display_name": "BOLL Middle",
                "pane": "main",
                "series_type": "line",
            },
            {"name": "lower", "display_name": "BOLL Lower", "pane": "main", "series_type": "line"},
        ],
        "period - 1",
        lambda columns, parameters: _boll(columns[str(parameters["source"])], parameters),
    ),
}


def definitions() -> list[dict[str, Any]]:
    return [entry[0] for entry in _REGISTRY.values()]


def resolve(algorithm_id: str) -> tuple[dict[str, Any], Compute] | None:
    return _REGISTRY.get(algorithm_id)
