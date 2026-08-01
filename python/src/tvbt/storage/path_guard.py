from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class PathEscapeError(ValueError):
    """Raised when a referenced file escapes data_root."""


class PathGuard:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)

    def resolve(self, relative: str) -> Path:
        if (
            not relative
            or PurePosixPath(relative).is_absolute()
            or PureWindowsPath(relative).is_absolute()
        ):
            raise PathEscapeError("absolute and empty paths are forbidden")
        normalized = relative.replace("\\", "/")
        if ".." in PurePosixPath(normalized).parts:
            raise PathEscapeError("parent traversal is forbidden")
        candidate = self.root.joinpath(*PurePosixPath(normalized).parts)
        existing = candidate
        missing: list[str] = []
        while not existing.exists() and existing != self.root:
            missing.append(existing.name)
            existing = existing.parent
        resolved_existing = existing.resolve(strict=True)
        if not resolved_existing.is_relative_to(self.root):
            raise PathEscapeError("symlink escapes data_root")
        resolved = resolved_existing.joinpath(*reversed(missing))
        if not resolved.is_relative_to(self.root):
            raise PathEscapeError("path escapes data_root")
        return resolved

    def relative(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise PathEscapeError("path escapes data_root")
        return resolved.relative_to(self.root).as_posix()
