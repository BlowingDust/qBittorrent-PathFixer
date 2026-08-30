import pytest

from qbittorrent_pathfixer.errors import ScanDataError
from qbittorrent_pathfixer.paths import normalize_internal_path, parent_directories, windows_absolute_path


def test_normalize_valid_path() -> None:
    assert normalize_internal_path("目录/sub/file.mkv") == "目录/sub/file.mkv"


@pytest.mark.parametrize("value", ["", "/a", "a/", "a//b", "a/../b", "a\\b"])
def test_reject_invalid_internal_path(value: str) -> None:
    with pytest.raises(ScanDataError):
        normalize_internal_path(value)


def test_parent_directories() -> None:
    assert parent_directories("a/b/c.txt") == ("a", "a/b")


def test_windows_absolute_path() -> None:
    assert windows_absolute_path(r"D:\Target", "a/b.txt") == r"D:\Target\a\b.txt"
