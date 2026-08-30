# qBittorrent PathFixer

有些种子包含过长的文件名或目录名：它们可能超过 Linux 文件系统的单组件字节限制，或在复制到 Windows 时触发路径长度、保留名称及非法字符等兼容性问题。此时 qBittorrent 可能无法创建文件，或者已经下载的内容无法按原路径复制到目标目录。

qBittorrent PathFixer 通过 Web API 读取种子的文件结构，按照可配置的字符数、UTF-8 字节数和 Windows 路径规则找出问题条目，并生成只包含待处理项的 CSV。用户可以在 CSV 中亲自调整文件名和目录结构，随后让程序检查路径冲突、父目录改名关系和服务端状态，再通过 dry-run 审阅最终操作计划。

确认计划无误后，程序会按顺序调用 qBittorrent 的文件及文件夹改名 API，并等待每项异步操作实际生效后才继续；任何一项失败都会立即停止。重复内部路径会被识别和阻断，不会套用自动猜测的命名方案。

**※ 本项目基本由 ChatGPT 完成，建议交由 AI 分析和执行相关指令。**

## 安装

在 Windows PowerShell 中：

```powershell
py -3.14 -m venv venv
venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Python 3.11–3.14 均为目标支持版本，不要求必须使用 3.14。

## 配置

复制示例配置：

```powershell
Copy-Item config.example.toml config.toml
```

至少检查：

- `qbittorrent.base_url`
- `qbittorrent.torrent_hash`
- `windows.destination_root`
- `[limits]` 中的限制

### `[limits]` 各项说明

程序会同时检查“字符数”和“UTF-8 字节数”，因为两者衡量的不是同一件事：

- 字符数主要用于判断 Windows 路径、复制工具或其他应用程序对名称和路径长度的限制；
- UTF-8 字节数主要用于判断 Linux 文件系统保存名称和路径时所占用的字节数；
- ASCII 字符通常占 1 个 UTF-8 字节，常见中文字符通常占 3 个 UTF-8 字节，部分 emoji 占 4 个 UTF-8 字节；
- 因此，一个中文文件名可能字符数不多，但 UTF-8 字节数已经超过 Linux 单个名称通常为 255 字节的限制；反过来，一个全部由 ASCII 组成的深层路径也可能先超过 Windows 的字符数限制。

例如：

```text
文件名：测试文件.txt
字符数：8
UTF-8 字节数：16
```

程序中的“字符数”具体指 Python Unicode 码点数。它通常与直观看到的字符数一致，但组合字符等特殊 Unicode 文本可能显示为一个字形、实际统计为多个码点。

各配置项作用如下：

| 配置项 | 默认值 | 作用 |
|---|---:|---|
| `filename_chars` | `200` | 文件末级名称允许的最大字符数，不包含上级目录 |
| `filename_utf8_bytes` | `220` | 文件末级名称经过 UTF-8 编码后允许的最大字节数 |
| `directory_chars` | `200` | 每一级目录名称允许的最大字符数，不是所有目录名的总和 |
| `directory_utf8_bytes` | `220` | 每一级目录名称经过 UTF-8 编码后允许的最大字节数 |
| `relative_path_chars` | `1000` | 种子内部完整相对路径允许的最大字符数，包含目录、文件名和 `/` 分隔符 |
| `relative_path_utf8_bytes` | `3800` | 完整相对路径经过 UTF-8 编码后允许的最大字节数，也包含 `/` 分隔符 |
| `windows_absolute_path_chars` | `240` | `windows.destination_root` 与种子相对路径拼接后，Windows 最终绝对路径允许的最大字符数 |

这些默认值有意低于常见文件系统的理论边界，为路径拼接、复制工具差异和系统行为保留余量。例如，名称字节限制没有设置为接近 Linux 常见的 255 字节边界，Windows 最终路径也没有设置为传统 `MAX_PATH` 的临界值。

所有限制都可以按实际环境修改。某项不需要检查时，可以将其设置为 `false`，例如：

```toml
[limits]
filename_chars = 200
filename_utf8_bytes = 220
directory_chars = 200
directory_utf8_bytes = 220
relative_path_chars = false
relative_path_utf8_bytes = 3800
windows_absolute_path_chars = 240
```

不要使用 `0` 表示关闭限制；配置只接受正整数或 `false`。

`windows_absolute_path_chars` 会把当前种子的 `windows.destination_root` 计算在内。因此，不同种子复制到不同 Windows 目录时，应分别使用对应的配置文件或修改该配置项：

```toml
[windows]
destination_root = "D:/Downloads/TargetTorrent"
```

如果 WebUI 通过 IP 白名单允许免认证，不要配置 `username` 和 `password_env`。如果需要认证，同时配置二者，并在环境变量中设置密码：

```powershell
$env:QBIT_PASSWORD = "你的密码"
```

重命名底层为异步操作，执行器使用以下可选配置：

```toml
[execution]
poll_interval_seconds = 1.0
operation_timeout_seconds = 60.0
settle_delay_seconds = 0.5
```

- `poll_interval_seconds`：写请求被接受后查询文件路径变化的间隔；
- `operation_timeout_seconds`：等待单项操作达到完整目标状态的最长时间；
- `settle_delay_seconds`：单项确认成功后、发送下一项之前的额外稳定时间。

`config.toml`、`work/` 和虚拟环境已被 `.gitignore` 排除。

## 使用

先测试连接：

```powershell
venv\Scripts\python.exe -m qbittorrent_pathfixer --config config.toml connection-test
```

再执行只读扫描：

```powershell
venv\Scripts\python.exe -m qbittorrent_pathfixer --config config.toml scan
```

扫描会在 `work/` 中生成：

- `manifest.json`
- `snapshot.json`
- `rename_intents.csv`
- `scan_report.json`
- `run.log`

CSV 采用 UTF-8 with BOM，只包含检测到问题的文件和文件夹。完整文件及目录集合保存在 `snapshot.json` 中。

如果种子中多个文件具有完全相同的内部路径，扫描不会丢弃或合并这些文件。CSV 使用 `file_index` 区分它们，并标记 `DUPLICATE_INTERNAL_PATH`。qBittorrent 4.6.7 的改名 API 只接受路径、不接受文件 index，因此这类记录在后续执行阶段必须经过专门验证，不能直接按普通文件处理。

## 校验人工计划

人工编辑 CSV 的注意事项：
- 不要修改 file_index、record_id、old_path 等程序字段。
- 在 new_path 填写完整的最终种子相对路径。
- 路径统一使用 /。
- 将 action 填为 rename 或 skip。
- 文件夹及其问题后代都出现时，填写的最终父子路径必须一致。
- DUPLICATE_INTERNAL_PATH 条目目前可以先保留空白或标记 skip。

编辑 CSV 后，先运行：

```powershell
venv\Scripts\python.exe -m qbittorrent_pathfixer --config config.toml validate --plan "work/扫描目录/rename_intents.csv"
```

也可以使用与未来执行命令一致的 dry-run 形式：

```powershell
venv\Scripts\python.exe -m qbittorrent_pathfixer --config config.toml apply --plan "work/扫描目录/rename_intents.csv" --dry-run
```

两种形式都会重新读取 qBittorrent 文件列表、检查扫描后是否发生路径漂移、展开文件夹对所有后代的影响、检查最终路径和生成 API 操作计划。它们不会调用 `renameFile` 或 `renameFolder`。

校验输出目录中的 `validation_issues.csv` 可以用 Excel 打开并按 `severity`、`code` 或 `record_id` 筛选。修改原始 `rename_intents.csv` 后重新运行校验，不要编辑 `submitted_intents.csv`，后者只是某次校验使用的留档副本。

dry-run 通过并人工检查 `execution_plan.json` 后，可在交互式 PowerShell 中执行：

```powershell
venv\Scripts\python.exe -m qbittorrent_pathfixer --config config.toml apply --plan "work/扫描目录/rename_intents.csv"
```

程序会再次读取并校验服务端状态，然后要求输入 `RENAME 哈希前12位`。每个写请求返回 HTTP 200 后，程序仍会轮询受影响文件的实际路径；只有确认异步操作完整完成才会继续下一项。请求失败或结果无法确认时立即停止，其余项目标记为 `not_run`。

## 安全说明

- HTTP 不加密用户名、密码和 Cookie。仅应在可信局域网或安全隧道中使用。
- 不带 `--dry-run` 的 `apply` 会实际调用重命名 API；务必先检查计划并确认目标种子。
- 不要把包含凭据的配置或运行输出上传到公共位置。

## 重复内部路径

程序会保留并标记 qBittorrent 返回的重复内部路径，但不会自动为这些条目制定或执行私人化的命名方案。此类记录默认由校验器阻断；成因、API 限制及可能的研究方向见 [DUPLICATE_PATH_ANALYSIS.md](DUPLICATE_PATH_ANALYSIS.md)。

需求和设计详见 [REQUIREMENTS.md](REQUIREMENTS.md) 与 [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)。
