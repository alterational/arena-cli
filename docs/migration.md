# 升级说明

## 2.4.0：`arena-agent` → `arena-cli`

2.4.0 之前这个项目的名字不统一：仓库叫 `arena-cli`，Hermes 技能叫 `arena-agent`
（斜杠命令 `/arena-agent`），数据目录是 `~/.arena-agent`。现在收成一个名字：

| 东西 | 2.3.x | 2.4.0 |
| --- | --- | --- |
| 仓库 / 包 | `arena-cli` | `arena-cli` |
| 命令 | `arena` | `arena` |
| Hermes 技能 / 斜杠命令 | `arena-agent` / `/arena-agent` | **`arena-cli` / `/arena-cli`** |
| 数据目录 | `~/.arena-agent` | **`~/.arena-cli`** |

### 数据目录：自动迁移，登录态不丢

`arena` 第一次运行时（任何子命令都算）会把 `~/.arena-agent` 搬到 `~/.arena-cli`：

- **新目录不存在** → 整个目录改名，状态文件、配置、浏览器 profile 原样带走。
- **新目录已经存在**（比如先跑过 `arena status`，`prof/`、`jobs/` 已经被建出来）→ 逐项合并：
  目标没有的文件搬过去，同名文件保留 mtime 更新的那个。所以不会把已有登录态盖掉。
- 迁移完在 `~/.arena-cli/.migrated-from-arena-agent` 写个标记，之后不再重扫 chromium 的大目录。
- 搬空后旧目录会被删掉；搬不动的（权限/占用）留在原地，可以直接删。
- 显式设置了 `ARENA_HOME` 时**不迁移**（那是你自己的目录，工具不碰）。

验证一下：

```bash
arena status        # 看「会话文件」是不是 ~/.arena-cli/state.json
arena check         # 是不是还登录着
```

### 技能目录：手动收尾

技能名变了，旧的技能目录不会自动删（否则可能删掉用户自己改过的东西）：

```bash
git pull && pip install -e .
./install.sh --force                # 装成 ~/.hermes/skills/arena-cli
rm -rf ~/.hermes/skills/arena-agent  # 删掉旧的，否则会同时有两个斜杠命令
```

`./install.sh` 发现旧目录时会提示，但不会替你删。

### 兼容性

- `arena` 的**命令、flag、退出码、JSON 信封**都没变，脚本不用改。
- `scripts/arena_agent.py` 老入口还在（转发到同一套引擎）。
- 只有「名字」变了：斜杠命令、数据目录、技能安装路径。改脚本里写死的 `~/.arena-agent` 即可。

## 2.3.0：工作区与代理

- 新增 `arena workspace list|show|download`（`--all` 打包 zip）。
- 新增 `--proxy`（单次 / `config set proxy` / `ARENA_PROXY` / `HTTPS_PROXY`），
  同时作用于浏览器和 `arena doctor` 的网络检查。

## 2.2.0：多轮与流式

- `arena ask --chat last|<id>|<序号>` 在已有会话里接着问。
- `arena ask --stream` 边生成边看（进度走 stderr，stdout 仍是最终回答）。
