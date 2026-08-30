from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from .errors import ScanDataError


def normalize_internal_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScanDataError("种子内部路径为空")
    if "\x00" in value or "\\" in value:
        raise ScanDataError(f"种子内部路径包含非法分隔符或 NUL：{value!r}")
    if value.startswith("/") or value.endswith("/"):
        raise ScanDataError(f"种子内部路径必须是规范相对路径：{value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ScanDataError(f"种子内部路径包含空段、. 或 ..：{value!r}")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != value:
        raise ScanDataError(f"种子内部路径不是规范形式：{value!r}")
    return normalized


def parent_directories(file_path: str) -> tuple[str, ...]:
    parts = file_path.split("/")
    return tuple("/".join(parts[:index]) for index in range(1, len(parts)))


def windows_absolute_path(destination_root: str, relative_path: str) -> str:
    root = PureWindowsPath(destination_root)
    child = PureWindowsPath(*relative_path.split("/"))
    return str(root / child)


def path_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]

