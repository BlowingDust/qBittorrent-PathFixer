from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError
from .models import Limit


HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class QBittorrentConfig:
    base_url: str
    torrent_hash: str
    timeout_seconds: float = 15.0
    retry_network_errors: int = 2
    username: str | None = None
    password_env: str | None = None


@dataclass(frozen=True)
class LimitsConfig:
    filename_chars: Limit = 200
    filename_utf8_bytes: Limit = 220
    directory_chars: Limit = 200
    directory_utf8_bytes: Limit = 220
    relative_path_chars: Limit = 1000
    relative_path_utf8_bytes: Limit = 3800
    windows_absolute_path_chars: Limit = 240


@dataclass(frozen=True)
class WindowsConfig:
    destination_root: str
    case_sensitive: bool = False


@dataclass(frozen=True)
class OutputConfig:
    root: Path


@dataclass(frozen=True)
class ExecutionConfig:
    poll_interval_seconds: float = 1.0
    operation_timeout_seconds: float = 60.0
    settle_delay_seconds: float = 0.5


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    qbittorrent: QBittorrentConfig
    limits: LimitsConfig
    windows: WindowsConfig
    output: OutputConfig
    execution: ExecutionConfig = ExecutionConfig()

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output"]["root"] = str(self.output.root)
        return data

    def get_password(self) -> str | None:
        if self.qbittorrent.password_env is None:
            return None
        return os.environ.get(self.qbittorrent.password_env)


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"缺少或无效的 [{key}] 配置段")
    return value


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("qbittorrent.base_url 必须是非空字符串")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("qbittorrent.base_url 必须是包含主机的 HTTP 或 HTTPS URL")
    if parsed.query or parsed.fragment:
        raise ConfigError("qbittorrent.base_url 不得包含查询参数或片段")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return float(value)


def _limit(table: dict[str, Any], name: str, default: int) -> Limit:
    value = table.get(name, default)
    if value is False:
        return False
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"limits.{name} 必须是正整数或 false")
    return value


def load_config(path: Path) -> AppConfig:
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML 格式错误：{exc}") from exc

    if raw.get("schema_version") != 1:
        raise ConfigError("仅支持 schema_version = 1")

    qb = _table(raw, "qbittorrent")
    torrent_hash = qb.get("torrent_hash")
    if not isinstance(torrent_hash, str) or not HASH_RE.fullmatch(torrent_hash):
        raise ConfigError("qbittorrent.torrent_hash 必须是 40 位十六进制 v1 哈希")
    username = qb.get("username")
    password_env = qb.get("password_env")
    if username is not None and (not isinstance(username, str) or not username):
        raise ConfigError("qbittorrent.username 必须是非空字符串")
    if password_env is not None and (not isinstance(password_env, str) or not password_env):
        raise ConfigError("qbittorrent.password_env 必须是非空字符串")
    if (username is None) != (password_env is None):
        raise ConfigError("qbittorrent.username 与 password_env 必须同时配置或同时省略")
    retries = qb.get("retry_network_errors", 2)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ConfigError("qbittorrent.retry_network_errors 必须是非负整数")

    limits_raw = _table(raw, "limits")
    windows_raw = _table(raw, "windows")
    destination_root = windows_raw.get("destination_root")
    if not isinstance(destination_root, str) or not destination_root.strip():
        raise ConfigError("windows.destination_root 必须是 Windows 绝对路径")
    win_path = PureWindowsPath(destination_root)
    if not win_path.is_absolute():
        raise ConfigError("windows.destination_root 必须是 Windows 绝对路径或 UNC 路径")
    case_sensitive = windows_raw.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ConfigError("windows.case_sensitive 必须是 true 或 false")

    output_raw = _table(raw, "output")
    output_value = output_raw.get("root", "work")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ConfigError("output.root 必须是非空路径字符串")
    output_path = Path(output_value)
    if not output_path.is_absolute():
        output_path = path.resolve().parent / output_path

    execution_raw = raw.get("execution", {})
    if not isinstance(execution_raw, dict):
        raise ConfigError("[execution] 配置段必须是表")

    return AppConfig(
        schema_version=1,
        qbittorrent=QBittorrentConfig(
            base_url=_normalize_base_url(qb.get("base_url")),
            torrent_hash=torrent_hash.lower(),
            timeout_seconds=_positive_number(qb.get("timeout_seconds", 15.0), "qbittorrent.timeout_seconds"),
            retry_network_errors=retries,
            username=username,
            password_env=password_env,
        ),
        limits=LimitsConfig(
            filename_chars=_limit(limits_raw, "filename_chars", 200),
            filename_utf8_bytes=_limit(limits_raw, "filename_utf8_bytes", 220),
            directory_chars=_limit(limits_raw, "directory_chars", 200),
            directory_utf8_bytes=_limit(limits_raw, "directory_utf8_bytes", 220),
            relative_path_chars=_limit(limits_raw, "relative_path_chars", 1000),
            relative_path_utf8_bytes=_limit(limits_raw, "relative_path_utf8_bytes", 3800),
            windows_absolute_path_chars=_limit(limits_raw, "windows_absolute_path_chars", 240),
        ),
        windows=WindowsConfig(destination_root=str(win_path), case_sensitive=case_sensitive),
        output=OutputConfig(root=output_path),
        execution=ExecutionConfig(
            poll_interval_seconds=_positive_number(execution_raw.get("poll_interval_seconds", 1.0), "execution.poll_interval_seconds"),
            operation_timeout_seconds=_positive_number(execution_raw.get("operation_timeout_seconds", 60.0), "execution.operation_timeout_seconds"),
            settle_delay_seconds=_positive_number(execution_raw.get("settle_delay_seconds", 0.5), "execution.settle_delay_seconds"),
        ),
    )
