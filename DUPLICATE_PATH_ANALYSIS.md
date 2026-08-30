# qBittorrent 重复内部路径问题分析

## 1. 问题概述

qBittorrent 的文件列表 API 可能返回路径完全相同、但 file index 和文件大小不同的条目。这些条目并非 API 重复发送同一记录；它们可能代表不同的种子文件，只是在 qBittorrent 当前使用的内部路径中发生了名称碰撞。

本文基于 qBittorrent v4.6.7 的只读检查、源码分析和合成测试整理。具体种子、服务器地址及用户命名数据均不属于本文内容。

## 2. 一类已观察到的成因

在一个大型种子的案例中，原始 `.torrent` 元数据中的文件路径全部唯一，但若干目录名和文件名超过了 Linux 常见文件系统的单路径组件 255 字节限制。qBittorrent/libtorrent 对这些名称进行文件系统安全化或截断后，多个具有相同长前缀的名称丢失了位于末尾的卷数或版本差异，最终变成相同路径。

示意：

```text
原始文件 A：<相同的长前缀>第一卷.epub
原始文件 B：<相同的长前缀>第二卷.epub
原始文件 C：<相同的长前缀>第三卷.epub

处理后 A：<相同的截断前缀>.epub
处理后 B：<相同的截断前缀>.epub
处理后 C：<相同的截断前缀>.epub
```

案例中的超长目录组件约为 258–270 个 UTF-8 字节，超长文件名约为 268–285 个 UTF-8 字节；碰撞后的名称主体稳定在约 240 个 UTF-8 字节，再加扩展名。这些数值是特定案例的观察结果，不应视为所有 qBittorrent 或文件系统版本的固定规则。

这里的关键是 UTF-8 字节数，而不只是字符数。日文、中文等字符常占多个 UTF-8 字节，视觉上不一定很长，却可能先达到文件系统限制。

## 3. 为什么 file index 不足以通过 Web API 直接改名

`/api/v2/torrents/files` 为每个文件返回 `index` 和当前路径，但 qBittorrent v4.6.7 的路径式 Web API 政名接口只接收：

```text
hash
oldPath
newPath
```

Web API 控制器把路径传给内部的路径式 `renameFile`。在 `AbstractFileStorage::renameFile(oldPath, newPath)` 中，qBittorrent 从 index 0 开始遍历文件列表，并选取第一个满足 `filePath(i) == oldPath` 的文件。找到第一个匹配项后，后续相同路径不会替换这个选择。

随后 qBittorrent 调用按 index 工作的 `TorrentImpl::renameFile(index, newPath)`，再把逻辑 index 映射为 libtorrent native index，最终调用 libtorrent 的 `torrent_handle::rename_file(file_index_t, ...)`。因此 libtorrent 本身可以精确按 index 改名，歧义产生在 qBittorrent Web API 从 `oldPath` 推导 index 的这一层。

```text
Web API oldPath
    ↓
遍历 filePath(i)
    ↓
选择第一个相等的逻辑 index
    ↓
映射为 libtorrent native index
    ↓
torrent_handle::rename_file(...)
```

源码参考：

- [qBittorrent v4.6.7 Web API 控制器](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.7/src/webui/api/torrentscontroller.cpp)
- [qBittorrent v4.6.7 AbstractFileStorage 路径式改名](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.7/src/base/bittorrent/abstractfilestorage.cpp)
- [qBittorrent v4.6.7 TorrentImpl 按 index 政名](https://github.com/qbittorrent/qBittorrent/blob/release-4.6.7/src/base/bittorrent/torrentimpl.cpp)

## 4. “逐个剥离”的行为与异步风险

假设三个 index 当前都显示为 `duplicate.epub`。第一次请求把 `duplicate.epub` 改为唯一名称后，理论上最低的匹配 index 会离开碰撞组；再次使用同一个 oldPath 时，遍历便会命中下一个仍保留旧路径的 index。这形成一种按 index 顺序逐个“剥离”碰撞项的可能方法。

在 qBittorrent v4.6.7 上使用三个文件的合成碰撞种子进行的隔离实验，观察到了这一顺序行为。但它不是 Web API 对指定 index 政名的正式保证，也不能直接证明任意版本、种子状态或存储后端都安全。

此外，底层 `rename_file()` 是异步操作。提交请求并不代表内部路径和磁盘状态已经完成更新；qBittorrent 还会通过 libtorrent 的成功或失败 alert 完成后续处理。连续快速发送多个相同 oldPath 的请求，可能在前一个变化可见前再次选中相同条目。因此任何研究性实现都必须串行操作，并在每次请求后按 index 重新读取文件列表，确认预期路径变化后才继续。

## 5. 数据完整性风险

即使内部索引能够逐项分离，多个逻辑文件曾经竞争同一个物理路径时，也不能仅凭当前文件名判断磁盘内容属于哪个 index。潜在风险包括：

- 现有物理文件可能只对应碰撞组中的一个文件；
- 名称变化顺序与预想不同时，内容会被赋予错误名称；
- 重启、重新校验或恢复下载可能改变可观察状态；
- 针对书籍卷数、配套图片或元数据的命名推断高度依赖具体内容。

若使用者自行研究修复流程，应先暂停种子、备份数据与状态、使用隔离的合成种子验证目标版本，并在改名后执行完整重新校验；缺失或不匹配的数据可能需要重新下载。

## 6. 本项目的处理边界

本项目保留重复路径的检测和审计信息：

- 不合并或丢弃重复条目；
- 为每条记录保留 file index、大小和当前路径；
- 使用包含 file index 的唯一记录身份；
- 标记 `DUPLICATE_INTERNAL_PATH`；
- 在普通执行流程中阻断仍需改名的重复路径条目。

本项目不提供自动重复路径修复、按内容猜测卷数、成套文件统一命名或“逐个剥离”的执行命令。这些策略依赖用户数据和命名偏好，而且存在数据完整性风险，不适合作为通用默认行为。

## 7. 结论

重复内部路径可能源于唯一的超长原始名称经过文件系统安全化或截断后发生碰撞。qBittorrent Web API 又只接受 oldPath，而不接受 file index，使碰撞条目无法被随机、无歧义地寻址。

源码和隔离实验说明顺序消歧在特定版本上具有研究价值，但异步执行和磁盘内容归属仍是关键风险。本项目因此只负责识别、记录和阻断，不自动替用户决定修复名称或执行碰撞修复。
