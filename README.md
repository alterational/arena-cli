# arena-cli

用命令行（真实浏览器驱动）调用 **https://arena.ai/agent**：提问、等它跑完、把回答和产出的文件拿回来。

```bash
arena ask "帮我调研三家竞品的定价策略"          # 回答直接打到 stdout
arena workspace download --all --out ws.zip     # Agent 生成的文件打包带走
```

`arena.ai/agent` 是网页端的通用 agent（能调研、写文档、跑代码并产出文件），但没有开放 API；
这个项目补上命令行这一层：不用 API key，走的就是网页本身——登录一次（导入浏览器 cookies），之后一直是登录态。

## 为什么用它

- **一句话一个回答**：`arena ask` 提交后自动等到 Agent 执行完，stdout 只有回答正文，可以直接转发/管道。
- **多轮 + 后台**：`--chat last` 接着上一个会话问；`--no-wait` 挂后台，`arena jobs result` 取结果。
- **拿得到文件**：Agent 工作区里的 `.md`/`.csv` 直接 `show` 看、`download` 存。
- **给脚本和 agent 用**：`--json` 统一信封、失败带下一步提示、退出码可判断、`arena schema` 自省。

## 安装

```bash
git clone https://github.com/chenty/arena-cli.git && cd arena-cli
pip install -e .                          # 得到 arena 命令（也可 ./bin/arena 免安装跑）
python -m playwright install chromium     # 浏览器内核，一次性
```

在 Hermes 里用斜杠命令的话，再装一次技能：

```bash
./install.sh        # → ~/.hermes/skills/arena-cli，之后 /arena-cli 你的问题
```

## 登录（一次性）

推荐用浏览器导出的 cookies 文件（Netscape `cookies.txt` / Cookie-Editor JSON / Playwright storage_state，自动识别）：

```bash
arena cookies import ~/Downloads/arena.ai_cookies.txt
arena check                       # 确认还是登录态
```

没有 cookies 文件时走邮箱登录（会开浏览器，方便点邮件链接）：`arena login --email you@example.com --headed`。
多账号用 `--profile` 隔离：`arena profile create work --from work_cookies.txt` + `arena profile use work`。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `arena ask "问题"` | 提问并打印回答（`--json` / `--stream` / `--no-wait` / `--chat last`） |
| `arena check` | 登录态体检（`--probe` 真发一条验证） |
| `arena status` | 离线总览：会话、配置、后台任务（不开浏览器） |
| `arena cookies import\|export\|show` | cookies 导入 / 导出 / 看清单（只显示名字） |
| `arena chat list\|open` | 最近的会话 |
| `arena workspace list\|show\|download` | 会话工作区：列文件 / 看内容 / 下载（`--all` 打包） |
| `arena jobs list\|status\|result` | 后台任务管理（`ask --no-wait` 产生的） |
| `arena profile list\|create\|use` | 多套登录态切换 |
| `arena config list\|set` | 改超时、代理、选择器 |
| `arena doctor` | 环境体检：依赖 / 浏览器 / 网络 / 会话 / 技能安装 |

完整参数：`arena --help`、`arena schema <命令>`，或见 [docs/cli.md](docs/cli.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/cli.md](docs/cli.md) | 完整命令参考、全局参数、工作区、代理、典型用法 |
| [docs/auth.md](docs/auth.md) | cookies 三种格式、邮箱登录、多 profile |
| [docs/agent.md](docs/agent.md) | 给脚本/agent 的契约：JSON 信封、风险分级、退出码、后台任务 |
| [docs/hermes-skill.md](docs/hermes-skill.md) | 装成 Hermes 技能、入口契约、升级与迁移 |
| [docs/development.md](docs/development.md) | 目录结构、自测、改版维护、发布 |
| [docs/migration.md](docs/migration.md) | 从 `arena-agent`（2.3.x 及更早）升级 |

## 安全

`~/.arena-cli/state.json` 就是账号凭证（权限已设 600）：别提交到 git、别贴进聊天。
密码不经过本工具（邮箱 magic link / 验证码）。请只在本人账号、低频串行使用，遵守站点条款。

---

名字只有一个：仓库/包 `arena-cli`、命令 `arena`、Hermes 技能 `arena-cli`（`/arena-cli`）、数据目录 `~/.arena-cli`。
