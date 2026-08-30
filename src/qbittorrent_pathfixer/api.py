from __future__ import annotations

import time
from typing import Any

import httpx

from .config import QBittorrentConfig
from .errors import ApiCompatibilityError, AuthenticationError, ConnectionError, ExecutionFailedError, ScanDataError, TorrentNotFoundError
from .models import ServerInfo, TorrentFile, TorrentInfo


MIN_PATH_API = (2, 8, 0)


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.strip().lstrip("v").split("."))
    except ValueError as exc:
        raise ApiCompatibilityError(f"无法解析 Web API 版本：{value!r}") from exc


class QBittorrentClient:
    def __init__(self, config: QBittorrentConfig, password: str | None = None, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.password = password
        self._authenticated = False
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "qbittorrent-pathfixer/0.1.0"},
            transport=transport,
        )

    def __enter__(self) -> "QBittorrentClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, endpoint: str, *, retry: bool = False, **kwargs: Any) -> httpx.Response:
        attempts = self.config.retry_network_errors + 1 if retry else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(method, endpoint, **kwargs)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(0.2 * (2**attempt), 1.0))
                    continue
                raise ConnectionError(f"连接 qBittorrent 失败：{exc.__class__.__name__}") from exc
            if 300 <= response.status_code < 400:
                raise ConnectionError(f"WebUI 返回不允许的重定向：HTTP {response.status_code}")
            return response
        raise ConnectionError(f"连接 qBittorrent 失败：{last_error}")

    def connect(self) -> ServerInfo:
        if self.config.username is not None:
            if self.password is None:
                raise AuthenticationError("已配置用户名，但没有提供密码")
            response = self._request(
                "POST",
                "api/v2/auth/login",
                data={"username": self.config.username, "password": self.password},
            )
            if response.status_code != 200 or response.text.strip() != "Ok.":
                raise AuthenticationError("qBittorrent WebUI 登录失败")
            self._authenticated = True

        version_response = self._request("GET", "api/v2/app/version", retry=True)
        api_response = self._request("GET", "api/v2/app/webapiVersion", retry=True)
        for response, label in ((version_response, "应用版本"), (api_response, "Web API 版本")):
            if response.status_code in {401, 403}:
                raise AuthenticationError(f"读取{label}被拒绝，请配置认证信息或检查 IP 白名单")
            if response.status_code != 200:
                raise ConnectionError(f"读取{label}失败：HTTP {response.status_code}")
        api_version = api_response.text.strip()
        parsed = _version_tuple(api_version)
        if parsed + (0,) * (3 - len(parsed)) < MIN_PATH_API:
            raise ApiCompatibilityError(f"Web API {api_version} 不支持路径式文件/目录改名，需要至少 2.8.0")
        return ServerInfo(
            app_version=version_response.text.strip(),
            webapi_version=api_version,
            base_url=self.config.base_url,
            authenticated=self._authenticated,
        )

    def get_torrent(self, torrent_hash: str) -> TorrentInfo:
        response = self._request("GET", "api/v2/torrents/info", params={"hashes": torrent_hash}, retry=True)
        if response.status_code != 200:
            raise ConnectionError(f"读取目标种子失败：HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ScanDataError("目标种子 API 返回的不是有效 JSON") from exc
        if not isinstance(data, list) or not data:
            raise TorrentNotFoundError(f"找不到目标种子：{torrent_hash}")
        exact = [item for item in data if isinstance(item, dict) and str(item.get("hash", "")).lower() == torrent_hash.lower()]
        if len(exact) != 1:
            raise ScanDataError("目标种子 API 未返回唯一的精确哈希结果")
        item = exact[0]
        return TorrentInfo(
            hash=torrent_hash.lower(),
            name=str(item.get("name", "")),
            save_path=item.get("save_path"),
            content_path=item.get("content_path"),
        )

    def get_files(self, torrent_hash: str, indexes: list[int] | tuple[int, ...] | None = None) -> list[TorrentFile]:
        params = {"hash": torrent_hash}
        if indexes is not None:
            params["indexes"] = "|".join(str(index) for index in indexes)
        response = self._request("GET", "api/v2/torrents/files", params=params, retry=True)
        if response.status_code == 404:
            raise TorrentNotFoundError(f"找不到目标种子：{torrent_hash}")
        if response.status_code != 200:
            raise ConnectionError(f"读取种子文件列表失败：HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ScanDataError("文件列表 API 返回的不是有效 JSON") from exc
        if not isinstance(data, list):
            raise ScanDataError("文件列表 API 返回值必须是数组")
        files: list[TorrentFile] = []
        try:
            for item in data:
                files.append(
                    TorrentFile(
                        index=int(item["index"]),
                        path=str(item["name"]),
                        size=int(item["size"]),
                        progress=float(item["progress"]),
                        priority=int(item["priority"]),
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise ScanDataError("文件列表包含缺失或无效字段") from exc
        return files

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self._rename("renameFile", torrent_hash, old_path, new_path)

    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self._rename("renameFolder", torrent_hash, old_path, new_path)

    def _rename(self, action: str, torrent_hash: str, old_path: str, new_path: str) -> None:
        response = self._request(
            "POST",
            f"api/v2/torrents/{action}",
            data={"hash": torrent_hash, "oldPath": old_path, "newPath": new_path},
        )
        if response.status_code in {401, 403}:
            raise AuthenticationError("qBittorrent 拒绝重命名请求")
        if response.status_code != 200:
            detail = response.text.strip()[:500]
            suffix = f"：{detail}" if detail else ""
            raise ExecutionFailedError(f"重命名请求失败：HTTP {response.status_code}{suffix}")

    def close(self) -> None:
        if self._authenticated:
            try:
                self._request("POST", "api/v2/auth/logout")
            except ConnectionError:
                pass
        self._client.close()
