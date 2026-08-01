from __future__ import annotations

import json
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tvbt import CONTRACT_VERSION, ENGINE_VERSION
from tvbt.algorithms import definitions
from tvbt.api.jobs import Job, JobStore
from tvbt.backtest import run_backtest
from tvbt.calculation import calculate
from tvbt.logging_config import StructuredLogger
from tvbt.optimization import run_study
from tvbt.replay import generate_replay
from tvbt.storage.path_guard import PathGuard

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VALID_KINDS = {"calculation", "replay", "backtest", "optimization"}


class InternalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], logger: StructuredLogger, guard: PathGuard | None = None
    ) -> None:
        super().__init__(address, InternalHandler)
        self.jobs = JobStore()
        self.structured_logger = logger
        self.guard = guard or PathGuard(Path.cwd() / ".tvbt-test-data")

    def run_calculation(self, job_id: str) -> None:
        self.jobs.run(job_id, lambda value, event: calculate(value, self.guard, event))
        job = self.jobs.get(job_id)
        if job is not None:
            self.structured_logger.info(
                "calculation.finished",
                "indicator calculation finished",
                {"job_id": job_id, "status": job.status, "progress": job.progress},
            )

    def run_replay(self, job_id: str) -> None:
        self.jobs.run(job_id, lambda value, event: generate_replay(value, self.guard, event))
        job = self.jobs.get(job_id)
        if job is not None:
            self.structured_logger.info(
                "replay.generation.finished",
                "causal replay generation finished",
                {"job_id": job_id, "status": job.status, "progress": job.progress},
            )

    def run_backtest(self, job_id: str) -> None:
        self.jobs.run(job_id, lambda value, event: run_backtest(value, self.guard, event))
        job = self.jobs.get(job_id)
        if job is not None:
            self.structured_logger.info(
                "backtest.finished",
                "formal backtest finished",
                {"job_id": job_id, "status": job.status, "progress": job.progress},
            )

    def run_optimization(self, job_id: str) -> None:
        self.jobs.run(
            job_id,
            lambda value, event: run_study(
                value, self.guard, event, lambda progress: self.jobs.progress(job_id, progress)
            ),
        )
        job = self.jobs.get(job_id)
        if job is not None:
            self.structured_logger.info(
                "optimization.finished",
                "parameter optimization study finished",
                {"job_id": job_id, "status": job.status, "progress": job.progress},
            )


class InternalHandler(BaseHTTPRequestHandler):
    server: InternalServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        request_id, trace_id = self._ids()
        path = urlparse(self.path).path
        if path == "/internal/v1/health":
            self._json(
                HTTPStatus.OK,
                {
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "status": "ok",
                    "contract_version": CONTRACT_VERSION,
                    "services": {"python-engine": {"status": "ok", "version": ENGINE_VERSION}},
                },
            )
            return
        if path == "/internal/v1/algorithms":
            self._json(HTTPStatus.OK, {"request_id": request_id, "algorithms": definitions()})
            return
        match = re.fullmatch(r"/internal/v1/jobs/([^/]+)", path)
        if match:
            job = self.server.jobs.get(match.group(1))
            if job is None:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job does not exist", request_id)
            else:
                self._json(HTTPStatus.OK, job.response())
            return
        self._error(
            HTTPStatus.NOT_FOUND, "ENDPOINT_NOT_FOUND", "Endpoint does not exist", request_id
        )

    def do_POST(self) -> None:
        request_id, trace_id = self._ids()
        path = urlparse(self.path).path
        submit = re.fullmatch(r"/internal/v1/job-submissions/([^/]+)", path)
        if submit:
            kind = submit.group(1)
            if kind not in VALID_KINDS:
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "INVALID_JOB_KIND",
                    "Unknown job kind",
                    request_id,
                )
                return
            payload = self._body(request_id)
            if payload is None:
                return
            if payload.get("contract_version") != CONTRACT_VERSION:
                self._error(
                    HTTPStatus.CONFLICT,
                    "CONTRACT_VERSION_MISMATCH",
                    "Contract version mismatch",
                    request_id,
                )
                return
            job_id = payload.get("job_id")
            if not isinstance(job_id, str) or not ID_PATTERN.fullmatch(job_id):
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "INVALID_JOB_ID",
                    "job_id is invalid",
                    request_id,
                )
                return
            job = Job(job_id, kind, request_id, trace_id, payload=payload)
            if not self.server.jobs.submit(job):
                self._error(
                    HTTPStatus.CONFLICT, "JOB_ALREADY_EXISTS", "job_id already exists", request_id
                )
                return
            if kind == "calculation":
                threading.Thread(
                    target=self.server.run_calculation,
                    args=(job_id,),
                    daemon=True,
                ).start()
            elif kind == "replay":
                threading.Thread(
                    target=self.server.run_replay,
                    args=(job_id,),
                    daemon=True,
                ).start()
            elif kind == "backtest":
                threading.Thread(
                    target=self.server.run_backtest,
                    args=(job_id,),
                    daemon=True,
                ).start()
            elif kind == "optimization":
                threading.Thread(
                    target=self.server.run_optimization,
                    args=(job_id,),
                    daemon=True,
                ).start()
            self.server.structured_logger.info(
                "python.job.submitted",
                "job accepted",
                {"job_id": job_id, "kind": kind, "trace_id": trace_id},
            )
            self._json(
                HTTPStatus.ACCEPTED,
                {"request_id": request_id, "job_id": job_id, "status": "queued"},
            )
            return
        cancel = re.fullmatch(r"/internal/v1/jobs/([^/]+)/cancel", path)
        if cancel:
            cancelled_job = self.server.jobs.cancel(cancel.group(1))
            if cancelled_job is None:
                self._error(HTTPStatus.NOT_FOUND, "JOB_NOT_FOUND", "Job does not exist", request_id)
            else:
                self._json(HTTPStatus.ACCEPTED, cancelled_job.response())
            return
        self._error(
            HTTPStatus.NOT_FOUND, "ENDPOINT_NOT_FOUND", "Endpoint does not exist", request_id
        )

    def _ids(self) -> tuple[str, str]:
        return self._safe_id(self.headers.get("X-Request-ID")), self._safe_id(
            self.headers.get("X-Trace-ID")
        )

    @staticmethod
    def _safe_id(value: str | None) -> str:
        return value if value is not None and ID_PATTERN.fullmatch(value) else secrets.token_hex(12)

    def _body(self, request_id: str) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 262_144:
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError, json.JSONDecodeError:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "Request body must be a JSON object",
                request_id,
            )
            return None

    def _error(self, status: HTTPStatus, code: str, message: str, request_id: str) -> None:
        self._json(status, {"error": {"code": code, "message": message, "request_id": request_id}})

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return
