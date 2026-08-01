from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from tvbt.api.server import InternalServer
from tvbt.logging_config import configure_logging


def test_health_and_placeholder_job(tmp_path: Path) -> None:
    logger, runtime = configure_logging(
        tmp_path / "test.ndjson",
        level="INFO",
        max_bytes=1024,
        backup_count=9,
        project_root=Path.cwd(),
    )
    server = InternalServer(("127.0.0.1", 0), logger)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.request("GET", "/internal/v1/health")
        response = connection.getresponse()
        health = json.loads(response.read())
        assert response.status == 200
        assert health["contract_version"] == "1.0.0"

        body = json.dumps(
            {
                "contract_version": "1.0.0",
                "request_id": "req-1",
                "trace_id": "trace-1",
                "job_id": "job-1",
                "dataset": {},
                "output_path": "cache/job-1",
            }
        )
        connection.request(
            "POST",
            "/internal/v1/job-submissions/calculation",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        accepted = connection.getresponse()
        payload = json.loads(accepted.read())
        assert accepted.status == 202
        assert payload["status"] == "queued"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()
