from __future__ import annotations

import threading

import pytest

from tvbt.api.jobs import Job, JobStore
from tvbt.storage import memory_guard


def test_compute_queue_is_serial_and_cancelled_waiter_never_runs() -> None:
    store = JobStore()
    entered = threading.Event()
    release = threading.Event()
    second_ran = threading.Event()
    for key in ("one", "two", "three"):
        store.submit(Job(key, "calculation", "request", "trace"))

    def first(*_: object) -> str:
        entered.set()
        assert release.wait(5)
        return "first"

    one = threading.Thread(target=store.run, args=("one", first))
    two = threading.Thread(target=store.run, args=("two", lambda *_: second_ran.set()))
    one.start()
    assert entered.wait(5)
    two.start()
    assert store.get("two").status == "queued"
    store.cancel("two")
    two.join(2)
    assert not two.is_alive()
    assert store.get("two").status == "cancelled"
    assert not second_ran.is_set()
    release.set()
    one.join(2)
    store.run("three", lambda *_: "third")
    assert store.get("three").status == "completed"


@pytest.mark.parametrize("used,available", [(2048, 4096), (128, 511)])
def test_memory_guard_stops_before_exhaustion(
    monkeypatch: pytest.MonkeyPatch, used: int, available: int
) -> None:
    monkeypatch.delenv("TVBT_CHAN_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.setattr(
        memory_guard,
        "memory_usage",
        lambda: (used * memory_guard.MIB, available * memory_guard.MIB),
    )
    with pytest.raises(MemoryError, match="内存保护"):
        memory_guard.check_memory()


def test_memory_guard_allows_work_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TVBT_CHAN_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr(
        memory_guard, "memory_usage", lambda: (200 * memory_guard.MIB, 1024 * memory_guard.MIB)
    )
    memory_guard.check_memory()
