from pathlib import Path

import pytest

from qbittorrent_pathfixer.config import load_config
from qbittorrent_pathfixer.errors import ConfigError


def write_config(path: Path, extra: str = "") -> None:
    path.write_text(
        """schema_version = 1
[qbittorrent]
base_url = "http://example.test:8080"
torrent_hash = "0123456789abcdef0123456789abcdef01234567"
[limits]
filename_utf8_bytes = 220
[windows]
destination_root = "D:/Target"
[output]
root = "work"
""" + extra,
        encoding="utf-8",
    )


def test_load_config_normalizes_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(path)
    config = load_config(path)
    assert config.qbittorrent.base_url == "http://example.test:8080/"
    assert config.qbittorrent.torrent_hash == "0123456789abcdef0123456789abcdef01234567"
    assert config.output.root == tmp_path / "work"
    assert config.limits.filename_chars == 200
    assert config.execution.poll_interval_seconds == 1.0
    assert config.execution.operation_timeout_seconds == 60.0


def test_limit_can_be_disabled(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(path, "\n[limits.extra]\n")
    # Duplicate tables are intentionally invalid TOML; verify a real false value separately.
    text = path.read_text(encoding="utf-8").replace("filename_utf8_bytes = 220", "filename_utf8_bytes = false")
    text = text.replace("\n[limits.extra]\n", "")
    path.write_text(text, encoding="utf-8")
    assert load_config(path).limits.filename_utf8_bytes is False


def test_credentials_must_be_paired(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(path)
    text = path.read_text(encoding="utf-8").replace(
        'torrent_hash = "0123456789abcdef0123456789abcdef01234567"',
        'torrent_hash = "0123456789abcdef0123456789abcdef01234567"\nusername = "admin"',
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="同时配置"):
        load_config(path)


def test_destination_root_must_be_absolute(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_config(path)
    path.write_text(path.read_text(encoding="utf-8").replace("D:/Target", "Target"), encoding="utf-8")
    with pytest.raises(ConfigError, match="绝对路径"):
        load_config(path)
