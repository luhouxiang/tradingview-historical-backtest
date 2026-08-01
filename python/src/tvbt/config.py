from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tvbt import CONTRACT_VERSION


class ConfigError(ValueError):
    """Raised when application configuration violates a fixed boundary."""


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    max_file_bytes: int
    backup_count: int
    compress_backups: bool


@dataclass(frozen=True)
class AppConfig:
    contract_version: str
    host: str
    port: int
    data_root: Path
    logging: LoggingConfig


def load(path: Path) -> AppConfig:
    document = _parse_yaml_subset(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ConfigError("unsupported schema_version")
    app = _mapping(document, "app")
    engine = _mapping(document, "python_engine")
    storage = _mapping(document, "storage")
    logging = _mapping(document, "logging")
    contract_version = _string(app, "contract_version")
    if contract_version != CONTRACT_VERSION:
        raise ConfigError(
            f"contract_version {contract_version!r} does not match {CONTRACT_VERSION!r}"
        )
    parsed = urlparse(_string(engine, "base_url"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ConfigError("python_engine.base_url must use HTTP on a loopback host")
    if parsed.port is None:
        raise ConfigError("python_engine.base_url must include a port")
    root_value = _string(storage, "data_root")
    root = (
        (Path.cwd() / root_value).resolve()
        if not Path(root_value).is_absolute()
        else Path(root_value).resolve()
    )
    log_config = LoggingConfig(
        level=_string(logging, "level"),
        max_file_bytes=_integer(logging, "max_file_bytes"),
        backup_count=_integer(logging, "backup_count"),
        compress_backups=_boolean(logging, "compress_backups"),
    )
    if log_config.backup_count != 9 or log_config.max_file_bytes <= 0:
        raise ConfigError("logging requires backup_count 9 and positive max_file_bytes")
    return AppConfig(
        contract_version, parsed.hostname or "127.0.0.1", parsed.port, root, log_config
    )


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise ConfigError(f"line {number}: indentation must use two spaces")
        stripped = raw.strip()
        if ":" not in stripped:
            raise ConfigError(f"line {number}: expected key: value")
        key, raw_value = stripped.split(":", 1)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if key in parent:
            raise ConfigError(f"line {number}: duplicate key {key!r}")
        if not raw_value.strip():
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value.strip())
    return root


def _parse_scalar(value: str) -> Any:
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _integer(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer")
    return value


def _boolean(parent: dict[str, Any], key: str) -> bool:
    value = parent.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value
