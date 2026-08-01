from pathlib import Path

import pytest

from tvbt.storage.path_guard import PathEscapeError, PathGuard


def test_accepts_relative_path(tmp_path: Path) -> None:
    guard = PathGuard(tmp_path)
    assert guard.resolve("cache/job/result.json").is_relative_to(tmp_path)


@pytest.mark.parametrize("value", ["../escape", "a/../../escape", "C:/escape", "\\\\server\\share"])
def test_rejects_escape(tmp_path: Path, value: str) -> None:
    guard = PathGuard(tmp_path)
    with pytest.raises(PathEscapeError):
        guard.resolve(value)


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    guard = PathGuard(tmp_path)
    with pytest.raises(PathEscapeError):
        guard.resolve("escape/file.json")
