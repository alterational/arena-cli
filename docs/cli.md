# arena CLI 完整参考

面向要写脚本、或想用全所有功能的人。只想跑起来看 [README](../README.md) 就够；
`arena --help` / `arena schema <命令>` 永远是当前版本最准的一份。

## 安装方式（任选其一）

```bash
./bin/arena --help          # 免安装，直接跑仓库里的启动器
pip install -e .            # 装成真命令（pyproject 的 console_scripts）
pipx install .              # 隔离安装
```

命令结构是 `<命令> <子命令>`，**全局 flag 写在命令前或后都生效**（`arena --json ask "…"` = `arena ask "…" --json`）。

## 全部命令

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

## 全局参数

`--json` · `--jq '<表达式>'` · `--dry-run` · `--yes` · `--no-wait` · `-q/--quiet` · `-v/--verbose` ·
`--no-color` · `--profile <名>` · `--config <文件>` · `--base-url` · `--timeout <秒>` ·
`--max-chars <N>` · `--cookies <文件>` · `--headed` · `--proxy <代理>`

## 工作区（Agent 产出的文件）

会话页右侧那张 Workspace 卡里的东西，直接用命令行拿：

```bash
arena workspace list                        # 列文件（自动展开文件夹，默认最近会话）
arena workspace list --chat last --json     # {files:[{path,size,href}], stats:{used,quota,files}}
arena workspace show uploads/note.txt       # 打印内容（stdout 只有内容）
arena workspace download demo.txt --out ./  # 下载（--out 可以是文件或已存在的目录）
arena workspace download --all --out ws.zip # 整个工作区打包
```

路径写法：`uploads/note.txt`（完整）、`note.txt`（唯一时可直接写文件名）、`*/note.txt`（通配）。
覆盖已存在的本地文件需要 `--yes`；二进制文件 `show` 会拒绝并提示改用 `download`。

实现有两条路：先读 Workspace 面板 DOM（快）；面板是另一种文件树 UI、认不出行时，
自动改用页面右上角「下载工作区」的 zip 来列目录/取内容（`data.source = "archive"` 会标出来）。
DOM 细节见 [references/troubleshooting.md](../references/troubleshooting.md#工作区workspace-面板)。

## 代理

```bash
arena --proxy http://127.0.0.1:7890 ask "…"           # 单次
arena config set proxy http://127.0.0.1:7890          # 持久（写进 config.json）
arena config set proxy socks5://127.0.0.1:1080        # socks5 也支持
arena config set proxy http://user:pass@10.0.0.1:8080 # 带认证（输出里密码会打码）
export ARENA_PROXY=http://127.0.0.1:7890              # 环境变量（HTTPS_PROXY/ALL_PROXY 也认）
```

优先级：`--proxy` > 配置文件 > `ARENA_PROXY` > `HTTPS_PROXY`/`ALL_PROXY` > 直连。
代理同时作用于**浏览器**（Playwright 透传，含 `--proxy-bypass-list=<-loopback>`，否则 Chrome 会默认给
localhost 开白名单）和 `arena doctor` 的网络检查。代理连不通明确报 `BROWSER_ERROR`（不装成功）。

## 典型用法

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

## 旧入口 `scripts/arena_agent.py`

2.0 之前的唯一入口，现在还留着（转发到同一套引擎），老脚本不用改：

```bash
python scripts/arena_agent.py login  --email you@example.com [--code 123456] [--headed] [--wait 300]
python scripts/arena_agent.py ask    "<内容>" | --file p.md | -   [--timeout 900] [--format json|text|markdown] [--out f.md]
python scripts/arena_agent.py check  [--probe]
python scripts/arena_agent.py export-cookies --out cookies.json
python scripts/arena_agent.py import-cookies cookies.json

通用：--base-url  --profile-dir  --state-file  --cookies <文件>  --headed  --channel chrome  --user-agent  --config
```

新脚本请直接用 `arena`：多一层统一信封、风险闸门、`--jq`、后台任务。
