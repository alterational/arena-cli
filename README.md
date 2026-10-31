# arena-agent-browser

用真实浏览器（Playwright / browser-use 同款 Chromium）驱动 **https://arena.ai/agent** 的技能包：

> 打开页面 → 登录并**记住 cookies** → 把用户内容写进 Agent 输入框 → 提交 → 等 Agent 执行完 → 取回回答 → 转发给用户

本目录就是一个 **Hermes 技能**（name: `arena-agent`），装进 `~/.hermes/skills/` 后自动成为斜杠命令：

```bash
./install.sh          # 安装到 ~/.hermes/skills/arena-agent
/arena-agent 帮我调研三家竞品的定价策略          # 在 Hermes 里直接这样用
```

- `SKILL.md`：技能定义（frontmatter + When to Use / Quick Reference / Procedure / Pitfalls / Verification）
- `scripts/arena_ask.py`：**直接调用入口** —— 一句话进，回答正文出（stdout 可直接转发）
- `scripts/arena_agent.py`：确定性引擎（登录 / 提问 / 会话管理 / cookies 导入导出）
- `snippets/*.js`：只有 `browser_exec`（纯 JS）时的等价实现
- `references/troubleshooting.md`：DOM 结构、选择器、错误码维护手册

## 从源码安装

```bash
git clone https://github.com/chenty/arena-cli.git
cd arena-cli

pip install -r requirements.txt && python -m playwright install chromium   # 依赖
pip install -e .            # 得到 `arena` 命令（也可以 ./bin/arena 免安装跑）
./install.sh                # 装成 Hermes 技能（自动装到 ~/.hermes/skills/arena-agent）
```

仓库名（`arena-cli`）和技能名（`arena-agent`）故意不同：仓库是「CLI + 技能」的完整项目，
`install.sh` 总会把技能装成 `~/.hermes/skills/arena-agent/`，所以斜杠命令始终是 `/arena-agent`。

## arena CLI（命令体系）

安装成系统命令（任选其一）：

```bash
./bin/arena --help          # 免安装直接跑
pip install -e .            # 或装成真命令（编写 pyproject 的 console_scripts）
pipx install .              # 或隔离安装
```

命令结构是 `<命令> <子命令>`，**全局 flag 写在命令前或后都生效**：

```
arena ask [内容]              把内容发给 Agent，打印回答（--json 结构化 / --stream 流式 / --no-wait 后台 / --chat 多轮）
arena login                   登录并保存 cookies（--email / --code / --wait）
arena check                   登录态体检（--probe 会真发一条验证）
arena status                  离线总览：profile / 会话 / 配置 / 最近后台任务（不启动浏览器）
arena cookies import <文件>   导入 cookies（Netscape .txt / JSON / storage_state）
arena cookies export          导出当前会话（--out）
arena cookies show            查看 cookie 清单（只显示名字，不显示值）
arena chat list               最近会话（--limit）
arena chat last               最近一条会话的链接
arena chat open <id|序号|last> 打开会话（--browser 用系统浏览器）
arena config list|get|set|unset|path
arena profile list|show|create|use|delete    多套登录态切换
arena workspace list|show|download         会话工作区：列文件 / 看内容 / 下载（--all 打包）
arena jobs list|status|result|logs|cancel    后台任务（ask --no-wait 产生的）
arena schema [命令…]          命令自省：参数 / 类型 / 默认值 / 风险等级
arena doctor                  环境体检（依赖/浏览器/网络/会话/技能安装）
arena version
```

常用全局参数：`--json` · `--jq '<表达式>'` · `--dry-run` · `--yes` · `--no-wait` · `-q/--quiet` ·
`-v/--verbose` · `--no-color` · `--profile <名>` · `--config <文件>` · `--base-url` · `--timeout <秒>` ·
`--max-chars <N>` · `--cookies <文件>` · `--headed` · `--proxy <代理>`

### 工作区（Agent 产出的文件）

会话页右侧那张 Workspace 卡里的东西，直接用命令行拿：

```bash
arena workspace list                        # 列文件（自动展开文件夹，默认最近会话）
arena workspace list --chat last --json     # 结构化：{files:[{path,size,href}], stats:{used,quota,files}}
arena workspace show uploads/note.txt       # 打印内容（stdout 只有内容）
arena workspace download demo.txt --out ./  # 下载（--out 可以是文件或已存在的目录）
arena workspace download --all --out ws.zip # 整个工作区打包
```

路径写法：`uploads/note.txt`（完整）、`note.txt`（唯一时可直接写文件名）、`*/note.txt`（通配）。
覆盖已存在的本地文件需要 `--yes`；二进制文件 `show` 会拒绝并提示改用 `download`。

实现上有两条路：先读 Workspace 面板 DOM（快）；面板是另一种文件树 UI 认不出行时，
自动改用页面右上角「下载工作区」的 zip 来列目录/取内容（`data.source = "archive"` 会标出来）。

### 代理

```bash
arena --proxy http://127.0.0.1:7890 ask "…"          # 单次
arena config set proxy http://127.0.0.1:7890          # 持久（写进 config.json）
arena config set proxy socks5://127.0.0.1:1080        # socks5 也支持
arena config set proxy http://user:pass@10.0.0.1:8080 # 带认证（输出里密码会打码）
export ARENA_PROXY=http://127.0.0.1:7890              # 环境变量（HTTPS_PROXY/ALL_PROXY 也认）
```

优先级：`--proxy` > 配置文件 > `ARENA_PROXY` > `HTTPS_PROXY`/`ALL_PROXY` > 直连。
代理同时作用于**浏览器**（Playwright 透传，含 `--proxy-bypass-list=<-loopback>`，
否则 Chrome 会默认给 localhost 开白名单）和 `arena doctor` 的网络检查。
代理连不通会明确报 `BROWSER_ERROR`（不装成功）。

典型用法：

```bash
# 一句提问
arena ask "帮我调研三家竞品的定价策略"

# 上传 cookies → 体检 → 提问
arena cookies import ~/uploads/arena.ai_cookies.txt
arena check
arena ask --json "把结论整理成表格" --max-chars 3500

# 多轮：在刚才那个会话里接着问（--chat 支持 last / chat_id / chat list 的序号）
arena ask --chat last "把上面的结论翻译成英文"
arena ask --chat 1 "第一轮那个结论再展开一点"
arena ask --chat 01a19037-62e2-735d-937e-b500aa740edc "继续"

# 流式：边生成边看（进度走 stderr，stdout 仍只有最终回答）
arena ask --stream "写一篇 800 字的竞品分析"

# 流式 + 结构化可以同时用（JSON 最后一次性输出，中途增量在 stderr）
arena ask --stream --json "…" --jq '.data.answer'

# 多账号：工作号 / 个人号各一套登录态
arena profile create work --from ~/uploads/work_cookies.txt
arena profile use work
arena ask "用工作号跑一下"

# 页面改版时改选择器（不用改代码）
arena config set selectors '{"editor_selectors":["div.ProseMirror[contenteditable=\"true\"]"]}'
arena config set timeout 1800
arena config list

# 长任务不阻塞：挂后台 → 回来看结果
arena ask --no-wait --json "跑一个十分钟的调研"     # → data.job_id
arena jobs status last
arena jobs result last                            # stdout 就是回答正文

# 只要字段、不要人话
arena ask --json "…" --jq '.data.answer'
arena status --jq '.data.session.has_auth_cookie'
```

## agent 契约（对齐 lark-cli 的那套）

命令行工具给 agent 用时最怕三件事：输出不可预测、危险操作没有闸门、失败装成功。arena CLI 按 lark-cli 的体系做了五件事：

**1. 统一信封**（`--json` / `--jq` 时生效；不加仍是给人看的彩色表格）

```json
{ "ok": true, "identity": "default",
  "data": { "answer": "…", "url": "https://arena.ai/agent/…", "elapsed_seconds": 41 },
  "meta": { "version": "2.1.0" } }
```

失败退出码非 0，且带一张「下一步该干什么」的名片（不用去猜日志）：

```json
{ "ok": false, "identity": "default",
  "error": { "type": "auth", "subtype": "login_required",
             "message": "这个 profile 没有可用登录态",
             "hint": "arena cookies import <cookies 文件>  （或 arena login --email 你@邮箱）" } }
```

**2. 风险分级**：每个命令标 `read` / `write` / `high-risk-write`。
`high-risk-write`（删除 profile、覆盖已有登录态）不带 `--yes` 直接拒绝，退出码 1、`confirmation_required`，
hint 里同时给「加 `--yes`」和「换个 `--profile`」两条路 —— agent 必须先去问用户。

**3. `--dry-run` / `--jq` / `schema` 三件套**

```bash
arena --dry-run --json ask "问题"           # 先看清要做哪几步，一条都不执行
arena --json status | jq .                 # 或直接 --jq '.data.session.has_auth_cookie'
arena schema ask                           # 参数/类型/默认值/风险，agent 自省用（不用背文档）
arena schema                               # 全部命令一览
```

**4. 不假装成功**：页面乐观渲染的消息被服务端撤回（限流 429 时就是这样）会明确报 `RATE_LIMITED`（退出码 7），
而不是把整页文本差分当成回答 —— 曾经真的抽出过 `"Agent"` 这种控件文案。

**5. 后台任务**：`arena ask --no-wait` 立刻返回 `job_id`，任务在后台跑，
`arena jobs {list,status,result,logs,cancel}` 管理结果。状态落在 `~/.arena-agent/jobs/<job_id>/`，
进程死掉会被识别为 `stale`（不会永远 running）。

`arena --help` 顶部就是给 agent 的 **AGENT QUICKSTART**，示例按「最短路径 → 结构化 → 自省」排。

**退出码**（脚本可判断）：`0` 成功 · `1` 一般错误 · `2` 需要登录 · `3` 发送失败 · `4` 超时 ·
`5` 页面改版 · `6` 人机校验 · `7` 被限流（`create-chat` 429）· `64` 用法错误（argparse 默认的 2 会和"需要登录"撞车，已改掉）

**配置与 profile**：配置在 `~/.arena-agent/config.json`；一个 profile = 一套会话
（`~/.arena-agent/state.json` + `prof/` 是 default，其它在 `~/.arena-agent/profiles/<名>/`）。
优先级：命令行 flag > 环境变量（`ARENA_HOME` / `ARENA_PROFILE` / `ARENA_BASE_URL` / `NO_COLOR`）> 配置文件 > 内置默认。

## 装成 Hermes 技能（斜杠命令）

```bash
./install.sh                 # 复制到 ~/.hermes/skills/arena-agent
./install.sh --link          # 或软链（改代码即时生效，开发用）
HERMES_HOME=~/work/.hermes ./install.sh   # 装到指定 profile
python3 scripts/verify_skill.py           # 规范自检（frontmatter / 章节 / 脚本引用）
```

装好后每个技能自动变成斜杠命令，参数就是用户原话：

```text
/arena-agent 帮我调研三家竞品的定价策略
/arena-agent --cookies ~/uploads/arena.ai_cookies.txt 用这份 cookies 提问
```

技能里声明的入口（SKILL.md 用 `${HERMES_SKILL_DIR}` 模板变量，agent 拿到的就是绝对路径）：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/arena_ask.py "<用户原话>"
```

技能里的入口 `scripts/arena_ask.py` 契约很简单，方便 agent 直接转述：

| 情况 | stdout | 退出码 |
| --- | --- | --- |
| 成功 | **只有回答正文**（无 JSON、无日志、无页面 UI 噪音） | `0` |
| 失败 | 一行 `❌ <CODE>: <下一步该做什么>` | `2/3/4/5/6` |

可选参数：`--cookies <文件>`（导入+提问一步到位）、`--json`、`--max-chars 3500`（长回答截断+附会话链接）、`--timeout`、`--headed`、`-v`。

## 30 秒上手（不用 Hermes 也能跑）

```bash
pip install -r requirements.txt
python -m playwright install chromium

# 方式 1（推荐）：用户上传 cookies 文件，一条命令直接用
python scripts/arena_agent.py --cookies ~/uploads/arena.ai_cookies.txt ask "帮我调研三家竞品的定价策略" --format markdown

# 方式 2：没有 cookies 文件时，走邮箱登录（带窗口，方便过验证码 / 点邮件链接）
python scripts/arena_agent.py login --email you@example.com --headed --wait 300
python scripts/arena_agent.py ask "帮我调研三家竞品的定价策略" --format markdown
```

## 上传 cookies 文件（推荐）

支持三种格式，自动识别，**只输出 cookie 名字、绝不打印值**，导入后 `state.json` 权限自动 `600`：

| 格式 | 例子 |
| --- | --- |
| **Netscape `cookies.txt`**（curl / 扩展导出） | `arena.ai<TAB>FALSE<TAB>/<TAB>TRUE<TAB>1827972668<TAB>arena-auth-prod-v1.1<TAB>…` |
| EditThisCookie / Cookie-Editor JSON 数组 | `[{"name":"arena-auth-prod-v1.1","value":"…","domain":".arena.ai"}]` |
| Playwright storage_state | `{"cookies":[…],"origins":[…]}` |

```bash
python scripts/arena_agent.py import-cookies arena.ai_cookies.txt   # 导入（= 记住登录）
python scripts/arena_agent.py --cookies arena.ai_cookies.txt ask "问题"  # 导入 + 立刻提问
python scripts/arena_agent.py check                                  # 会话是否有效 + cookie 体检
```

`import-cookies` 输出：

```json
{ "ok": true, "format": "netscape", "count": 15, "domains": [".arena.ai", "arena.ai"],
  "auth_cookie_names": ["arena-auth-prod-v1.1", "arena-auth-prod-v1.0"],
  "expired_names": [], "has_auth_cookie": true }
```

`has_auth_cookie=false` 或 `check` 报 `logged_in=false` → 这份文件没有有效登录态，让用户重新导出。

## 目录

```
arena-agent-skill/
├── SKILL.md                  # 技能定义（Hermes 加载这个 → /arena-agent）
├── install.sh                # 装进 ~/.hermes/skills/（--force / --link）
├── README.md
├── config.example.json       # 选择器/行为参数默认值，放到 ~/.arena-agent/config.json 生效
├── requirements.txt
├── pyproject.toml            # pip install -e . → 得到 `arena` 命令
├── bin/arena                 # 免安装启动器（python -m arena）
├── arena/                    # CLI 包
│   ├── cli.py                #   命令分发 / 全局 flag / 退出码
│   ├── config.py             #   配置优先级与 profile
│   ├── session.py            #   会话开关 + 错误建议
│   ├── ui.py                 #   颜色/表格/JSON（中文对齐）
│   ├── engine.py             #   浏览器引擎（原 arena_agent.py）
│   ├── errors.py             #   错误码 → {type,subtype,message,hint}
│   ├── workspace.py          #   工作区：路径拼装/查找/文本判定/统计解析
│   ├── jq.py                 #   --jq 用的极简 jq（.a.b / [0] / [] / |）
│   ├── jobs.py               #   后台任务（--no-wait 的落地实现）
│   ├── spec.py               #   从 argparse 反射出 schema（agent 自省）
│   └── commands/             #   ask/auth/cookies/chat/config/profile/
│                             #   jobs/schema/status/doctor/workspace
├── scripts/
│   ├── arena_ask.py          # Hermes 技能入口：一句话进、回答正文出（契约不变）
│   ├── arena_agent.py        # 兼容旧入口（转发到引擎）
│   └── verify_skill.py       # SKILL.md 是否符合 Hermes 技能规范
├── references/
│   └── troubleshooting.md    # DOM 结构 / 选择器 / 错误码维护手册
├── snippets/                 # browser_exec（纯 JS）路线
│   ├── 01_fill_and_send.js
│   ├── 02_wait_and_read.js
│   ├── 03_login_email.js
│   ├── 03b_fill_code.js
│   └── 04_dump_session.js
└── tests/
    ├── mock_server.py        # 本地假站点，复刻真实 DOM
    ├── run_tests.py          # 引擎 + CLI（11 组）
    ├── run_js_tests.py       # snippets/*.js 纯 JS 路线
    ├── run_entry_tests.py    # 技能入口 + install.sh（26 项）
    ├── run_cli_tests.py      # arena CLI 命令体系（13 组 / 70+ 项：信封、风险、dry-run、jq、jobs、schema）
    └── run_all.sh            # 一键全跑
```

## 这个页面到底是什么结构（实测 2026-10）

| 元素 | 实际实现 |
| --- | --- |
| 输入框 | Tiptap/ProseMirror：`div.tiptap.ProseMirror[contenteditable="true"]`（不是 textarea） |
| 发送 | `button[aria-label="Send message"]`；输入为空时按钮禁用 |
| 对话回合 | `[data-agent-transcript-message]`；**用户回合**内含 `[data-user-message-body-row]`，回答回合没有 |
| 回答正文 | 回答回合里的 `.prose`（`1s · 2:17 AM`、`Was this task successful?` 等噪音在 `.prose` 之外，天然排除） |
| 首次访问 | `role=dialog` 弹 **Terms of Use**，必须点 **Agree** |
| 未登录 | 点发送才弹 **Log In or Create Account**：`Continue with Google`（OAuth）或邮箱 magic link；输入框 `input[type=email]`，按钮 `Continue with email` |
| 匿名可用性 | ❌ 匿名发送无效：消息留在输入框、不产生任何对话 → 必须登录 |
| 反爬 | 有 Cloudflare 托管挑战（"Performing security verification"），高频自动化会被拦 |
| 登录 cookie | **httpOnly**，`document.cookie` 读不到 → 所以用 Playwright 的 `storage_state` 落盘 |

两个容易踩的坑，代码里都处理了：

1. **直接改 `innerText`** 不会被 React/ProseMirror 接管 → 发送按钮仍然禁用。正确做法是派发 `paste` 事件（`scripts/arena_agent.py:fill_editor`）。
2. **等待结束**不能只 sleep：用"没有 Stop 按钮 + 文本连续 N 秒不变"双条件（`stable_seconds`，默认 2.5s）。

## 命令速查

```bash
arena_agent.py login  --email you@example.com [--code 123456] [--headed] [--wait 300]
arena_agent.py ask    "<内容>" | --file p.md | -        [--timeout 900] [--format json|text|markdown] [--out f.md]
arena_agent.py check  [--probe]
arena_agent.py export-cookies --out cookies.json
arena_agent.py import-cookies cookies.json

通用：--base-url  --profile-dir  --state-file  --cookies <文件>  --headed  --channel chrome  --user-agent  --config
```

退出码：`0` 成功 · `1` 输入/cookies 文件有问题 · `2` 需要登录 · `3` 发送失败 · `4` 等待超时 · `5` 页面结构变了 · `6` 被人机校验拦截

## 自测（不需要真账号）

```bash
tests/run_all.sh
```

覆盖：未登录必须报 `LOGIN_REQUIRED`（不许假成功）→ 登录并落盘 cookies → **换一个全新浏览器 profile、只带 state.json** 仍能直接提问（证明 cookies 真的记住了）→ 多行中文 → 长文本 + `<div>`/引号等特殊字符不被吃掉 → `check` 判定 → **上传 Netscape cookies.txt 一条命令直接提问** → 真实上传文件解析（含"输出不含 cookie 值"检查）→ 坏文件报 `BAD_INPUT`。

## 安全

- `~/.arena-agent/state.json` 就是账号凭证，脚本已设为 `600`；**不要**提交到 git、不要贴到聊天里。
- 登录密码不经过本技能：走的是邮箱 magic link / 一次性验证码。
- 只在用户本人授权下操作其账号，遵守目标站点条款与频率限制；建议串行、低频使用。
