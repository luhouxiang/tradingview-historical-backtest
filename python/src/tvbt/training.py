"""Paused, process-local sessions using the one authoritative execution driver.

No worker waits for a UI timer: each admitted job advances a bounded command,
publishes an immutable prefix, and releases its compute slot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tvbt.backtest import ExecutionStep, backtest_steps
from tvbt.storage.path_guard import PathGuard
from tvbt.strategy import validate_strategy_payload

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TRANSPORT = {"request_id", "trace_id", "job_id", "output_path", "training"}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _facts(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _TRANSPORT}


def _integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"invalid {name}")
    if maximum is not None and value > maximum:
        raise ValueError(f"invalid {name}")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass
class _Session:
    payload: dict[str, Any]
    facts_hash: str
    driver: Generator[ExecutionStep, None, str]
    stop: threading.Event
    frames: list[ExecutionStep] = field(default_factory=list)
    cursor: int = -1
    state: str = "live"
    completed_run_ref: str | None = None
    # Cache BEFORE writing: a failed publication retry must not repeat fills.
    commands: dict[str, tuple[str, str, str, dict[str, Any] | None]] = field(default_factory=dict)
    latest_operation: str | None = None


class TrainingStore:
    def __init__(self, guard: PathGuard) -> None:
        self.guard = guard
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def command(self, payload: dict[str, Any], cancelled: threading.Event) -> str:
        control = payload.get("training")
        if not isinstance(control, dict):
            raise ValueError("training command is missing")
        sid, operation = control.get("session_id"), control.get("operation_id")
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise ValueError("invalid training session ID")
        if not isinstance(operation, str) or not _ID.fullmatch(operation):
            raise ValueError("invalid training operation ID")
        action = control.get("action")
        if action not in {"create", "advance", "seek", "cancel"}:
            raise ValueError("invalid training action")
        if set(control) - {
            "session_id",
            "operation_id",
            "action",
            "expected_cursor",
            "count",
            "target_bar_index",
        }:
            raise ValueError("unknown training control field")
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise ValueError("invalid command job ID")
        output_ref = f"runs/training/{sid}/commands/{job_id}"
        if payload.get("output_path") != output_ref:
            raise ValueError("training output must be the designated command directory")
        fingerprint = _digest({"facts": _facts(payload), "command": control})
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None:
                cached = session.commands.get(operation)
                if cached is not None:
                    previous, reference, snapshot_hash, snapshot = cached
                    if previous != fingerprint:
                        raise ValueError("training operation ID reused with different facts")
                    if snapshot is None:
                        snapshot = json.loads(
                            self.guard.resolve(reference + "/snapshot.json").read_text(
                                encoding="utf-8"
                            )
                        )
                    if _digest(snapshot) != snapshot_hash:
                        raise ValueError("immutable training command snapshot was changed")
                    result = self._publish(
                        sid,
                        reference,
                        snapshot,
                        update_latest=session.latest_operation == operation,
                    )
                    session.commands[operation] = (previous, reference, snapshot_hash, None)
                    return result
                if session.facts_hash != _digest(_facts(payload)):
                    raise ValueError("training session facts changed; create a new session")
            if cancelled.is_set():
                raise InterruptedError("training command cancelled before execution")
            if action == "create":
                if session is not None:
                    raise ValueError("training session already exists")
                session = self._create(payload, sid, cancelled)
                self._sessions[sid] = session
                reset = True
                begin = -1
            else:
                if session is None:
                    raise ValueError(
                        "training session is unavailable; engine restart requires a new session"
                    )
                expected = _integer(control.get("expected_cursor"), "expected_cursor", 0)
                if expected != session.cursor:
                    raise ValueError("training cursor conflict; refresh current session")
                begin = session.cursor
                reset = action in {"seek", "cancel"}
                if action == "cancel":
                    if session.state not in {"cancelled", "completed"}:
                        session.stop.set()
                        session.driver.close()
                        session.state = "cancelled"
                elif action == "seek":
                    target = _integer(control.get("target_bar_index"), "target_bar_index", 0)
                    start = session.frames[0].bar_index
                    head = session.frames[-1].bar_index
                    if not start <= target <= head:
                        raise ValueError("seek only permits viewing the processed training prefix")
                    session.cursor = target
                elif action == "advance":
                    count = _integer(control.get("count"), "count", 1, 100)
                    if session.state in {"cancelled", "interrupted"}:
                        raise ValueError("training session is not executable")
                    try:
                        self._advance(session, count, cancelled)
                    except Exception:
                        # Publish bars that actually finished before the failure;
                        # never drop an already executed fill or call it a full run.
                        interrupted = self._snapshot(sid, operation, session, begin, reset)
                        interrupted_hash = _digest(interrupted)
                        session.commands[operation] = (
                            fingerprint,
                            output_ref,
                            interrupted_hash,
                            interrupted,
                        )
                        session.latest_operation = operation
                        self._publish(sid, output_ref, interrupted, update_latest=True)
                        session.commands[operation] = (
                            fingerprint,
                            output_ref,
                            interrupted_hash,
                            None,
                        )
                        raise
            assert session is not None
            snapshot = self._snapshot(sid, operation, session, begin, reset)
            snapshot_hash = _digest(snapshot)
            session.commands[operation] = (fingerprint, output_ref, snapshot_hash, snapshot)
            session.latest_operation = operation
            result = self._publish(sid, output_ref, snapshot, update_latest=True)
            # The command's immutable file is the durable idempotency evidence.
            # Retain a decoded snapshot only while publication is incomplete,
            # not another full-history collection of parsed event payloads.
            session.commands[operation] = (fingerprint, output_ref, snapshot_hash, None)
            return result

    def _create(self, payload: dict[str, Any], sid: str, cancelled: threading.Event) -> _Session:
        if (
            self.guard.resolve(f"runs/training/{sid}").exists()
            or self.guard.resolve(f"runs/{sid}").exists()
        ):
            raise ValueError(
                "training session ID already has persisted facts; create a new session"
            )
        validate_strategy_payload(payload)
        if payload["algorithm"]["algorithm_id"] != "divergence_segment_end_simulation":
            raise ValueError("online training requires the segment-end simulation strategy")
        if payload.get("execution", {}).get("fill_timing") != "next_bar_open":
            raise ValueError("online training requires explicit next_bar_open execution")
        if payload.get("run_id") != sid:
            raise ValueError("training run ID must match session ID")
        execution_payload = copy.deepcopy(payload)
        execution_payload.pop("training", None)
        execution_payload["output_path"] = f"runs/{sid}"
        stop = threading.Event()
        # Warmup cancellation is checked by the existing execution driver. After
        # the first frame the command's event is detached so cancelling a later
        # batch cannot close the generator or erase its known history.
        initial_stop = _InitialCancellation(stop, cancelled)
        driver = backtest_steps(execution_payload, self.guard, initial_stop)
        session = _Session(execution_payload, _digest(_facts(payload)), driver, stop)
        try:
            session.frames.append(next(driver))
            session.cursor = session.frames[-1].bar_index
            initial_stop.initial = None
            self._finish_if_terminal(session)
        except BaseException:
            driver.close()
            raise
        return session

    def _advance(self, session: _Session, count: int, cancelled: threading.Event) -> None:
        for _ in range(count):
            if cancelled.is_set():
                break  # Already processed frames remain publishable and resumable.
            head = session.frames[-1].bar_index
            if session.cursor < head:
                session.cursor += 1  # Record viewing, never a second execution.
                continue
            if session.state == "completed":
                break
            try:
                frame = next(session.driver)
                if frame.bar_index != head + 1:
                    raise ValueError("non-contiguous training execution cursor")
                session.frames.append(frame)
                session.cursor = frame.bar_index
                self._finish_if_terminal(session)
            except BaseException:
                session.state = "interrupted"
                session.driver.close()
                raise

    def _finish_if_terminal(self, session: _Session) -> None:
        if not session.frames[-1].terminal_bar:
            return
        try:
            next(session.driver)
        except StopIteration as finished:
            reference = str(finished.value)
            if not (self.guard.resolve(reference) / "_SUCCESS").is_file():
                raise ValueError("training terminal run did not commit successfully") from finished
            session.completed_run_ref = reference
            session.state = "completed"
        else:
            raise ValueError("training execution continued beyond its terminal bar")

    def _snapshot(
        self, sid: str, operation: str, session: _Session, begin: int, reset: bool
    ) -> dict[str, Any]:
        raw_events = [
            row
            for frame in session.frames
            if (reset or frame.bar_index > begin) and frame.bar_index <= session.cursor
            for row in frame.events
        ]
        events = [
            {
                **{k: v for k, v in row.items() if k != "payload_json"},
                "payload": json.loads(row["payload_json"]),
            }
            for row in raw_events
            if row["known_at_bar_index"] <= session.cursor
        ]
        frame = session.frames[session.cursor - session.frames[0].bar_index]
        account = next(
            json.loads(row["payload_json"])
            for row in reversed(frame.events)
            if json.loads(row["payload_json"]).get("event_type") == "simulation_account"
        )
        pinned = session.payload
        state = session.state
        if state in {"live", "completed"} and session.cursor < session.frames[-1].bar_index:
            state = "lookback"
        snapshot = {
            "session_id": sid,
            "operation_id": operation,
            "dataset_id": pinned["dataset"]["dataset_id"],
            "data_revision": pinned["dataset"]["data_revision"],
            "run_id": pinned["run_id"],
            "run_signature": pinned["run_signature"],
            "from_bar_index": pinned["range"]["from_bar_index"],
            "to_bar_index": pinned["range"]["to_bar_index"],
            "cursor": session.cursor,
            "processed_until_bar_index": session.frames[-1].bar_index,
            "state": state,
            "reset": reset,
            "events": events,
            "account": account,
        }
        if session.completed_run_ref is not None:
            snapshot["completed_run_ref"] = session.completed_run_ref
        return snapshot

    def _publish(
        self, sid: str, reference: str, snapshot: dict[str, Any], *, update_latest: bool
    ) -> str:
        output = self.guard.resolve(reference)
        path = output / "snapshot.json"
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != snapshot:
                raise ValueError("immutable training snapshot already contains different facts")
        else:
            _atomic_json(path, snapshot)
        marker = output / "_SUCCESS"
        checksum = {"snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if not marker.exists():
            _atomic_json(marker, checksum)
        elif json.loads(marker.read_text(encoding="utf-8")) != checksum:
            raise ValueError("training snapshot checksum mismatch")
        if update_latest:
            _atomic_json(
                self.guard.resolve(f"runs/training/{sid}/latest.json"), {"snapshot_ref": reference}
            )
        return reference


class _InitialCancellation(threading.Event):
    def __init__(self, persistent: threading.Event, initial: threading.Event) -> None:
        super().__init__()
        self.persistent = persistent
        self.initial: threading.Event | None = initial

    def is_set(self) -> bool:
        return self.persistent.is_set() or (self.initial is not None and self.initial.is_set())
