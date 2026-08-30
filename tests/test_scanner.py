import pytest

from qbittorrent_pathfixer.errors import ScanDataError
from qbittorrent_pathfixer.models import ServerInfo, TorrentFile, TorrentInfo
from qbittorrent_pathfixer.scanner import scan_files


SERVER = ServerInfo("v4.6.7", "2.9.3", "http://localhost/", False)
TORRENT = TorrentInfo("0123456789abcdef0123456789abcdef01234567", "test")


def torrent_file(index: int, path: str) -> TorrentFile:
    return TorrentFile(index=index, path=path, size=10, progress=0.5, priority=1)


def test_scanner_infers_unique_directories(app_config) -> None:
    result = scan_files(
        SERVER,
        TORRENT,
        [torrent_file(1, "a/b/one.txt"), torrent_file(0, "a/two.txt")],
        app_config,
    )
    assert [entry.path for entry in result.files] == ["a/two.txt", "a/b/one.txt"]
    assert [entry.path for entry in result.directories] == ["a", "a/b"]


def test_problem_directory_is_not_repeated_on_children(app_config) -> None:
    long_dir = "中" * 100
    result = scan_files(SERVER, TORRENT, [torrent_file(0, f"{long_dir}/ok.txt")], app_config)
    assert {issue.code for issue in result.directories[0].issues} >= {"DIR_NAME_UTF8_BYTES_EXCEEDED"}
    assert "FILE_NAME_UTF8_BYTES_EXCEEDED" not in {issue.code for issue in result.files[0].issues}


def test_case_collision_marks_both_files(app_config) -> None:
    result = scan_files(SERVER, TORRENT, [torrent_file(0, "A.txt"), torrent_file(1, "a.TXT")], app_config)
    assert all("WINDOWS_CASE_COLLISION" in {issue.code for issue in entry.issues} for entry in result.files)


def test_duplicate_index_is_rejected(app_config) -> None:
    with pytest.raises(ScanDataError, match="重复 index"):
        scan_files(SERVER, TORRENT, [torrent_file(0, "a"), torrent_file(0, "b")], app_config)


def test_duplicate_paths_are_preserved_and_marked(app_config) -> None:
    result = scan_files(SERVER, TORRENT, [torrent_file(1, "same.txt"), torrent_file(2, "same.txt")], app_config)
    assert len(result.files) == 2
    assert [entry.file_index for entry in result.files] == [1, 2]
    assert all("DUPLICATE_INTERNAL_PATH" in {issue.code for issue in entry.issues} for entry in result.files)
