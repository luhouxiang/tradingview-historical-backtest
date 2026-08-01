from pathlib import Path

from tvbt.config import load


def test_load_repository_config() -> None:
    config = load(Path(__file__).parents[2] / "config" / "app.yaml")
    assert config.contract_version == "1.0.0"
    assert config.host == "127.0.0.1"
    assert config.port == 8091
