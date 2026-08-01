"""Fixed-timeframe structure vector derived only from confirmed snapshots."""

from __future__ import annotations

from trading_research.contracts import (
    CenterStatus, Direction, LevelStructureState, MultiLevelStructureState,
    StructureSnapshot, SwingLabel,
)


def build_level_state(snapshot: StructureSnapshot) -> LevelStructureState:
    last_high = next((item.label for item in reversed(snapshot.swings) if "HIGH" in item.label or item.label in {SwingLabel.HH, SwingLabel.LH}), None)
    last_low = next((item.label for item in reversed(snapshot.swings) if "LOW" in item.label or item.label in {SwingLabel.HL, SwingLabel.LL}), None)
    seeds = [item for item in snapshot.centers if item.status is CenterStatus.CONFIRMED]
    migration = Direction.UNKNOWN
    if len(seeds) >= 2:
        previous, current = seeds[-2], seeds[-1]
        if current.high < previous.low:
            migration = Direction.DOWN
        elif current.low > previous.high:
            migration = Direction.UP
    direction = migration
    if last_high is SwingLabel.LH and last_low is SwingLabel.LL:
        direction = Direction.DOWN
    elif last_high is SwingLabel.HH and last_low is SwingLabel.HL:
        direction = Direction.UP
    available = max(
        [item.available_from for item in snapshot.fractals]
        + [item.available_from for item in snapshot.centers]
        + [item.confirm_time for item in snapshot.included_bars],
        default=None,
    )
    if available is None:
        raise ValueError("cannot build a level state from an empty snapshot")
    return LevelStructureState(
        snapshot.timeframe, available, direction, last_high, last_low, migration, len(snapshot.strokes)
    )


def build_multilevel_state(snapshots: tuple[StructureSnapshot, ...]) -> MultiLevelStructureState:
    if not snapshots:
        raise ValueError("at least one structure snapshot is required")
    levels = tuple(sorted((build_level_state(item) for item in snapshots), key=lambda item: item.timeframe.seconds, reverse=True))
    available_from = max(item.as_of for item in levels)
    return MultiLevelStructureState(levels, tuple(int(item.direction) for item in levels), available_from)

