import json

import httpx
import pytest

from qbittorrent_pathfixer.api import QBittorrentClient
from qbittorrent_pathfixer.config import QBittorrentConfig
from qbittorrent_pathfixer.errors import ApiCompatibilityError, AuthenticationError, ConnectionError


HASH = "0123456789abcdef0123456789abcdef01234567"


def config(**kwargs) -> QBittorrentConfig:
    values = {
        "base_url": "http://example.test/",
        "torrent_hash": HASH,
        "retry_network_errors": 0,
    }
    values.update(kwargs)
    return QBittorrentConfig(**values)


def response(request: httpx.Request, status: int = 200, text: str = "") -> httpx.Response:
    return httpx.Response(status, text=text, request=request)


def test_unauthenticated_connection_and_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/app/version"):
            return response(request, text="v4.6.7")
        if path.endswith("/app/webapiVersion"):
            return response(request, text="2.9.3")
        if path.endswith("/torrents/info"):
            return httpx.Response(200, json=[{"hash": HASH, "name": "demo"}], request=request)
        if path.endswith("/torrents/files"):
            return httpx.Response(200, json=[{"index": 0, "name": "a.txt", "size": 1, "progress": 0, "priority": 1}], request=request)
        raise AssertionError(path)

    with QBittorrentClient(config(), transport=httpx.MockTransport(handler)) as client:
        server = client.connect()
        torrent = client.get_torrent(HASH)
        files = client.get_files(HASH)
    assert server.authenticated is False
    assert torrent.name == "demo"
    assert files[0].path == "a.txt"


def test_authenticated_connection_logs_out() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            assert b"password=secret" in request.content
            return response(request, text="Ok.")
        if request.url.path.endswith("/auth/logout"):
            return response(request)
        if request.url.path.endswith("/app/version"):
            return response(request, text="v4.6.7")
        if request.url.path.endswith("/app/webapiVersion"):
            return response(request, text="2.9.3")
        raise AssertionError(request.url.path)

    with QBittorrentClient(config(username="admin", password_env="QBIT_PASSWORD"), "secret", httpx.MockTransport(handler)) as client:
        assert client.connect().authenticated is True
    assert any(path.endswith("/auth/logout") for path in calls)


def test_login_failure() -> None:
    transport = httpx.MockTransport(lambda request: response(request, status=403, text="Fails."))
    with QBittorrentClient(config(username="admin", password_env="PASS"), "wrong", transport) as client:
        with pytest.raises(AuthenticationError):
            client.connect()


def test_old_webapi_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        value = "v4.2.0" if request.url.path.endswith("/app/version") else "2.7.0"
        return response(request, text=value)

    with QBittorrentClient(config(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiCompatibilityError):
            client.connect()


def test_redirect_is_rejected() -> None:
    transport = httpx.MockTransport(lambda request: response(request, status=302))
    with QBittorrentClient(config(), transport=httpx.MockTransport(lambda request: response(request, status=302))) as client:
        with pytest.raises(ConnectionError, match="重定向"):
            client.connect()


def test_rename_and_index_filtered_read() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/torrents/renameFile"):
            assert b"oldPath=a.txt" in request.content
            assert b"newPath=b.txt" in request.content
            return response(request)
        if request.url.path.endswith("/torrents/files"):
            assert request.url.params["indexes"] == "3|7"
            return httpx.Response(200, json=[], request=request)
        raise AssertionError(request.url.path)

    with QBittorrentClient(config(), transport=httpx.MockTransport(handler)) as client:
        client.rename_file(HASH, "a.txt", "b.txt")
        client.get_files(HASH, [3, 7])
    assert len(calls) == 2
