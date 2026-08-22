from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    job_id: str
    kind: str
    request_id: str
    trace_id: str
    status: str = "queued"
    progress: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    result_ref: str | None = None
    error: dict[str, Any] | None = None
    progress_detail: dict[str, Any] = field(default_factory=dict)
    cancelled: threading.Event = field(default_factory=threading.Event)

    def response(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "request_id": self.request_id,
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
        }
        if self.result_ref is not None:
            response["result_ref"] = self.result_ref
        if self.error is not None:
            response["error"] = self.error
        if self.progress_detail:
            response["progress_detail"] = self.progress_detail
        return response


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, job: Job) -> bool:
        with self._lock:
            if job.job_id in self._jobs:
                return False
            self._jobs[job.job_id] = job
            return True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status in {"queued", "running"}:
                job.status = "cancelling"
                job.cancelled.set()
            return job

    def progress(self, job_id: str, value: float, detail: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "running":
                job.progress = max(job.progress, min(0.99, max(0.0, value)))
                if detail is not None:
                    job.progress_detail = dict(detail)

    def run(self, job_id: str, work: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.progress = 0.05
        try:
            result_ref = work(job.payload, job.cancelled)
            with self._lock:
                if job.cancelled.is_set():
                    job.status = "cancelled"
                else:
                    job.status = "completed"
                    job.progress = 1.0
                    job.result_ref = result_ref
        except InterruptedError:
            with self._lock:
                job.status = "cancelled"
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = {"code": f"{job.kind.upper()}_FAILED", "message": str(exc)}
