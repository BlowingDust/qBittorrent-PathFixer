from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__
from .api import QBittorrentClient
from .config import AppConfig, load_config
from .errors import (
    ApiCompatibilityError,
    AuthenticationError,
    BtFileRenameError,
    ConfigError,
    ConnectionError,
    ExecutionFailedError,
    ExecutionUnknownError,
    ScanDataError,
    TorrentNotFoundError,
    PlanFormatError,
    ValidationFailedError,
)
from .planner import compile_plan
from .records import create_execution_artifacts, write_scan_artifacts, write_validation_artifacts
from .executor import execute_operations
from .scanner import scan_files
from .validator import compare_snapshot_to_current, load_plan, validate_namespace


EXIT_CODES = {
    ConfigError: 2,
    AuthenticationError: 3,
    ConnectionError: 3,
    TorrentNotFoundError: 4,
    ApiCompatibilityError: 4,
    ScanDataError: 5,
    PlanFormatError: 6,
    ValidationFailedError: 7,
    ExecutionFailedError: 9,
    ExecutionUnknownError: 10,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qbittorrent-pathfixer",
        description="通过 qBittorrent Web API 只读扫描种子路径问题。",
    )
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="TOML 配置文件")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connection-test", help="测试连接、API 兼容性和目标种子")
    subparsers.add_parser("scan", help="执行只读扫描并生成 JSON/CSV")
    validate_parser = subparsers.add_parser("validate", help="校验人工编辑 CSV 并生成只读操作计划")
    validate_parser.add_argument("--plan", required=True, type=Path, help="人工编辑后的 rename_intents.csv")
    apply_parser = subparsers.add_parser("apply", help="预演或执行重命名计划")
    apply_parser.add_argument("--plan", required=True, type=Path, help="人工编辑后的 rename_intents.csv")
    apply_parser.add_argument("--dry-run", action="store_true", help="只校验和生成计划，不调用改名 API")
    return parser


def _password(config: AppConfig) -> str | None:
    qb = config.qbittorrent
    if qb.username is None:
        return None
    password = config.get_password()
    if password is not None:
        return password
    if not sys.stdin.isatty():
        raise ConfigError(f"环境变量 {qb.password_env} 未设置，且当前不是交互终端")
    return getpass.getpass(f"qBittorrent 用户 {qb.username} 的密码：")


def _warn_http(config: AppConfig) -> None:
    if config.qbittorrent.base_url.lower().startswith("http://"):
        print("警告：WebUI 使用未加密 HTTP，请确保当前 Windows 与服务器之间的网络可信。", file=sys.stderr)


def _connection_test(config: AppConfig) -> int:
    _warn_http(config)
    with QBittorrentClient(config.qbittorrent, _password(config)) as client:
        server = client.connect()
        torrent = client.get_torrent(config.qbittorrent.torrent_hash)
        files = client.get_files(config.qbittorrent.torrent_hash)
    print(f"连接成功：{server.base_url}")
    print(f"qBittorrent：{server.app_version}")
    print(f"Web API：{server.webapi_version}")
    print(f"认证方式：{'用户名/密码' if server.authenticated else '免认证/IP 白名单'}")
    print(f"目标种子：{torrent.name}")
    print(f"信息哈希：{torrent.hash}")
    print(f"文件数量：{len(files)}")
    return 0


def _scan(config: AppConfig) -> int:
    _warn_http(config)
    with QBittorrentClient(config.qbittorrent, _password(config)) as client:
        server = client.connect()
        torrent = client.get_torrent(config.qbittorrent.torrent_hash)
        files = client.get_files(config.qbittorrent.torrent_hash)
    result = scan_files(server, torrent, files, config)
    run_dir = write_scan_artifacts(result, config)
    problem_files = sum(bool(entry.issues) for entry in result.files)
    problem_directories = sum(bool(entry.issues) for entry in result.directories)
    print(f"扫描完成：{torrent.name}")
    print(f"文件：{len(result.files)}，目录：{len(result.directories)}")
    print(f"问题文件：{problem_files}，问题目录：{problem_directories}")
    print(f"输出目录：{run_dir}")
    return 0


def _validate(config: AppConfig, plan_path: Path, dry_run: bool) -> int:
    _warn_http(config)
    loaded = load_plan(plan_path.resolve())
    snapshot_hash = str(loaded.snapshot.get("torrent", {}).get("hash", "")).lower()
    if snapshot_hash != config.qbittorrent.torrent_hash:
        raise PlanFormatError("CSV 快照的种子哈希与当前配置不一致")
    with QBittorrentClient(config.qbittorrent, _password(config)) as client:
        server = client.connect()
        torrent = client.get_torrent(config.qbittorrent.torrent_hash)
        current_files = client.get_files(config.qbittorrent.torrent_hash)
    drift_messages = compare_snapshot_to_current(loaded.snapshot, current_files)
    validated = validate_namespace(loaded, config)
    messages = (*drift_messages, *validated.messages)
    has_errors = any(message.severity == "error" for message in messages)
    operations = () if has_errors else compile_plan(validated)
    run_dir = write_validation_artifacts(
        config=config,
        torrent_hash=torrent.hash,
        source_csv=loaded.csv_path,
        snapshot_path=loaded.snapshot_path,
        messages=messages,
        operations=operations,
        current_file_count=len(current_files),
    )
    errors = sum(message.severity == "error" for message in messages)
    warnings = sum(message.severity == "warning" for message in messages)
    label = "Dry-run" if dry_run else "校验"
    print(f"{label}完成：{torrent.name}")
    print(f"错误：{errors}，警告：{warnings}，计划操作：{len(operations)}")
    print(f"输出目录：{run_dir}")
    if errors:
        raise ValidationFailedError(f"存在 {errors} 个阻断错误；详细信息见 {run_dir / 'validation_report.json'}")
    return 0


def _apply(config: AppConfig, plan_path: Path) -> int:
    _warn_http(config)
    loaded = load_plan(plan_path.resolve())
    snapshot_hash = str(loaded.snapshot.get("torrent", {}).get("hash", "")).lower()
    if snapshot_hash != config.qbittorrent.torrent_hash:
        raise PlanFormatError("CSV 快照的种子哈希与当前配置不一致")
    with QBittorrentClient(config.qbittorrent, _password(config)) as client:
        client.connect()
        torrent = client.get_torrent(config.qbittorrent.torrent_hash)
        current_files = client.get_files(config.qbittorrent.torrent_hash)
        validated = validate_namespace(loaded, config)
        messages = (*compare_snapshot_to_current(loaded.snapshot, current_files), *validated.messages)
        if any(message.severity == "error" for message in messages):
            raise ValidationFailedError("执行前校验存在阻断错误，请先重新运行 validate")
        operations = compile_plan(validated)
        expected_confirmation = f"RENAME {torrent.hash[:12]}"
        print(f"目标种子：{torrent.name}")
        print(f"计划操作：{len(operations)}（文件夹 {sum(op.kind == 'rename_folder' for op in operations)}，文件 {sum(op.kind == 'rename_file' for op in operations)}）")
        print("警告：接下来会实际修改 qBittorrent 中的文件和目录路径。")
        if not sys.stdin.isatty():
            raise ConfigError("实际执行必须在交互终端中进行")
        confirmation = input(f"请输入 {expected_confirmation} 以确认：").strip()
        if confirmation != expected_confirmation:
            raise ConfigError("确认文本不匹配，未执行任何重命名")
        run_dir = create_execution_artifacts(
            config, torrent.hash, loaded.csv_path, loaded.snapshot_path, operations, current_files
        )
        print(f"执行记录：{run_dir}")
        execute_operations(
            client, torrent.hash, operations, current_files, config.execution, run_dir / "execution_state.json"
        )
        after_files = client.get_files(torrent.hash)
        from .records import write_json
        write_json(run_dir / "after_files.json", {
            "schema_version": 1,
            "torrent_hash": torrent.hash,
            "files": [vars(item) for item in after_files],
        })
    print(f"执行完成：{len(operations)} 项均已异步确认")
    print(f"输出目录：{run_dir}")
    return 0


def _exit_code(error: BtFileRenameError) -> int:
    for error_type, code in EXIT_CODES.items():
        if isinstance(error, error_type):
            return code
    return 12


def main(argv: list[str] | None = None) -> int:
    # Modern Windows terminals understand UTF-8, while the inherited code page
    # may not be able to represent Chinese diagnostics.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "connection-test":
            return _connection_test(config)
        if args.command == "scan":
            return _scan(config)
        if args.command == "validate":
            return _validate(config, args.plan, dry_run=False)
        if args.command == "apply":
            return _validate(config, args.plan, dry_run=True) if args.dry_run else _apply(config, args.plan)
        parser.error(f"未知命令：{args.command}")
    except BtFileRenameError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return _exit_code(exc)
    except KeyboardInterrupt:
        print("已由用户取消。", file=sys.stderr)
        return 130
    return 12
