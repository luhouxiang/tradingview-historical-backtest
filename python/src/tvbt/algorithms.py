from __future__ import annotations

from typing import Any

from tvbt.chan.algorithm import definition as chan_definition
from tvbt.indicators import definitions as indicator_definitions
from tvbt.strategy import definitions as strategy_definitions


def definitions() -> list[dict[str, Any]]:
    return [*indicator_definitions(), chan_definition(), *strategy_definitions()]
