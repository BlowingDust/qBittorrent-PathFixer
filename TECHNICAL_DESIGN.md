# qBittorrent PathFixer 技术设计

## 1. 文档信息

- 对应需求文档：[REQUIREMENTS.md](REQUIREMENTS.md)
- 目标客户端平台：Windows 10/11
- 目标 Python：3.11 至 3.14
- 目标服务端：qBittorrent v4.6.7（64-bit）
- 目标种子 v1 信息哈希：由配置文件提供
- WebUI 地址：由配置文件提供，可使用 HTTP
- 文档状态：初版技术设计

## 2. 设计目标

本设计将工具分成五个相互隔离的步骤：

1. 连接并读取 qBittorrent 状态；
2. 扫描完整种子结构并保存不可变快照；
3. 导出和读取用户编辑的 CSV；
4. 将用户最终意图校验并编译为有序 API 操作；
5. 经明确确认后执行，并重新读取服务端状态进行验证。

核心安全原则如下：

- 默认只读；
- CSV 只表达最终意图，不直接代表 API 调用；
- 执行前使用完整快照展开文件夹操作的全部影响；
- 任意写操作失败后立即停止；
- 所有已发送和未发送的操作都有持久化状态；
- 不在日志、快照或计划文件中保存密码及会话 Cookie。

## 3. 技术选型

### 3.1 运行时与依赖

- Python 3.11–3.14；
- `httpx`：HTTP 会话、Cookie、超时和可测试的传输层；
- `tomllib`：读取 TOML，Python 3.11 起属于标准库；
- `argparse`：命令行解析，避免首版引入大型 CLI 框架；
- `dataclasses`、`pathlib`、`csv`、`json`、`hashlib`、`logging`：标准库；
- `pytest`：自动化测试；
- `httpx.MockTransport`：API 单元测试，不需要真实 qBittorrent。

运行依赖与开发依赖在 `pyproject.toml` 中分组管理。项目不依赖当前 `venv` 中预装的第三方包；虚拟环境可以随时由项目元数据重建。

### 3.2 不使用官方之外的 qBittorrent 客户端封装

首版直接封装所需的少量 Web API 端点，不采用第三方 qBittorrent SDK，原因是：

- 所需端点数量少；
- 需要精确控制免认证、重定向、超时、错误分类和日志脱敏；
- 路径参数及 Web API 版本兼容判断需要由本项目明确负责；
- 可以降低 Python 3.14 兼容性风险。

## 4. 项目结构

计划采用 `src` 布局：

```text
qbittorrent-pathfixer/
├── pyproject.toml
├── README.md
├── REQUIREMENTS.md
├── TECHNICAL_DESIGN.md
├── config.example.toml
├── src/
│   └── qbittorrent_pathfixer/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── config.py
│       ├── errors.py
│       ├── models.py
│       ├── paths.py
│       ├── compatibility.py
│       ├── api.py
│       ├── scanner.py
│       ├── records.py
│       ├── validator.py
│       ├── planner.py
│       ├── executor.py
│       ├── verifier.py
│       └── logging_setup.py
└── tests/
    ├── fixtures/
    ├── test_config.py
    ├── test_paths.py
    ├── test_compatibility.py
    ├── test_api.py
    ├── test_scanner.py
    ├── test_records.py
    ├── test_validator.py
    ├── test_planner.py
    ├── test_executor.py
    └── test_verifier.py
```

模块职责：

| 模块 | 职责 |
|---|---|
| `cli.py` | 子命令、参数、退出码和终端摘要 |
| `config.py` | TOML、环境变量和配置校验 |
| `models.py` | 不含 I/O 的领域数据模型 |
| `paths.py` | qBittorrent 相对路径规范化、分解、拼接和前缀替换 |
| `compatibility.py` | 长度统计和 Windows 名称规则 |
| `api.py` | Web API 调用、会话、错误映射和版本检查 |
| `scanner.py` | 从文件列表推导目录、检测问题和生成快照 |
| `records.py` | JSON/CSV 的确定性读写和架构版本检查 |
| `validator.py` | 用户意图、快照漂移、最终命名空间和限制校验 |
| `planner.py` | 最终路径推导、操作合并、排序及临时路径 |
| `executor.py` | 确认、逐项写请求、状态落盘和失败即停 |
| `verifier.py` | 执行后重新读取并比较实际状态 |

依赖方向为：CLI → 用例模块 → API/记录层 → 数据模型。`models.py`、`paths.py` 和 `compatibility.py` 不依赖网络或文件系统，以便进行纯单元测试。

## 5. 配置设计

### 5.1 配置文件

使用 TOML。首版每份配置操作一个种子；复制配置文件即可处理另一个种子。

```toml
schema_version = 1

[qbittorrent]
base_url = "http://qbittorrent.example.test:8080/"
torrent_hash = "0123456789abcdef0123456789abcdef01234567"
timeout_seconds = 15.0
retry_network_errors = 2
# username = "admin"              # 可选
# password_env = "QBIT_PASSWORD"  # 可选，不保存密码本身

[limits]
filename_chars = 200
filename_utf8_bytes = 220
directory_chars = 200
directory_utf8_bytes = 220
relative_path_chars = 1000
relative_path_utf8_bytes = 3800
windows_absolute_path_chars = 240

[windows]
destination_root = "D:/Downloads/TargetTorrent"
case_sensitive = false

[output]
root = "work"
```

### 5.2 配置规则

- `schema_version` 必须为程序支持的版本；
- `base_url` 只允许 `http` 或 `https`，必须包含主机，可包含路径前缀；保存时规范化为单个尾随 `/`；
- 当前场景明确使用 HTTP。程序应在连接摘要中提示“HTTP 未加密”，但不阻止使用；
- `torrent_hash` 转换为小写，并严格匹配 40 位十六进制 v1 哈希；数据模型预留未来支持 64 位 v2 哈希的空间；
- `timeout_seconds` 必须大于 0；
- `retry_network_errors` 默认为 2，只适用于确认请求未到达或无业务响应的连接类错误；写请求超时属于“结果未知”，不得自动重发；
- `username` 与 `password_env` 必须同时出现或同时缺省；
- 未配置认证时，不调用登录端点，直接调用只读版本端点测试访问；
- 任一限制可用 TOML 中的 `false` 关闭，不能用 `0` 表示关闭；整数限制必须大于 0；
- `destination_root` 必填，解析为 Windows 绝对路径；允许盘符路径和 UNC 路径；
- 输出根目录相对于配置文件所在目录解析，绝对路径保持不变。

### 5.3 密码处理

配置中只保存环境变量名称，例如 `QBIT_PASSWORD`。如果设置了用户名但环境变量不存在，命令行在交互终端使用 `getpass` 提示；非交互环境直接报错。密码只存在于内存，不写入日志和异常文本。

## 6. qBittorrent Web API 设计

### 6.1 使用的端点

| 功能 | 方法与端点 | 参数 |
|---|---|---|
| 登录 | `POST api/v2/auth/login` | `username`, `password` |
| 注销 | `POST api/v2/auth/logout` | 无 |
| 应用版本 | `GET api/v2/app/version` | 无 |
| Web API 版本 | `GET api/v2/app/webapiVersion` | 无 |
| 查询目标种子 | `GET api/v2/torrents/info` | `hashes` |
| 获取文件列表 | `GET api/v2/torrents/files` | `hash` |
| 文件改名/移动 | `POST api/v2/torrents/renameFile` | `hash`, `oldPath`, `newPath` |
| 文件夹改名/移动 | `POST api/v2/torrents/renameFolder` | `hash`, `oldPath`, `newPath` |

qBittorrent 4.6.7 对应的新式改名接口使用 `oldPath` 和 `newPath`，但程序仍必须读取 Web API 版本。路径式 `renameFile` 和 `renameFolder` 要求 Web API 至少具备 2.8 系列能力；不满足时连接检查失败，不尝试旧式文件 ID 接口。

参考：[qBittorrent WebUI API 官方文档](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-4.1%29)

### 6.2 客户端接口

```python
class QBittorrentClient:
    def connect(self) -> ServerInfo: ...
    def close(self) -> None: ...
    def get_torrent(self, torrent_hash: str) -> TorrentInfo: ...
    def get_files(self, torrent_hash: str) -> list[TorrentFile]: ...
    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None: ...
    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None: ...
```

`connect()` 的步骤：

1. 创建固定 `base_url` 的客户端，关闭自动重定向；
2. 如果配置了凭据，调用登录；响应正文必须是成功标志；
3. 调用版本端点验证访问权限；
4. 解析应用版本和 Web API 版本；
5. 检查 API 能力；
6. 返回不含认证信息的 `ServerInfo`。

### 6.3 HTTP 安全约束

- `follow_redirects=False`，任何 3xx 都作为错误，防止认证数据被转发到其他主机；
- 所有参数使用表单或查询参数编码，不手工拼接；
- qBittorrent 内部路径始终使用 `/`；
- 设置稳定的 `User-Agent`；
- 只记录方法、端点名、状态码和耗时，不记录 Cookie、密码或完整表单；
- 连接超时、读取超时和 HTTP/业务错误映射为不同异常类型；
- GET 请求的临时网络错误可有限重试；
- POST 改名请求只有在明确获知请求尚未发送时才可重试。发生读取超时或连接中断时将结果标记为 `unknown` 并立即停止，随后由只读验证判断实际状态。

### 6.4 API 响应处理

- 登录失败：认证错误；
- `403`：访问被拒绝或主机策略问题；
- `404`：端点或种子不存在；
- 改名 `409`：旧路径、新路径或目标占用错误；
- 非预期 HTML 响应：通常表示反向代理、登录页或地址错误，应截断并脱敏后报告内容类型；
- `torrents/info` 返回空列表：目标种子不存在；
- 返回多个种子：视为服务端异常，不能任选其一；
- 文件列表中的 `name` 是唯一可信的种子内部相对路径来源。

## 7. 领域数据模型

模型使用冻结的 `dataclass` 或等价不可变类型。时间统一保存为带 `Z` 的 UTC ISO 8601 字符串。

### 7.1 核心模型

```python
ServerInfo(
    app_version: str,
    webapi_version: str,
    base_url: str,
    authenticated: bool,
)

TorrentInfo(
    hash: str,
    name: str,
    save_path: str | None,
    content_path: str | None,
)

TorrentFile(
    index: int,
    path: str,
    size: int,
    progress: float,
    priority: int,
)

DirectoryEntry(
    path: str,
)

PathMetrics(
    name_chars: int,
    name_utf8_bytes: int,
    relative_path_chars: int,
    relative_path_utf8_bytes: int,
    windows_absolute_path_chars: int,
)

Issue(
    code: str,
    severity: str,
    component: str | None,
    actual: int | str | None,
    limit: int | None,
    message: str,
)
```

### 7.2 用户意图模型

```python
RenameIntent(
    record_id: str,
    item_type: Literal["file", "folder"],
    old_path: str,
    new_path: str,
    action: Literal["rename", "skip", ""],
    note: str,
)
```

### 7.3 执行操作模型

```python
RenameOperation(
    operation_id: str,
    kind: Literal["rename_file", "rename_folder"],
    source_path: str,
    target_path: str,
    temporary: bool,
    reason_record_ids: tuple[str, ...],
    status: Literal[
        "pending", "running", "success", "failed", "unknown", "not_run"
    ],
)
```

`source_path` 是该操作执行时预期的当前路径，不一定等于 CSV 的 `old_path`。执行器不会直接把 CSV 行转换成请求。

## 8. 路径表示与度量

### 8.1 内部路径规范

- 种子内部路径采用规范化 POSIX 相对路径，例如 `dir/sub/file.mkv`；
- 不允许开头 `/`、盘符、UNC 前缀、空路径段、`.`、`..` 或反斜杠；
- 不使用 `Path.resolve()`，因为种子路径不是客户端本地文件系统路径；
- 使用 `PurePosixPath` 辅助分段，但在构造前先做严格字符串校验；
- 不做 Unicode NFC/NFD 自动归一化，避免未经用户允许改变名称；冲突检测额外使用 NFC 归一化键发现跨平台风险；
- API 返回的原始路径在快照中保留一份，规范化失败时扫描中止，不静默修复。

### 8.2 字符数

字符数定义为 Python `len(text)`，即 Unicode 码点数量。它不是用户视觉上的字形簇数量。例如某些组合字符可能显示为一个字形但计为多个码点。报告明确标注为 `chars/code points`，避免误解。

### 8.3 UTF-8 字节数

```python
len(text.encode("utf-8", errors="strict"))
```

编码失败属于数据错误。名称限制针对末级名称，路径限制针对使用 `/` 连接后的完整种子相对路径。

### 8.4 Windows 绝对路径字符数

1. 将配置的 Windows 根目录规范化，但不访问本地磁盘；
2. 将种子相对路径的 `/` 转为 `\`；
3. 以一个 `\` 连接根目录和相对路径；
4. 对完整字符串使用 `len()`；
5. UNC 前缀和盘符本身计入长度；不添加 `\\?\` 前缀；
6. 默认阈值 240，给复制工具和环境差异保留余量。

### 8.5 名称与路径限制

每一级目录组件独立应用目录名限制；文件末级组件应用文件名限制；完整相对路径和 Windows 绝对路径另行应用各自限制。一个项目可以同时产生多条问题。

## 9. Windows 兼容性检测

对文件名和每一级目录名检测：

- 控制字符 U+0000–U+001F；
- `< > : " / \\ | ? *`；
- 末尾空格或句点；
- 空名称、`.`、`..`；
- 去掉末尾空格/句点并取第一个句点前的主体后，匹配 `CON`、`PRN`、`AUX`、`NUL`、`COM1`–`COM9`、`LPT1`–`LPT9`，不区分大小写；
- 使用 `casefold()` 的大小写不敏感冲突；
- 使用 NFC 后再 `casefold()` 的归一化冲突风险。

Windows 兼容性键定义为各路径组件 `unicodedata.normalize("NFC", part).rstrip(" .").casefold()` 后再用 `/` 连接。该键只用于检测，不替换用户原文。

## 10. 扫描算法

### 10.1 输入

- 已验证的服务器信息；
- 唯一目标种子信息；
- `torrents/files` 返回的完整文件列表；
- 已验证配置。

### 10.2 步骤

1. 按 API 文件 `index` 排序；
2. 校验并规范化每个文件路径；
3. 重复 index 属于无效 API 数据并停止扫描；重复路径保留全部文件条目，按 index 区分，并标记 `DUPLICATE_INTERNAL_PATH`；
4. 从每个文件路径推导所有祖先目录，形成去重的目录集合；
5. 对文件和目录计算指标；
6. 对文件末级名、每一级目录名、相对路径和 Windows 绝对路径执行检测；
7. 目录组件自身有问题时，为该目录生成一条文件夹记录，而不是为其每个后代重复生成同一“目录名问题”；
8. 文件自身名称或其完整路径有问题时，为该文件生成文件记录；
9. 没有问题的项目不写入 CSV，但所有文件和推导目录均写入 JSON 快照；
10. 先原子写入快照，再从该快照导出 CSV 和扫描摘要。

如果一个文件仅因祖先目录组件过长而有问题，而文件自身名称及完整路径限制没有单独违规，则只导出对应文件夹记录。文件夹改名后，后代文件会在计划展开阶段统一复检。

### 10.3 稳定记录编号

```text
record_id = SHA-256(
    schema_version + "\0" + torrent_hash + "\0" + item_type + "\0"
    + 文件条目的 file_index（目录为空） + "\0" + old_path
) 的前 20 个十六进制字符
```

编号用于检测 CSV 行被错误复制或对应到其他快照，不作为安全签名。

## 11. 文件格式

### 11.1 工作目录

每次运行生成：

```text
work/<UTC时间>_<哈希前12位>_scan/
├── manifest.json
├── snapshot.json
├── rename_intents.csv
├── scan_report.json
└── run.log
```

执行时不修改原扫描目录，而是创建：

```text
work/<UTC时间>_<哈希前12位>_apply/
├── source_manifest.json
├── submitted_intents.csv
├── validation_report.json
├── execution_plan.json
├── execution_state.json
├── before_snapshot.json
├── after_snapshot.json
├── reverse_mapping.csv
└── run.log
```

### 11.2 原子写入

JSON、CSV 和执行状态先写入同目录临时文件，调用 `flush()` 和 `os.fsync()` 后使用 `os.replace()` 替换。执行状态每次 API 操作前后都落盘，尽量降低程序崩溃造成的状态丢失。

### 11.3 `manifest.json`

至少包含：

```json
{
  "schema_version": 1,
  "run_id": "20250101T000000Z_0123456789ab_scan",
  "created_at": "2026-08-17T07:00:00Z",
  "server": {
    "base_url": "http://qbittorrent.example.test:8080/",
    "app_version": "v4.6.7",
    "webapi_version": "2.x",
    "authenticated": false
  },
  "torrent": {
    "hash": "0123456789abcdef0123456789abcdef01234567",
    "name": "..."
  },
  "config_fingerprint": "sha256:...",
  "snapshot_sha256": "sha256:..."
}
```

配置指纹排除密码环境变量的实际值，只包含影响扫描和校验结果的非敏感配置。

### 11.4 `snapshot.json`

包含：

- `schema_version`；
- 服务器和种子摘要；
- 生效的非敏感限制；
- Windows 目标根目录；
- 完整文件数组；
- 完整推导目录数组；
- 每项指标和问题；
- 快照内容摘要。

JSON 以 UTF-8、`ensure_ascii=false`、稳定键顺序和固定缩进写入。摘要基于排除摘要字段本身后的规范 JSON 字节计算。

### 11.5 `rename_intents.csv`

使用 UTF-8 with BOM（`utf-8-sig`）和 RFC 4180 风格引号。字段顺序固定：

```text
schema_version
snapshot_sha256
record_id
torrent_hash
item_type
file_index
old_path
new_path
issues
old_name_chars
old_name_utf8_bytes
old_path_chars
old_path_utf8_bytes
old_windows_absolute_path_chars
action
note
```

规则：

- 程序生成字段：从 `schema_version` 到 `old_windows_absolute_path_chars`；
- 用户字段：`new_path`、`action`、`note`；
- `issues` 使用以 `;` 分隔的稳定问题代码；
- `new_path` 默认留空，不自动生成；
- `action` 默认留空；
- 导入时要求列名完整，但允许用户调整列顺序；
- 不允许额外未知列，防止拼写错误被静默忽略；
- 重复 `record_id`、被修改的程序字段或不匹配的快照摘要均为阻断错误；
- CSV 单元格若以 `= + - @`、制表符或回车开头，导出供查看的文本字段时使用版本化的防公式注入转义；导入时按同一版本做可逆还原。路径字段不得被 Excel 当作公式执行。

具体实现采用单引号前缀作为 Excel 文本保护，并在 CSV 的 `schema_version` 所对应的格式规范中定义还原规则。程序生成字段还原后必须与快照严格匹配。用户字段 `new_path` 也按同一规则读取：一个保护用单引号被移除；如用户确实需要名称以单引号开头，则输入两个单引号表示一个字面单引号。以 `= + - @` 开头的路径本身可以是合法种子路径，不能仅因首字符而拒绝。

## 12. 问题代码

首版定义稳定代码：

| 代码 | 含义 |
|---|---|
| `FILE_NAME_CHARS_EXCEEDED` | 文件名字符数超限 |
| `FILE_NAME_UTF8_BYTES_EXCEEDED` | 文件名 UTF-8 字节数超限 |
| `DIR_NAME_CHARS_EXCEEDED` | 目录名字符数超限 |
| `DIR_NAME_UTF8_BYTES_EXCEEDED` | 目录名 UTF-8 字节数超限 |
| `REL_PATH_CHARS_EXCEEDED` | 相对路径字符数超限 |
| `REL_PATH_UTF8_BYTES_EXCEEDED` | 相对路径 UTF-8 字节数超限 |
| `WINDOWS_ABS_PATH_CHARS_EXCEEDED` | Windows 最终绝对路径字符数超限 |
| `WINDOWS_INVALID_CHAR` | Windows 非法字符或控制字符 |
| `WINDOWS_TRAILING_DOT_SPACE` | 名称以句点或空格结尾 |
| `WINDOWS_RESERVED_NAME` | Windows 保留设备名 |
| `WINDOWS_CASE_COLLISION` | 大小写不敏感冲突 |
| `WINDOWS_NORMALIZATION_COLLISION` | Unicode 归一化后冲突 |
| `INVALID_INTERNAL_PATH` | API 返回或用户填写的路径结构非法 |
| `DUPLICATE_INTERNAL_PATH` | 多个文件 index 具有完全相同的种子内部路径 |

扫描阶段发现 API 返回 `INVALID_INTERNAL_PATH` 时不生成可执行计划，而是整体失败并保留诊断报告。

## 13. CSV 导入和意图校验

### 13.1 导入步骤

1. 以 `utf-8-sig` 严格读取；
2. 校验表头；
3. 校验每行列数、架构版本、哈希和快照摘要；
4. 用 `record_id` 回查快照；
5. 确认所有程序字段未被修改；
6. 解析 `action`；
7. `rename` 必须有 `new_path`；
8. `skip` 必须忽略 `new_path`，如果同时填写则给出警告并不执行；
9. 空 action 表示未完成，默认阻止整批执行，但允许生成校验报告；
10. 规范化并校验所有新路径。

### 13.2 文件夹与文件一致性

建立原始路径树和最终路径树。对每个文件夹意图 `A -> B`：

- 原始位于 `A/` 下且没有更具体文件夹意图的后代，默认映射到 `B/` 下的相同后缀；
- 存在嵌套文件夹意图 `A/C -> B/D` 时，其目标必须位于 `B/` 下；
- 存在显式文件意图 `A/x.txt -> B/y.txt` 时，其目标必须位于 `B/` 下；
- 若文件或嵌套目录目标脱离 `B/`，报告父子关系不一致，阻止执行；
- 最具体的显式意图负责末级名称或更深层结构，祖先映射负责共同前缀。

对未列入 CSV 的后代，自动应用最具体的文件夹映射，不要求用户填写。

### 13.3 当前服务端漂移检查

校验或执行开始前重新读取文件列表：

- 按规范路径、index 和 size 构造当前指纹；
- 与扫描快照比较；
- 任意路径增加、删除、重命名或 index/path 对应变化均视为结构漂移；
- progress 等下载状态变化不视为结构漂移；
- 存在结构漂移时禁止沿用旧 CSV，用户必须重新扫描并迁移人工结果。

首版不自动把旧 CSV 合并到新快照，避免错误映射。

## 14. 最终命名空间计算

### 14.1 计算顺序

1. 读取完整快照中的所有文件；
2. 建立已确认的文件夹映射；
3. 对每个原始文件应用最具体且一致的文件夹映射；
4. 如果存在该文件的显式 `rename` 意图，以其完整最终路径覆盖自动推导结果；
5. `skip` 表示保留原路径；如果它位于已改名文件夹下而无法真正保留原路径，则这是意图矛盾，阻止执行；
6. 生成每个原始文件唯一的最终路径；
7. 重新推导完整最终目录集合；
8. 对全部文件和目录重新执行所有限制和 Windows 兼容性检查；
9. 检查精确路径、大小写键、NFC 大小写键以及文件/目录结构冲突。

最终命名空间中的任一阻断问题都会阻止计划生成，即使相关文件原本没有出现在 CSV 中。

### 14.2 冲突类型

- 两个文件映射到同一路径；
- 文件目标与目录目标同名；
- Windows 大小写不敏感后重名；
- Unicode NFC + casefold 后重名；
- 目标路径是另一个文件路径的后代；
- 目标占用且占用项目没有在计划中移走；
- 文件夹映射把目录移动到自身后代；
- 多个文件夹映射互相包含但目标层级不一致。

## 15. 操作计划编译

### 15.1 两层表示

规划器同时维护：

- 文件级最终映射：每个原始文件 → 唯一最终文件路径；
- API 操作序列：以尽量少且可验证的文件夹/文件操作实现相同映射。

编译完成后，在纯内存命名空间中模拟整个操作序列。只有模拟结果与文件级最终映射完全一致，计划才有效。

### 15.2 文件夹操作合并条件

原始文件夹 `A` 可合并为一次 `renameFolder A -> B`，仅当：

- `A` 的全部后代文件都映射到 `B/<原后缀>`；
- 没有后代被要求留在 `A` 或移出 `B`；
- `B` 不是 `A` 的自身后代；
- 合并不会吞并另一个独立目录；
- 模拟 qBittorrent 前缀替换后得到的文件集合与最终映射一致。

从较大的公共子树开始寻找合并候选。无法安全合并的差异使用 `renameFile` 或更小范围的 `renameFolder`。

### 15.3 当前路径表

执行计划生成及执行时维护：

```text
original_file_path -> current_file_path -> final_file_path
original_folder_path -> current_folder_path -> final_folder_path
```

应用 `renameFolder X -> Y` 后，所有以 `X/` 为前缀的当前文件及目录路径立即替换为 `Y/`。后续操作的 `source_path` 从更新后的当前路径表生成，绝不继续使用过期的扫描路径。

### 15.4 排序算法

1. 生成候选操作及其读写路径集合；
2. 若操作 B 的源路径会被操作 A 改变，则建立依赖边；
3. 若 A 的目标当前被 B 的源占用，则 B 必须先执行；
4. 嵌套目录的局部改名通常先于共同祖先改名；
5. 对依赖图进行稳定拓扑排序；
6. 若图存在环，插入同级唯一临时路径打破环，再重新建图；
7. 每插入临时操作后重新模拟并验证；
8. 输出确定性顺序，相同输入始终得到相同计划。

不能仅采用固定的“先文件后目录”或“先目录后文件”策略；实际顺序由路径依赖图决定。

### 15.5 临时路径

临时名称格式示例：

```text
.__btfr_tmp_<run-id短值>_<序号>__
```

要求：

- 与当前和最终命名空间都不冲突；
- 满足所有组件字节限制；
- 只在确有交换、大小写改名或占用环时使用；
- 与目标位于可行的同级或共同父路径；
- 在执行计划中明确标记 `temporary=true`；
- 最终验证时不得残留任何临时路径。

### 15.6 典型混合示例

用户意图：

```text
folder: A/VeryLongDir      -> A/Short
file:   A/VeryLongDir/x... -> A/Short/x.mkv
```

可能编译为：

```text
1. renameFolder A/VeryLongDir -> A/Short
2. renameFile   A/Short/x...  -> A/Short/x.mkv
```

第二步使用的是文件夹操作后的当前路径。若目标占用或嵌套映射要求不同，规划器可以得出不同顺序，但必须通过内存模拟证明最终文件映射一致。

## 16. 执行状态机

### 16.1 整体状态

```text
created -> validated -> confirmed -> executing
executing -> completed
executing -> stopped_failed
executing -> stopped_unknown
```

`completed` 表示所有 API 操作均已被服务端接受，并且每项操作的路径变化都已通过文件列表轮询确认；最终全量验证结果另行记录为 `verified` 或 `verification_failed`。HTTP 200 本身不视为操作完成。

### 16.2 单项状态

```text
pending -> running -> success
pending -> running -> failed
pending -> running -> unknown
pending -> not_run
```

在发送请求前先将该操作标记为 `running` 并原子落盘：

1. 写入开始时间和当前路径表摘要；
2. 发送一个 API 请求；
3. HTTP 2xx 仅将状态记为 `accepted`，随后轮询文件列表确认受影响文件的路径；
4. 观测到全部预期路径后写入 `success`，更新当前路径表，并经过短暂稳定间隔后才发送下一项；
5. 明确 HTTP/业务失败时写入 `failed`；
6. 请求结果或异步完成结果无法确认时写入 `unknown`；
7. `failed` 或 `unknown` 后立即把剩余 `pending` 标为 `not_run`，停止整批。

### 16.3 异步重命名确认

qBittorrent 4.6.7 的 Web API 控制器在调用 `Torrent::renameFile()` 或 `Torrent::renameFolder()` 后立即结束请求。底层 `libtorrent::torrent_handle::rename_file()` 是异步操作，真正完成或失败分别由 `file_renamed_alert` 和 `file_rename_failed_alert` 表示；这些 alert 不通过 Web API 返回。因此执行器不得连续快速发送计划中的写请求，也不得仅凭 HTTP 200 将操作标记为成功。

每个操作使用以下确认流程：

1. 写请求返回 2xx 后，以可配置间隔调用 `GET /api/v2/torrents/files`；优先使用 `indexes` 仅读取受该操作影响的文件；
2. `renameFile` 必须观测到目标文件 index 的路径等于本次操作的 `target_path`；
3. `renameFolder` 必须观测到该目录下所有受影响文件 index 都已切换到对应的新前缀；
4. 确认期间不发送下一项写请求；
5. 建议默认轮询间隔为 1 秒、单项超时为 60 秒、确认后的稳定间隔为 500 毫秒，三者均允许配置；
6. HTTP 2xx 后直到超时仍未出现完整目标状态，记为 `unknown` 而不是 `failed`，立即停止整批并保留现场供 `verify` 检查；
7. 轮询中若出现既不是操作前状态、也不是完整目标状态的部分变化，同样继续等待；超时后按 `unknown` 停止，不尝试自动回滚或重发；
8. 只有确认当前操作成功后，执行器才更新运行时路径映射并进入下一项。

轮询路径变化既是对异步 alert 的 Web API 侧替代确认，也能避免目录操作尚未完成时，后续请求继续引用新旧混合路径。

### 16.4 明确确认

非 `--dry-run` 执行前显示目标摘要及计划摘要，并要求用户输入：

```text
RENAME 0123456789ab
```

确认文本必须完全匹配。非交互运行首版不允许绕过确认；未来若增加自动模式，需要单独的显式危险开关，不属于当前范围。

### 16.5 Dry-run

`apply --dry-run` 完成以下全部步骤：

- 重新读取服务端并检查漂移；
- 导入 CSV；
- 展开完整最终命名空间；
- 执行所有校验；
- 编译和模拟操作计划；
- 写出验证报告与执行计划。

它不会调用 `renameFile` 或 `renameFolder`，也不会要求执行确认。

## 17. 执行后验证与恢复信息

### 17.1 验证

重新调用 `torrents/files`，按文件 index、size 和路径比较：

- 每个预期最终路径存在；
- 每个已移动的旧路径消失；
- 文件数量、index 和 size 不变；
- 没有临时路径残留；
- 实际完整路径集合等于计划最终路径集合。

progress 可以变化，不参与一致性结论。

### 17.2 `unknown` 操作的处理

写请求超时后不重发。验证器检查：

- 只有目标存在且源不存在：推断操作已生效，报告 `inferred_success`；
- 只有源存在且目标不存在：推断未生效，报告 `inferred_not_applied`；
- 两者都存在或都不存在：保持 `unknown`，要求人工检查。

推断结果不改写原始请求状态，而是作为独立验证字段保存。

### 17.3 反向映射

`reverse_mapping.csv` 记录每个成功操作执行时的：

- 实际源路径；
- 实际目标路径；
- 操作类型；
- 原操作 ID；
- 建议逆操作顺序。

它只用于人工恢复参考。首版不提供自动回滚命令，因为部分文件夹操作、后续下载状态及外部改动可能使自动回滚不安全。

## 18. 日志设计

### 18.1 终端输出

终端只显示简洁摘要、进度、警告和最终文件位置。数千个文件的逐项详情写入报告，不全部打印。

### 18.2 文件日志

日志字段至少包括：UTC 时间、级别、run ID、阶段、操作 ID、端点名、HTTP 状态、耗时和错误类别。

必须脱敏：

- 密码；
- `SID` Cookie；
- 认证表单；
- 环境变量值。

路径不是认证秘密，默认可写入本地执行记录以满足审计需求。异常响应正文最多记录有限字符，并先做 Cookie/密码模式脱敏。

## 19. 异常与退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功，或 dry-run 校验通过 |
| 2 | 命令行或配置错误 |
| 3 | 连接或认证失败 |
| 4 | 目标种子或 API 不兼容 |
| 5 | 扫描数据异常 |
| 6 | CSV 或快照格式错误 |
| 7 | 计划校验未通过 |
| 8 | 用户取消确认 |
| 9 | 执行失败，已停止 |
| 10 | 执行结果未知，已停止 |
| 11 | 执行后验证失败 |
| 12 | 未处理的程序错误 |

所有预期错误应转为简洁用户消息并指向详细报告；只有调试模式在终端显示堆栈。

## 20. CLI 设计

```text
python -m qbittorrent_pathfixer --config config.toml connection-test
python -m qbittorrent_pathfixer --config config.toml scan
python -m qbittorrent_pathfixer --config config.toml validate --plan <CSV>
python -m qbittorrent_pathfixer --config config.toml apply --plan <CSV> --dry-run
python -m qbittorrent_pathfixer --config config.toml apply --plan <CSV>
python -m qbittorrent_pathfixer --config config.toml verify --run <执行目录>
```

公共参数：

- `--config PATH`：配置文件，默认当前目录 `config.toml`；
- `--verbose`：增加终端诊断；
- `--no-color`：关闭颜色，首版可不实现颜色；
- `--version`：程序版本。

命令行为：

| 命令 | 网络读取 | 网络写入 | 主要输出 |
|---|---:|---:|---|
| `connection-test` | 是 | 否 | 版本、认证方式、目标种子摘要 |
| `scan` | 是 | 否 | 快照、CSV、扫描报告 |
| `validate` | 是 | 否 | 校验报告、操作计划 |
| `apply --dry-run` | 是 | 否 | 与 validate 相同，加执行摘要 |
| `apply` | 是 | 是 | 执行状态、前后快照、反向映射 |
| `verify` | 是 | 否 | 独立复核报告 |

`validate` 与 `apply --dry-run` 内部复用同一用例，避免校验逻辑分叉。

## 21. 测试设计

### 21.1 单元测试

- ASCII、中文、emoji、组合字符的码点和 UTF-8 字节统计；
- 盘符及 UNC Windows 根目录长度；
- 非法字符、控制字符、保留名、尾随句点和空格；
- NFC 与 `casefold()` 冲突；
- 相对路径解析和前缀边界，例如 `a/b` 不匹配 `a/bb`；
- 目录集合推导；
- 稳定 record ID 和快照摘要；
- CSV BOM、引号、换行、公式注入保护和字段篡改；
- 文件夹映射展开到未列入 CSV 的后代；
- 父子意图一致性；
- 冲突图、环、大小写改名和临时路径；
- 操作模拟结果与最终文件映射一致；
- 任意失败后其余操作全部 `not_run`。

### 21.2 API 测试

用 `httpx.MockTransport` 模拟：

- 免认证成功；
- 需要认证、登录成功和登录失败；
- 3xx 重定向；
- 空种子列表；
- Web API 版本过低；
- 正常文件列表和异常 JSON；
- 400、403、404、409、500；
- GET 网络错误重试；
- 写请求读取超时后不重试并进入 `unknown`；
- 日志不出现密码和 Cookie。

### 21.3 集成测试

使用内存假 API 或专用测试 qBittorrent：

- 扫描 → 编辑 fixture CSV → 校验 → dry-run；
- 文件和文件夹混合改名；
- 文件夹改名后文件操作使用新当前路径；
- 路径交换通过临时名称完成；
- 中间一步失败立即停止；
- 执行后完整文件集合验证。

真实 WebUI 的写集成测试必须使用专用小型测试种子，不允许使用本需求中的正式目标种子。

## 22. 分阶段实现计划

### 22.1 阶段一：只读扫描器

实现范围：

- `pyproject.toml` 和项目骨架；
- 配置加载和校验；
- API 免认证/认证连接、版本检测；
- 获取目标种子和文件列表；
- 严格路径模型、长度统计、Windows 兼容性检测；
- 完整 JSON 快照；
- 只包含问题项的 CSV；
- 扫描报告和相关测试。

不实现 `renameFile`、`renameFolder` 的实际调用入口，避免阶段一误写服务端。

### 22.2 阶段二：意图校验与计划器

- CSV 导入及防篡改；
- 服务端漂移检查；
- 文件夹映射展开；
- 最终命名空间全量校验；
- API 操作合并、依赖排序和临时路径；
- 内存模拟；
- `validate` 和 `apply --dry-run`。

### 22.3 阶段三：执行器

- 写 API 封装；
- 明确确认；
- 执行状态机和原子落盘；
- 当前路径表更新；
- 失败即停；
- 写请求结果未知处理；
- 反向映射。

### 22.4 阶段四：验证和发布准备

- 独立验证命令；
- 真实专用测试种子验证；
- Windows 安装和使用文档；
- 依赖锁定及打包检查。

## 23. 阶段一完成标准

阶段一只有同时满足以下条件才算完成：

1. 可以在 Windows 虚拟环境中安装项目并运行所有测试；
2. `connection-test` 能区分免认证成功、认证失败、目标种子不存在和 API 不兼容；
3. `scan` 对完整文件清单生成确定性 JSON 快照；
4. CSV 只包含检测到问题的文件或文件夹；
5. 未进入 CSV 的项目仍存在于快照并可用于后续冲突检查；
6. 字符、UTF-8 字节和 Windows 最终绝对路径统计有边界测试；
7. CSV 可以由 Excel 打开并安全保留中文路径；
8. 阶段一代码中没有可从 CLI 触发的重命名写操作；
9. 文档说明如何创建配置和运行只读扫描；
10. 对正式目标种子首次运行前，先使用连接测试确认 WebUI 返回的实际 Web API 版本。

## 24. 已知限制与后续扩展

- 首版一个配置文件只处理一个种子；
- 首版不自动迁移旧 CSV 到新快照；
- 首版不自动回滚；
- 首版不提供 GUI；
- Python `len()` 统计码点而非视觉字形；
- Windows 长路径行为受目标 Windows、注册表、应用清单及复制工具影响，因此使用用户配置阈值而非假定唯一硬上限；
- qBittorrent 改名操作的实际边界行为最终需要在专用测试种子上验证；
- 未来可以增加 XLSX 编辑文件、多个种子配置、自动名称建议和安全的恢复辅助，但不得改变“默认只读、确认后执行”的原则。
