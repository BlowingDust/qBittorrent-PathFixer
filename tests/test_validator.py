import csv
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qbittorrent_pathfixer.config import LimitsConfig
from qbittorrent_pathfixer.errors import PlanFormatError
from qbittorrent_pathfixer.models import ServerInfo, TorrentFile, TorrentInfo
from qbittorrent_pathfixer.planner import compile_plan
from qbittorrent_pathfixer.records import CSV_FIELDS, write_scan_artifacts
from qbittorrent_pathfixer.scanner import scan_files
from qbittorrent_pathfixer.validator import compare_snapshot_to_current, load_plan, validate_namespace


HASH = "0123456789abcdef0123456789abcdef01234567"
SERVER = ServerInfo("v4.6.7", "2.9.3", "http://localhost/", False)
TORRENT = TorrentInfo(HASH, "demo")


def _write_rows(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_folder_then_file_plan_uses_updated_source_path(app_config) -> None:
    config = replace(
        app_config,
        limits=LimitsConfig(
            filename_chars=8,
            filename_utf8_bytes=False,
            directory_chars=5,
            directory_utf8_bytes=False,
            relative_path_chars=False,
            relative_path_utf8_bytes=False,
            windows_absolute_path_chars=False,
        ),
    )
    raw = [TorrentFile(0, "LongFolder/LongFileName.txt", 10, 0.0, 1)]
    result = scan_files(SERVER, TORRENT, raw, config)
    run = write_scan_artifacts(result, config, datetime(2026, 8, 30, tzinfo=UTC))
    csv_path = run / "rename_intents.csv"
    rows = _read_rows(csv_path)
    for row in rows:
        row["action"] = "rename"
        row["new_path"] = "Short" if row["item_type"] == "folder" else "Short/x.txt"
    _write_rows(csv_path, rows)
    validated = validate_namespace(load_plan(csv_path), config)
    assert not validated.errors
    operations = compile_plan(validated)
    assert operations[0].to_dict()["kind"] == "rename_folder"
    assert [(op.kind, op.source_path, op.target_path) for op in operations] == [
        ("rename_folder", "LongFolder", "Short"),
        ("rename_file", "Short/LongFileName.txt", "Short/x.txt"),
    ]


def test_generated_csv_field_tampering_is_rejected(app_config) -> None:
    config = replace(app_config, limits=replace(app_config.limits, filename_chars=2))
    result = scan_files(SERVER, TORRENT, [TorrentFile(0, "long.txt", 1, 0.0, 1)], config)
    run = write_scan_artifacts(result, config, datetime(2026, 8, 30, 1, tzinfo=UTC))
    csv_path = run / "rename_intents.csv"
    rows = _read_rows(csv_path)
    rows[0]["old_path"] = "changed.txt"
    rows[0]["action"] = "rename"
    rows[0]["new_path"] = "x"
    _write_rows(csv_path, rows)
    with pytest.raises(PlanFormatError, match="程序字段被修改"):
        load_plan(csv_path)


def test_skipped_existing_duplicate_is_warning_not_error(app_config) -> None:
    config = replace(app_config, limits=replace(app_config.limits, filename_chars=False))
    raw = [TorrentFile(1, "same.txt", 1, 0.0, 1), TorrentFile(2, "same.txt", 2, 0.0, 1)]
    result = scan_files(SERVER, TORRENT, raw, config)
    run = write_scan_artifacts(result, config, datetime(2026, 8, 30, 2, tzinfo=UTC))
    csv_path = run / "rename_intents.csv"
    rows = _read_rows(csv_path)
    for row in rows:
        row["action"] = "skip"
    _write_rows(csv_path, rows)
    validated = validate_namespace(load_plan(csv_path), config)
    assert not validated.errors
    assert "EXISTING_SKIPPED_DUPLICATE" in {message.code for message in validated.warnings}
    assert compile_plan(validated) == ()


def test_snapshot_drift_compares_index_path_and_size(app_config) -> None:
    snapshot = {"files": [{"index": 0, "path": "a.txt", "size": 1}]}
    assert not compare_snapshot_to_current(snapshot, [TorrentFile(0, "a.txt", 1, 0.5, 1)])
    assert compare_snapshot_to_current(snapshot, [TorrentFile(0, "b.txt", 1, 0.5, 1)])[0].code == "SNAPSHOT_DRIFT"


def test_new_path_trailing_space_is_preserved_for_validation(app_config) -> None:
    config = replace(app_config, limits=replace(app_config.limits, directory_chars=2))
    result = scan_files(SERVER, TORRENT, [TorrentFile(0, "Long/file.txt", 1, 0.0, 1)], config)
    run = write_scan_artifacts(result, config, datetime(2026, 8, 30, 3, tzinfo=UTC))
    csv_path = run / "rename_intents.csv"
    rows = _read_rows(csv_path)
    rows[0]["action"] = "rename"
    rows[0]["new_path"] = "x "
    _write_rows(csv_path, rows)
    loaded = load_plan(csv_path)
    assert loaded.intents[0].new_path == "x "
    assert "WINDOWS_TRAILING_DOT_SPACE" in {message.code for message in validate_namespace(loaded, config).errors}
