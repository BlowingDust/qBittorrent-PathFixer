from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol

from .config import ExecutionConfig
from .errors import ConnectionError, ExecutionFailedError, ExecutionUnknownError
from .models import PlannedOperation, TorrentFile
from .records import write_json


class RenameClient(Protocol):
    def get_files(self, torrent_hash: str, indexes: list[int] | tuple[int, ...] | None = None) -> list[TorrentFile]: ...
    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None: ...
    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def execute_operations(
    client: RenameClient,
    torrent_hash: str,
    operations: tuple[PlannedOperation, ...],
    initial_files: list[TorrentFile],
    execution: ExecutionConfig,
    state_path: Path,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    current = {item.index: item.path for item in initial_files}
    state = {
        "schema_version": 1,
        "torrent_hash": torrent_hash,
        "status": "executing",
        "started_at": _now(),
        "updated_at": _now(),
        "operations": [
            {**operation.to_dict(), "status": "pending", "started_at": None, "accepted_at": None, "finished_at": None, "error": None}
            for operation in operations
        ],
    }
    write_json(state_path, state)

    for position, operation in enumerate(operations):
        item = state["operations"][position]
        item["status"] = "running"
        item["started_at"] = _now()
        state["updated_at"] = _now()
        write_json(state_path, state)
        try:
            affected = _affected_indexes(operation, current)
            expected = _expected_paths(operation, current, affected)
            if operation.kind == "rename_file":
                client.rename_file(torrent_hash, operation.source_path, operation.target_path)
            else:
                client.rename_folder(torrent_hash, operation.source_path, operation.target_path)
            item["status"] = "accepted"
            item["accepted_at"] = _now()
            state["updated_at"] = _now()
            write_json(state_path, state)
            _wait_for_paths(client, torrent_hash, expected, execution, monotonic, sleep)
        except ExecutionFailedError as exc:
            _stop(state, position, "failed", "stopped_failed", str(exc), state_path)
            raise
        except (ConnectionError, ExecutionUnknownError) as exc:
            _stop(state, position, "unknown", "stopped_unknown", str(exc), state_path)
            raise ExecutionUnknownError(str(exc)) from exc

        current.update(expected)
        item["status"] = "success"
        item["finished_at"] = _now()
        state["updated_at"] = _now()
        write_json(state_path, state)
        sleep(execution.settle_delay_seconds)

    state["status"] = "completed"
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    write_json(state_path, state)
    return state


def _affected_indexes(operation: PlannedOperation, current: dict[int, str]) -> list[int]:
    if operation.kind == "rename_file":
        if operation.file_index is None or current.get(operation.file_index) != operation.source_path:
            raise ExecutionFailedError(f"执行前路径与计划不一致：{operation.source_path}")
        return [operation.file_index]
    affected = sorted(index for index, path in current.items() if path.startswith(operation.source_path + "/"))
    if not affected:
        raise ExecutionFailedError(f"执行前找不到文件夹后代：{operation.source_path}")
    return affected


def _expected_paths(operation: PlannedOperation, current: dict[int, str], affected: list[int]) -> dict[int, str]:
    if operation.kind == "rename_file":
        return {affected[0]: operation.target_path}
    return {
        index: operation.target_path + current[index][len(operation.source_path):]
        for index in affected
    }


def _wait_for_paths(
    client: RenameClient,
    torrent_hash: str,
    expected: dict[int, str],
    execution: ExecutionConfig,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + execution.operation_timeout_seconds
    indexes = sorted(expected)
    while True:
        observed = {item.index: item.path for item in client.get_files(torrent_hash, indexes)}
        if all(observed.get(index) == path for index, path in expected.items()):
            return
        if monotonic() >= deadline:
            mismatch = sum(observed.get(index) != path for index, path in expected.items())
            raise ExecutionUnknownError(f"异步重命名确认超时，{mismatch} 个文件尚未达到目标路径")
        sleep(execution.poll_interval_seconds)


def _stop(state: dict, position: int, item_status: str, overall_status: str, error: str, state_path: Path) -> None:
    item = state["operations"][position]
    item["status"] = item_status
    item["finished_at"] = _now()
    item["error"] = error[:1000]
    for remaining in state["operations"][position + 1:]:
        remaining["status"] = "not_run"
    state["status"] = overall_status
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    write_json(state_path, state)
