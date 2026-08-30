from __future__ import annotations

from collections import defaultdict

from .compatibility import calculate_metrics, inspect_entry, windows_key
from .config import AppConfig
from .errors import ScanDataError
from .models import Issue, ScanResult, ScannedEntry, ServerInfo, TorrentFile, TorrentInfo
from .paths import normalize_internal_path, parent_directories


def scan_files(
    server: ServerInfo,
    torrent: TorrentInfo,
    raw_files: list[TorrentFile],
    config: AppConfig,
) -> ScanResult:
    seen_indexes: set[int] = set()
    files: list[ScannedEntry] = []
    directory_paths: set[str] = set()

    for raw in sorted(raw_files, key=lambda item: item.index):
        if raw.index in seen_indexes:
            raise ScanDataError(f"文件列表包含重复 index：{raw.index}")
        path = normalize_internal_path(raw.path)
        seen_indexes.add(raw.index)
        directory_paths.update(parent_directories(path))
        metrics = calculate_metrics(path, config.windows)
        files.append(
            ScannedEntry(
                item_type="file",
                path=path,
                metrics=metrics,
                issues=inspect_entry(path, "file", metrics, config.limits),
                file_index=raw.index,
                size=raw.size,
                progress=raw.progress,
                priority=raw.priority,
            )
        )

    directories: list[ScannedEntry] = []
    for path in sorted(directory_paths):
        metrics = calculate_metrics(path, config.windows)
        directories.append(
            ScannedEntry(
                item_type="folder",
                path=path,
                metrics=metrics,
                issues=inspect_entry(path, "folder", metrics, config.limits),
            )
        )

    _add_duplicate_path_issues(files)
    _add_windows_collisions([*directories, *files], config.windows.case_sensitive)
    return ScanResult(server=server, torrent=torrent, files=tuple(files), directories=tuple(directories))


def _add_duplicate_path_issues(files: list[ScannedEntry]) -> None:
    groups: dict[str, list[ScannedEntry]] = defaultdict(list)
    for entry in files:
        groups[entry.path].append(entry)
    for path, group in groups.items():
        if len(group) < 2:
            continue
        indexes = ",".join(str(entry.file_index) for entry in group)
        for entry in group:
            entry.issues.append(
                Issue(
                    code="DUPLICATE_INTERNAL_PATH",
                    severity="error",
                    component=path,
                    actual=indexes,
                    limit=None,
                    message="多个种子文件具有相同内部路径；路径式改名无法直接区分，执行前需专门处理",
                )
            )


def _add_windows_collisions(entries: list[ScannedEntry], case_sensitive: bool) -> None:
    if case_sensitive:
        return
    groups: dict[str, list[ScannedEntry]] = defaultdict(list)
    for entry in entries:
        groups[windows_key(entry.path)].append(entry)
    for key, group in groups.items():
        distinct = {(entry.item_type, entry.path) for entry in group}
        if len(distinct) < 2:
            continue
        paths = "; ".join(sorted(entry.path for entry in group))
        for entry in group:
            entry.issues.append(
                Issue(
                    code="WINDOWS_CASE_COLLISION",
                    severity="error",
                    component=entry.path,
                    actual=paths,
                    limit=None,
                    message=f"Windows 不区分大小写或 Unicode 归一化后发生冲突（键：{key}）",
                )
            )
