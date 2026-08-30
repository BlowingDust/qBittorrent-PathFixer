from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig
from .models import ScanResult, ScannedEntry


CSV_FIELDS = (
    "schema_version",
    "snapshot_sha256",
    "record_id",
    "torrent_hash",
    "item_type",
    "file_index",
    "old_path",
    "new_path",
    "issues",
    "old_name_chars",
    "old_name_utf8_bytes",
    "old_path_chars",
    "old_path_utf8_bytes",
    "old_windows_absolute_path_chars",
    "action",
    "note",
)


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def record_id(torrent_hash: str, item_type: str, old_path: str, file_index: int | None = None) -> str:
    identity = str(file_index) if item_type == "file" else ""
    raw = f"1\0{torrent_hash}\0{item_type}\0{identity}\0{old_path}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, data: Any) -> None:
    rendered = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_write_bytes(path, rendered.encode("utf-8"))


def _config_fingerprint(config: AppConfig) -> str:
    public = config.public_dict()
    public["qbittorrent"].pop("password_env", None)
    public["qbittorrent"].pop("username", None)
    public.pop("output", None)
    return sha256_prefixed(canonical_json_bytes(public))


def _server_dict(result: ScanResult) -> dict[str, Any]:
    return asdict(result.server)


def _torrent_dict(result: ScanResult) -> dict[str, Any]:
    return asdict(result.torrent)


def build_snapshot(result: ScanResult, config: AppConfig, created_at: str) -> tuple[dict[str, Any], str]:
    body = {
        "schema_version": 1,
        "created_at": created_at,
        "server": _server_dict(result),
        "torrent": _torrent_dict(result),
        "limits": asdict(config.limits),
        "windows": asdict(config.windows),
        "directories": [entry.to_dict() for entry in result.directories],
        "files": [entry.to_dict() for entry in result.files],
    }
    digest = sha256_prefixed(canonical_json_bytes(body))
    snapshot = {**body, "snapshot_sha256": digest}
    return snapshot, digest


def _excel_safe(value: str) -> str:
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    if value.startswith("'"):
        return "'" + value
    return value


def _problem_entries(result: ScanResult) -> list[ScannedEntry]:
    entries = [entry for entry in (*result.directories, *result.files) if entry.issues]
    return sorted(entries, key=lambda entry: (entry.path, entry.item_type))


def write_csv(path: Path, result: ScanResult, snapshot_sha256: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    entries = _problem_entries(result)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="raise")
            writer.writeheader()
            for entry in entries:
                metrics = entry.metrics
                writer.writerow(
                    {
                        "schema_version": 1,
                        "snapshot_sha256": snapshot_sha256,
                        "record_id": record_id(result.torrent.hash, entry.item_type, entry.path, entry.file_index),
                        "torrent_hash": result.torrent.hash,
                        "item_type": entry.item_type,
                        "file_index": "" if entry.file_index is None else entry.file_index,
                        "old_path": _excel_safe(entry.path),
                        "new_path": "",
                        "issues": ";".join(sorted({issue.code for issue in entry.issues})),
                        "old_name_chars": metrics.name_chars,
                        "old_name_utf8_bytes": metrics.name_utf8_bytes,
                        "old_path_chars": metrics.relative_path_chars,
                        "old_path_utf8_bytes": metrics.relative_path_utf8_bytes,
                        "old_windows_absolute_path_chars": metrics.windows_absolute_path_chars,
                        "action": "",
                        "note": "",
                    }
                )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return len(entries)


def _issue_counts(entries: Iterable[ScannedEntry]) -> dict[str, int]:
    counts = Counter(issue.code for entry in entries for issue in entry.issues)
    return dict(sorted(counts.items()))


def write_scan_artifacts(result: ScanResult, config: AppConfig, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = timestamp.isoformat().replace("+00:00", "Z")
    run_id = f"{timestamp:%Y%m%dT%H%M%SZ}_{result.torrent.hash[:12]}_scan"
    run_dir = config.output.root / run_id
    if run_dir.exists():
        counter = 2
        while (config.output.root / f"{run_id}_{counter}").exists():
            counter += 1
        run_dir = config.output.root / f"{run_id}_{counter}"
    run_dir.mkdir(parents=True, exist_ok=False)

    snapshot, snapshot_digest = build_snapshot(result, config, created_at)
    write_json(run_dir / "snapshot.json", snapshot)
    csv_count = write_csv(run_dir / "rename_intents.csv", result, snapshot_digest)
    all_entries = (*result.directories, *result.files)
    report = {
        "schema_version": 1,
        "created_at": created_at,
        "torrent_hash": result.torrent.hash,
        "file_count": len(result.files),
        "directory_count": len(result.directories),
        "problem_file_count": sum(bool(entry.issues) for entry in result.files),
        "problem_directory_count": sum(bool(entry.issues) for entry in result.directories),
        "csv_record_count": csv_count,
        "issue_counts": _issue_counts(all_entries),
    }
    write_json(run_dir / "scan_report.json", report)
    manifest = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": created_at,
        "server": _server_dict(result),
        "torrent": _torrent_dict(result),
        "config_fingerprint": _config_fingerprint(config),
        "snapshot_sha256": snapshot_digest,
        "artifacts": {
            "snapshot": "snapshot.json",
            "rename_intents": "rename_intents.csv",
            "scan_report": "scan_report.json",
        },
    }
    write_json(run_dir / "manifest.json", manifest)
    log_line = (
        f"{created_at} INFO scan_completed run_id={run_dir.name} "
        f"files={len(result.files)} directories={len(result.directories)} "
        f"csv_records={csv_count}\n"
    )
    _atomic_write_bytes(run_dir / "run.log", log_line.encode("utf-8"))
    return run_dir


def write_validation_artifacts(
    config: AppConfig,
    torrent_hash: str,
    source_csv: Path,
    snapshot_path: Path,
    messages: Iterable[Any],
    operations: Iterable[Any],
    current_file_count: int,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = timestamp.isoformat().replace("+00:00", "Z")
    run_id = f"{timestamp:%Y%m%dT%H%M%SZ}_{torrent_hash[:12]}_validate"
    run_dir = config.output.root / run_id
    counter = 2
    while run_dir.exists():
        run_dir = config.output.root / f"{run_id}_{counter}"
        counter += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    message_list = list(messages)
    operation_list = list(operations)
    _atomic_write_bytes(run_dir / "submitted_intents.csv", source_csv.read_bytes())
    _atomic_write_bytes(run_dir / "source_snapshot.json", snapshot_path.read_bytes())
    report = {
        "schema_version": 1,
        "created_at": created_at,
        "torrent_hash": torrent_hash,
        "current_file_count": current_file_count,
        "error_count": sum(message.severity == "error" for message in message_list),
        "warning_count": sum(message.severity == "warning" for message in message_list),
        "messages": [message.to_dict() for message in message_list],
    }
    plan = {
        "schema_version": 1,
        "created_at": created_at,
        "torrent_hash": torrent_hash,
        "operation_count": len(operation_list),
        "temporary_operation_count": sum(operation.temporary for operation in operation_list),
        "operations": [operation.to_dict() for operation in operation_list],
    }
    write_json(run_dir / "validation_report.json", report)
    write_json(run_dir / "execution_plan.json", plan)
    _write_validation_messages_csv(run_dir / "validation_issues.csv", message_list)
    log_line = (
        f"{created_at} INFO validation_completed run_id={run_dir.name} "
        f"errors={report['error_count']} warnings={report['warning_count']} "
        f"operations={len(operation_list)}\n"
    )
    _atomic_write_bytes(run_dir / "run.log", log_line.encode("utf-8"))
    return run_dir


def create_execution_artifacts(
    config: AppConfig,
    torrent_hash: str,
    source_csv: Path,
    snapshot_path: Path,
    operations: Iterable[Any],
    initial_files: Iterable[Any],
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = timestamp.isoformat().replace("+00:00", "Z")
    run_id = f"{timestamp:%Y%m%dT%H%M%SZ}_{torrent_hash[:12]}_execute"
    run_dir = config.output.root / run_id
    counter = 2
    while run_dir.exists():
        run_dir = config.output.root / f"{run_id}_{counter}"
        counter += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    operation_list = list(operations)
    file_list = list(initial_files)
    _atomic_write_bytes(run_dir / "submitted_intents.csv", source_csv.read_bytes())
    _atomic_write_bytes(run_dir / "source_snapshot.json", snapshot_path.read_bytes())
    write_json(run_dir / "execution_plan.json", {
        "schema_version": 1,
        "created_at": created_at,
        "torrent_hash": torrent_hash,
        "operation_count": len(operation_list),
        "operations": [operation.to_dict() for operation in operation_list],
    })
    write_json(run_dir / "before_files.json", {
        "schema_version": 1,
        "created_at": created_at,
        "torrent_hash": torrent_hash,
        "files": [asdict(item) for item in file_list],
    })
    _atomic_write_bytes(run_dir / "run.log", f"{created_at} INFO execution_created operations={len(operation_list)}\n".encode("utf-8"))
    return run_dir


def _write_validation_messages_csv(path: Path, messages: list[Any]) -> None:
    fields = ("severity", "code", "record_id", "path", "message")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for message in messages:
                writer.writerow(
                    {
                        "severity": message.severity,
                        "code": message.code,
                        "record_id": message.record_id or "",
                        "path": _excel_safe(message.path or ""),
                        "message": _excel_safe(message.message),
                    }
                )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
