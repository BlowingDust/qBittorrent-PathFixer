from pathlib import Path

import pytest

from qbittorrent_pathfixer.config import (
    AppConfig,
    LimitsConfig,
    OutputConfig,
    QBittorrentConfig,
    WindowsConfig,
)


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        schema_version=1,
        qbittorrent=QBittorrentConfig(
            base_url="http://127.0.0.1:8080/",
            torrent_hash="0123456789abcdef0123456789abcdef01234567",
            retry_network_errors=0,
        ),
        limits=LimitsConfig(),
        windows=WindowsConfig(destination_root=r"D:\Downloads\Target"),
        output=OutputConfig(root=tmp_path / "work"),
    )
