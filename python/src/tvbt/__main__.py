from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from tvbt import CONTRACT_VERSION
from tvbt.api.server import InternalServer
from tvbt.config import load
from tvbt.logging_config import configure_logging, set_runtime_logger
from tvbt.storage.path_guard import PathGuard


def main() -> None:
    if sys.version_info[:2] != (3, 14):
        raise SystemExit(
            f"Python 3.14 is required; found {sys.version_info.major}.{sys.version_info.minor}"
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    args = parser.parse_args()
    cfg = load(args.config)
    guard = PathGuard(cfg.data_root)
    log_path = guard.resolve("logs/python/strategy.log")
    logger, logging_runtime = configure_logging(
        log_path,
        level=cfg.logging.level,
        max_bytes=cfg.logging.max_file_bytes,
        backup_count=cfg.logging.backup_count,
        project_root=Path.cwd(),
    )
    set_runtime_logger(logger)
    server = InternalServer((cfg.host, cfg.port), logger, guard)

    def stop(_signum: int, _frame: object) -> None:
        logger.info("engine.stopped", "Python engine stopping")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info(
        "engine.started",
        "Python engine started",
        {
            "listen": f"{cfg.host}:{cfg.port}",
            "contract_version": CONTRACT_VERSION,
            "logging_degraded": logging_runtime.degraded,
        },
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        logging_runtime.close()


if __name__ == "__main__":
    main()
