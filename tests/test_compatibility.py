from qbittorrent_pathfixer.compatibility import calculate_metrics, inspect_entry, inspect_windows_name, windows_key
from qbittorrent_pathfixer.config import LimitsConfig, WindowsConfig


def codes(issues: list) -> set[str]:
    return {issue.code for issue in issues}


def test_unicode_metrics_count_codepoints_and_utf8_bytes() -> None:
    metrics = calculate_metrics("目录/文件😀.txt", WindowsConfig(destination_root=r"D:\T"))
    assert metrics.name_chars == len("文件😀.txt")
    assert metrics.name_utf8_bytes == len("文件😀.txt".encode("utf-8"))
    assert metrics.relative_path_utf8_bytes == len("目录/文件😀.txt".encode("utf-8"))


def test_name_and_path_limits_are_independent() -> None:
    limits = LimitsConfig(filename_chars=3, filename_utf8_bytes=False, relative_path_chars=5)
    metrics = calculate_metrics("dir/long.txt", WindowsConfig(destination_root=r"D:\T"))
    found = codes(inspect_entry("dir/long.txt", "file", metrics, limits))
    assert "FILE_NAME_CHARS_EXCEEDED" in found
    assert "FILE_NAME_UTF8_BYTES_EXCEEDED" not in found
    assert "REL_PATH_CHARS_EXCEEDED" in found


def test_windows_invalid_and_reserved_names() -> None:
    assert "WINDOWS_INVALID_CHAR" in codes(inspect_windows_name("bad?.txt"))
    assert "WINDOWS_TRAILING_DOT_SPACE" in codes(inspect_windows_name("bad. "))
    assert "WINDOWS_RESERVED_NAME" in codes(inspect_windows_name("CON.txt"))


def test_windows_key_normalizes_case_unicode_and_trailing_dot() -> None:
    assert windows_key("A/É.txt") == windows_key("a/E\u0301.TXT.")
