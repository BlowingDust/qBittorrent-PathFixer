import json
from pathlib import Path

import pytest

from qbittorrent_pathfixer.config import ExecutionConfig
from qbittorrent_pathfixer.errors import ExecutionUnknownError
from qbittorrent_pathfixer.executor import execute_operations
from qbittorrent_pathfixer.models import PlannedOperation, TorrentFile


HASH = "0123456789abcdef0123456789abcdef01234567"


def torrent_file(index: int, path: str) -> TorrentFile:
    return TorrentFile(index=index, path=path, size=1, progress=1.0, priority=1)


class FakeClient:
    def __init__(self, paths: dict[int, str], delayed_reads: int = 0, never_complete: bool = False):
        self.paths = paths
        self.delayed_reads = delayed_reads
        self.never_complete = never_complete
        self.pending: tuple[str, str] | None = None
        self.writes: list[tuple[str, str, str]] = []

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self.writes.append(("file", old_path, new_path))
        self.pending = (old_path, new_path)

    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self.writes.append(("folder", old_path, new_path))
        self.pending = (old_path, new_path)

    def get_files(self, torrent_hash: str, indexes=None) -> list[TorrentFile]:
        if self.pending and not self.never_complete:
            if self.delayed_reads:
                self.delayed_reads -= 1
            else:
                old, new = self.pending
                self.paths = {index: (new + path[len(old):] if path == old or path.startswith(old + "/") else path) for index, path in self.paths.items()}
                self.pending = None
        selected = self.paths if indexes is None else {index: self.paths[index] for index in indexes}
        return [torrent_file(index, path) for index, path in selected.items()]


def test_waits_for_async_folder_completion(tmp_path: Path) -> None:
    client = FakeClient({0: "old/a.txt", 1: "old/b.txt"}, delayed_reads=1)
    operation = PlannedOperation("op", "rename_folder", "old", "new", False, None, ("record",))
    state = execute_operations(
        client, HASH, (operation,), list(client.get_files(HASH)),
        ExecutionConfig(0.01, 5.0, 0.01), tmp_path / "state.json", sleep=lambda _: None,
    )
    assert state["status"] == "completed"
    assert state["operations"][0]["status"] == "success"
    assert client.paths == {0: "new/a.txt", 1: "new/b.txt"}


def test_timeout_stops_remaining_operations(tmp_path: Path) -> None:
    client = FakeClient({0: "a.txt", 1: "c.txt"}, never_complete=True)
    operations = (
        PlannedOperation("one", "rename_file", "a.txt", "b.txt", False, 0, ("r1",)),
        PlannedOperation("two", "rename_file", "c.txt", "d.txt", False, 1, ("r2",)),
    )
    clock = iter([0.0, 2.0])
    with pytest.raises(ExecutionUnknownError, match="确认超时"):
        execute_operations(
            client, HASH, operations, list(client.get_files(HASH)),
            ExecutionConfig(0.01, 1.0, 0.01), tmp_path / "state.json",
            monotonic=lambda: next(clock), sleep=lambda _: None,
        )
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped_unknown"
    assert state["operations"][0]["status"] == "unknown"
    assert state["operations"][1]["status"] == "not_run"
    assert len(client.writes) == 1
