from __future__ import annotations

import re
import unicodedata

from .config import LimitsConfig, WindowsConfig
from .models import Issue, PathMetrics
from .paths import path_name, windows_absolute_path


INVALID_WINDOWS_CHARS = frozenset('<>:"/\\|?*')
RESERVED_WINDOWS_NAMES = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE
)


def calculate_metrics(path: str, windows: WindowsConfig) -> PathMetrics:
    name = path_name(path)
    absolute = windows_absolute_path(windows.destination_root, path)
    return PathMetrics(
        name_chars=len(name),
        name_utf8_bytes=len(name.encode("utf-8", errors="strict")),
        relative_path_chars=len(path),
        relative_path_utf8_bytes=len(path.encode("utf-8", errors="strict")),
        windows_absolute_path_chars=len(absolute),
    )


def windows_key(path: str) -> str:
    return "/".join(
        unicodedata.normalize("NFC", part).rstrip(" .").casefold()
        for part in path.split("/")
    )


def _exceeded(code: str, actual: int, limit: int | bool, component: str, message: str) -> Issue | None:
    if limit is not False and actual > limit:
        return Issue(code, "error", component, actual, limit, message)
    return None


def inspect_entry(
    path: str,
    item_type: str,
    metrics: PathMetrics,
    limits: LimitsConfig,
) -> list[Issue]:
    name = path_name(path)
    issues: list[Issue] = []
    if item_type == "file":
        checks = (
            _exceeded("FILE_NAME_CHARS_EXCEEDED", metrics.name_chars, limits.filename_chars, name, "文件名字符数超限"),
            _exceeded("FILE_NAME_UTF8_BYTES_EXCEEDED", metrics.name_utf8_bytes, limits.filename_utf8_bytes, name, "文件名 UTF-8 字节数超限"),
        )
    else:
        checks = (
            _exceeded("DIR_NAME_CHARS_EXCEEDED", metrics.name_chars, limits.directory_chars, name, "目录名字符数超限"),
            _exceeded("DIR_NAME_UTF8_BYTES_EXCEEDED", metrics.name_utf8_bytes, limits.directory_utf8_bytes, name, "目录名 UTF-8 字节数超限"),
        )
    issues.extend(issue for issue in checks if issue is not None)
    path_checks = (
        _exceeded("REL_PATH_CHARS_EXCEEDED", metrics.relative_path_chars, limits.relative_path_chars, path, "相对路径字符数超限"),
        _exceeded("REL_PATH_UTF8_BYTES_EXCEEDED", metrics.relative_path_utf8_bytes, limits.relative_path_utf8_bytes, path, "相对路径 UTF-8 字节数超限"),
        _exceeded("WINDOWS_ABS_PATH_CHARS_EXCEEDED", metrics.windows_absolute_path_chars, limits.windows_absolute_path_chars, path, "Windows 最终绝对路径字符数超限"),
    )
    issues.extend(issue for issue in path_checks if issue is not None)
    issues.extend(inspect_windows_name(name))
    return issues


def inspect_windows_name(name: str) -> list[Issue]:
    issues: list[Issue] = []
    invalid = sorted({char for char in name if char in INVALID_WINDOWS_CHARS or ord(char) < 32})
    if invalid:
        shown = " ".join(f"U+{ord(char):04X}" if ord(char) < 32 else char for char in invalid)
        issues.append(Issue("WINDOWS_INVALID_CHAR", "error", name, shown, None, "名称包含 Windows 非法字符"))
    if name.endswith((" ", ".")):
        issues.append(Issue("WINDOWS_TRAILING_DOT_SPACE", "error", name, name[-1], None, "名称以空格或句点结尾"))
    trimmed = name.rstrip(" .")
    stem = trimmed.split(".", 1)[0]
    if RESERVED_WINDOWS_NAMES.fullmatch(stem):
        issues.append(Issue("WINDOWS_RESERVED_NAME", "error", name, stem, None, "名称是 Windows 保留设备名"))
    return issues

