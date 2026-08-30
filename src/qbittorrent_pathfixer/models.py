from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Limit = int | Literal[False]


@dataclass(frozen=True)
class ServerInfo:
    app_version: str
    webapi_version: str
    base_url: str
    authenticated: bool


@dataclass(frozen=True)
class TorrentInfo:
    hash: str
    name: str
    save_path: str | None = None
    content_path: str | None = None


@dataclass(frozen=True)
class TorrentFile:
    index: int
    path: str
    size: int
    progress: float
    priority: int


@dataclass(frozen=True)
class PathMetrics:
    name_chars: int
    name_utf8_bytes: int
    relative_path_chars: int
    relative_path_utf8_bytes: int
    windows_absolute_path_chars: int

    def to_dict(self) -> dict[str, int]:
        return {
            "name_chars": self.name_chars,
            "name_utf8_bytes": self.name_utf8_bytes,
            "relative_path_chars": self.relative_path_chars,
            "relative_path_utf8_bytes": self.relative_path_utf8_bytes,
            "windows_absolute_path_chars": self.windows_absolute_path_chars,
        }


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    component: str | None
    actual: int | str | None
    limit: int | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "component": self.component,
            "actual": self.actual,
            "limit": self.limit,
            "message": self.message,
        }


@dataclass
class ScannedEntry:
    item_type: Literal["file", "folder"]
    path: str
    metrics: PathMetrics
    issues: list[Issue] = field(default_factory=list)
    file_index: int | None = None
    size: int | None = None
    progress: float | None = None
    priority: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "item_type": self.item_type,
            "path": self.path,
            "metrics": self.metrics.to_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if self.item_type == "file":
            result.update(
                {
                    "index": self.file_index,
                    "size": self.size,
                    "progress": self.progress,
                    "priority": self.priority,
                }
            )
        return result


@dataclass(frozen=True)
class ScanResult:
    server: ServerInfo
    torrent: TorrentInfo
    files: tuple[ScannedEntry, ...]
    directories: tuple[ScannedEntry, ...]


@dataclass(frozen=True)
class RenameIntent:
    record_id: str
    item_type: Literal["file", "folder"]
    file_index: int | None
    old_path: str
    new_path: str
    action: Literal["rename", "skip"]
    note: str
    issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class ValidationMessage:
    severity: Literal["error", "warning"]
    code: str
    message: str
    record_id: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlannedOperation:
    operation_id: str
    kind: Literal["rename_file", "rename_folder"]
    source_path: str
    target_path: str
    temporary: bool
    file_index: int | None
    reason_record_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
