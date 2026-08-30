import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from qbittorrent_pathfixer.models import ServerInfo, TorrentFile, TorrentInfo
from qbittorrent_pathfixer.records import build_snapshot, record_id, write_scan_artifacts
from qbittorrent_pathfixer.scanner import scan_files


HASH = "0123456789abcdef0123456789abcdef01234567"


def make_result(app_config):
    server = ServerInfo("v4.6.7", "2.9.3", "http://localhost/", False)
    torrent = TorrentInfo(HASH, "demo")
    files = [
        TorrentFile(0, "normal.txt", 1, 0.0, 1),
        TorrentFile(1, f"{'中' * 100}/ok.txt", 2, 0.0, 1),
    ]
    return scan_files(server, torrent, files, app_config)


def test_snapshot_hash_is_deterministic(app_config) -> None:
    result = make_result(app_config)
    one, one_hash = build_snapshot(result, app_config, "2026-08-17T00:00:00Z")
    two, two_hash = build_snapshot(result, app_config, "2026-08-17T00:00:00Z")
    assert one == two
    assert one_hash == two_hash == one["snapshot_sha256"]


def test_artifacts_include_only_problem_entries_in_csv(app_config) -> None:
    run_dir = write_scan_artifacts(make_result(app_config), app_config, datetime(2026, 8, 17, tzinfo=UTC))
    assert {path.name for path in run_dir.iterdir()} == {
        "manifest.json",
        "snapshot.json",
        "rename_intents.csv",
        "scan_report.json",
        "run.log",
    }
    assert (run_dir / "rename_intents.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with (run_dir / "rename_intents.csv").open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    assert rows[0]["item_type"] == "folder"
    assert rows[0]["new_path"] == ""
    snapshot = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert len(snapshot["files"]) == 2
    assert len(snapshot["directories"]) == 1


def test_record_id_is_stable_and_scoped() -> None:
    first = record_id(HASH, "file", "a.txt", 1)
    assert first == record_id(HASH, "file", "a.txt", 1)
    assert first != record_id(HASH, "file", "a.txt", 2)
    assert first != record_id(HASH, "folder", "a.txt")
    assert len(first) == 20
