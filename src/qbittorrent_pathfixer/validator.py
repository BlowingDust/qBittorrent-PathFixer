from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compatibility import calculate_metrics, inspect_entry, windows_key
from .config import AppConfig
from .errors import PlanFormatError
from .models import RenameIntent, ValidationMessage
from .paths import normalize_internal_path
from .records import CSV_FIELDS, record_id


@dataclass(frozen=True)
class LoadedPlan:
    snapshot: dict[str, Any]
    snapshot_path: Path
    csv_path: Path
    intents: tuple[RenameIntent, ...]


@dataclass(frozen=True)
class ValidatedNamespace:
    loaded: LoadedPlan
    final_by_index: dict[int, str]
    folder_mappings: dict[str, str]
    messages: tuple[ValidationMessage, ...]

    @property
    def errors(self) -> tuple[ValidationMessage, ...]:
        return tuple(message for message in self.messages if message.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationMessage, ...]:
        return tuple(message for message in self.messages if message.severity == "warning")


def _csv_unescape(value: str) -> str:
    if value.startswith("''"):
        return value[1:]
    if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@\t\r":
        return value[1:]
    return value


def _expected_problem_rows(snapshot: dict[str, Any]) -> dict[str, dict[str, str]]:
    torrent_hash = snapshot["torrent"]["hash"]
    digest = snapshot["snapshot_sha256"]
    expected: dict[str, dict[str, str]] = {}
    for entry in [*snapshot["directories"], *snapshot["files"]]:
        if not entry["issues"]:
            continue
        item_type = entry["item_type"]
        file_index = entry.get("index") if item_type == "file" else None
        rid = record_id(torrent_hash, item_type, entry["path"], file_index)
        metrics = entry["metrics"]
        expected[rid] = {
            "schema_version": "1",
            "snapshot_sha256": digest,
            "record_id": rid,
            "torrent_hash": torrent_hash,
            "item_type": item_type,
            "file_index": "" if file_index is None else str(file_index),
            "old_path": entry["path"],
            "issues": ";".join(sorted({issue["code"] for issue in entry["issues"]})),
            "old_name_chars": str(metrics["name_chars"]),
            "old_name_utf8_bytes": str(metrics["name_utf8_bytes"]),
            "old_path_chars": str(metrics["relative_path_chars"]),
            "old_path_utf8_bytes": str(metrics["relative_path_utf8_bytes"]),
            "old_windows_absolute_path_chars": str(metrics["windows_absolute_path_chars"]),
        }
    return expected


def load_plan(csv_path: Path) -> LoadedPlan:
    snapshot_path = csv_path.parent / "snapshot.json"
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlanFormatError(f"找不到与 CSV 同目录的 snapshot.json：{snapshot_path}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlanFormatError(f"snapshot.json 无法读取：{exc}") from exc
    if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("snapshot_sha256"), str):
        raise PlanFormatError("snapshot.json 架构版本或摘要无效")
    expected = _expected_problem_rows(snapshot)
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None or set(reader.fieldnames) != set(CSV_FIELDS) or len(reader.fieldnames) != len(CSV_FIELDS):
                raise PlanFormatError("CSV 表头与 schema_version 1 不一致")
            rows = list(reader)
    except FileNotFoundError as exc:
        raise PlanFormatError(f"CSV 文件不存在：{csv_path}") from exc
    except UnicodeError as exc:
        raise PlanFormatError("CSV 不是有效的 UTF-8/UTF-8 BOM 文件") from exc
    if len(rows) != len(expected):
        raise PlanFormatError(f"CSV 应包含 {len(expected)} 条问题记录，实际为 {len(rows)} 条")

    seen: set[str] = set()
    intents: list[RenameIntent] = []
    generated_fields = tuple(name for name in CSV_FIELDS if name not in {"new_path", "action", "note"})
    for line_no, row in enumerate(rows, 2):
        rid = (row.get("record_id") or "").strip()
        if rid in seen:
            raise PlanFormatError(f"CSV 第 {line_no} 行 record_id 重复：{rid}")
        seen.add(rid)
        reference = expected.get(rid)
        if reference is None:
            raise PlanFormatError(f"CSV 第 {line_no} 行 record_id 不属于当前快照：{rid}")
        for field in generated_fields:
            actual = _csv_unescape(row.get(field, "")) if field == "old_path" else (row.get(field) or "")
            if actual != reference[field]:
                raise PlanFormatError(f"CSV 第 {line_no} 行程序字段被修改：{field}")
        action = (row.get("action") or "").strip().lower()
        if action not in {"rename", "skip"}:
            raise PlanFormatError(f"CSV 第 {line_no} 行 action 必须是 rename 或 skip")
        # Path-leading/trailing spaces are meaningful and must be validated,
        # not silently removed. Only action is whitespace-normalized.
        new_path = _csv_unescape(row.get("new_path") or "")
        if action == "rename":
            if not new_path:
                raise PlanFormatError(f"CSV 第 {line_no} 行 action=rename 但 new_path 为空")
            try:
                new_path = normalize_internal_path(new_path)
            except Exception as exc:
                raise PlanFormatError(f"CSV 第 {line_no} 行 new_path 无效：{exc}") from exc
        file_index_text = reference["file_index"]
        intents.append(
            RenameIntent(
                record_id=rid,
                item_type=reference["item_type"],
                file_index=int(file_index_text) if file_index_text else None,
                old_path=reference["old_path"],
                new_path=new_path,
                action=action,
                note=row.get("note") or "",
                issue_codes=tuple(reference["issues"].split(";")) if reference["issues"] else (),
            )
        )
    if seen != set(expected):
        raise PlanFormatError("CSV 缺少快照中的问题记录")
    return LoadedPlan(snapshot=snapshot, snapshot_path=snapshot_path, csv_path=csv_path, intents=tuple(intents))


def _is_descendant(path: str, folder: str) -> bool:
    return path.startswith(folder + "/")


def _apply_folder_mapping(path: str, mappings: dict[str, str]) -> str:
    candidates = [source for source in mappings if _is_descendant(path, source)]
    if not candidates:
        return path
    source = max(candidates, key=lambda item: (item.count("/"), len(item)))
    return mappings[source] + path[len(source):]


def validate_namespace(loaded: LoadedPlan, config: AppConfig) -> ValidatedNamespace:
    messages: list[ValidationMessage] = []
    folder_intents = [intent for intent in loaded.intents if intent.item_type == "folder"]
    file_intents = {intent.file_index: intent for intent in loaded.intents if intent.item_type == "file"}
    mappings = {intent.old_path: intent.new_path for intent in folder_intents if intent.action == "rename"}

    original_directories = {entry["path"] for entry in loaded.snapshot["directories"]}
    target_groups: dict[str, list[str]] = defaultdict(list)
    for source, target in mappings.items():
        target_groups[target].append(source)
        if target in original_directories and target not in mappings:
            messages.append(ValidationMessage("error", "FOLDER_TARGET_OCCUPIED", "文件夹目标已存在且不会被移走", path=target))
    for target, sources in target_groups.items():
        if len(sources) > 1:
            messages.append(ValidationMessage("error", "DUPLICATE_FOLDER_TARGET", "多个文件夹映射到同一目标", path=target))

    sorted_sources = sorted(mappings, key=lambda item: (item.count("/"), item))
    for source in sorted_sources:
        target = mappings[source]
        if target == source or _is_descendant(target, source):
            messages.append(ValidationMessage("error", "INVALID_FOLDER_TARGET", "文件夹不能保持原名或移动到自身后代", path=source))
        for ancestor in sorted_sources:
            if ancestor == source or not _is_descendant(source, ancestor):
                continue
            expected_prefix = mappings[ancestor] + "/"
            if not target.startswith(expected_prefix):
                messages.append(ValidationMessage("error", "INCONSISTENT_FOLDER_MAPPING", "嵌套文件夹目标与祖先目标不一致", path=source))

    final_by_index: dict[int, str] = {}
    original_by_index: dict[int, str] = {}
    for entry in loaded.snapshot["files"]:
        index = int(entry["index"])
        original = entry["path"]
        original_by_index[index] = original
        inherited = _apply_folder_mapping(original, mappings)
        intent = file_intents.get(index)
        if intent is not None and intent.action == "rename":
            for source, target in mappings.items():
                if _is_descendant(original, source) and not _is_descendant(intent.new_path, target):
                    messages.append(ValidationMessage("error", "INCONSISTENT_FILE_MAPPING", f"文件最终路径必须位于已改名父目录 {target}/ 下", intent.record_id, original))
            final = intent.new_path
        else:
            final = inherited
            if intent is not None and intent.action == "skip":
                messages.append(ValidationMessage("warning", "SKIPPED_PROBLEM", "该问题文件不直接改名；如父目录改名，仅继承父目录变化", intent.record_id, original))
        final_by_index[index] = final

    _validate_final_entries(loaded, config, original_by_index, final_by_index, file_intents, messages)
    return ValidatedNamespace(loaded, final_by_index, mappings, tuple(messages))


def _validate_final_entries(
    loaded: LoadedPlan,
    config: AppConfig,
    original_by_index: dict[int, str],
    final_by_index: dict[int, str],
    file_intents: dict[int | None, RenameIntent],
    messages: list[ValidationMessage],
) -> None:
    exact: dict[str, list[int]] = defaultdict(list)
    folded: dict[str, list[int]] = defaultdict(list)
    for index, final in final_by_index.items():
        exact[final].append(index)
        folded[windows_key(final)].append(index)
        metrics = calculate_metrics(final, config.windows)
        issues = inspect_entry(final, "file", metrics, config.limits)
        intent = file_intents.get(index)
        skipped = intent is not None and intent.action == "skip"
        for issue in issues:
            details = issue.message
            if issue.limit is not None:
                details += f"（实际 {issue.actual}，限制 {issue.limit}）"
            elif issue.actual is not None:
                details += f"（问题值：{issue.actual}）"
            messages.append(
                ValidationMessage(
                    "warning" if skipped else "error",
                    "UNRESOLVED_" + issue.code if skipped else issue.code,
                    details,
                    intent.record_id if intent else None,
                    final,
                )
            )

    for final, indexes in exact.items():
        if len(indexes) < 2:
            continue
        original_paths = {original_by_index[index] for index in indexes}
        all_skipped_duplicates = len(original_paths) == 1 and all(
            (file_intents.get(index) is not None
             and file_intents[index].action == "skip"
             and "DUPLICATE_INTERNAL_PATH" in file_intents[index].issue_codes)
            for index in indexes
        )
        messages.append(
            ValidationMessage(
                "warning" if all_skipped_duplicates else "error",
                "EXISTING_SKIPPED_DUPLICATE" if all_skipped_duplicates else "DUPLICATE_FINAL_PATH",
                f"最终路径由多个文件 index 使用：{','.join(map(str, indexes))}",
                path=final,
            )
        )

    if not config.windows.case_sensitive:
        for key, indexes in folded.items():
            exact_paths = {final_by_index[index] for index in indexes}
            if len(exact_paths) > 1:
                messages.append(ValidationMessage("error", "WINDOWS_FINAL_PATH_COLLISION", f"Windows 兼容键冲突，涉及 index：{','.join(map(str, indexes))}", path=key))

    directory_paths: set[str] = set()
    for final in final_by_index.values():
        parts = final.split("/")
        directory_paths.update("/".join(parts[:position]) for position in range(1, len(parts)))
    for directory in sorted(directory_paths):
        metrics = calculate_metrics(directory, config.windows)
        for issue in inspect_entry(directory, "folder", metrics, config.limits):
            details = issue.message
            if issue.limit is not None:
                details += f"（实际 {issue.actual}，限制 {issue.limit}）"
            messages.append(ValidationMessage("error", issue.code, details, path=directory))
    file_paths = set(final_by_index.values())
    for directory in directory_paths & file_paths:
        messages.append(ValidationMessage("error", "FILE_DIRECTORY_CONFLICT", "同一路径同时作为文件和目录", path=directory))


def compare_snapshot_to_current(snapshot: dict[str, Any], current_files: list[Any]) -> list[ValidationMessage]:
    expected = {(int(entry["index"]), entry["path"], int(entry["size"])) for entry in snapshot["files"]}
    actual = {(item.index, item.path, item.size) for item in current_files}
    if expected == actual:
        return []
    return [ValidationMessage("error", "SNAPSHOT_DRIFT", "qBittorrent 当前文件 index、路径或大小与扫描快照不一致")]
