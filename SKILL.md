---
name: arena-agent
description: 把用户的话交给 arena.ai/agent 执行，取回 Agent 的回答（支持上传 cookies 文件）
version: 2.1.0
author: taozi
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Browser, Automation, Arena, AgentMode, Cookies]
    requires_toolsets: [terminal]
    config:
      - key: arena.state_file
        description: 保存 Arena 登录会话（cookies）的快照文件路径
        default: "~/.arena-agent/state.json"
        prompt: Arena 会话快照路径
      - key: arena.timeout
        description: 等待 Agent 执行完成的最长秒数
        default: "900"
        prompt: 最长等待秒数
required_environment_variables: []
---

# Arena Agent Mode（浏览器提问 → 取回结果）

把用户的请求交给 **https://arena.ai/agent** 上运行着的 Agent，等它跑完，把回答原样带回。

一句话调用（推荐在此之后不要再自己加工）：

```bash
arena ask "<用户的原话>"
```

`arena` 命令没装进 PATH 时，用等价的技能内置入口（契约完全相同）：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/arena_ask.py "<用户的原话>"
```

## When to Use

- 用户说「用 Arena 的 Agent 帮我做 / 查 / 生成…」「把这段发给 arena agent 跑一下」「Agent Mode 那边结果怎么样」
- 用户要在**同一个会话里接着追问**（多轮上下文）：第二次起加 `--chat last`
- 用户上传了 arena.ai 的 cookies 文件，想直接借这个登录态提问
- 任务需要 Arena Agent 的长链执行能力（调研、多步操作、产出文件），而不是本机一次性回答

不适用：只是普通问答（直接答更快）；用户要的是本机浏览器操作（用 `browser_exec`）。

## Quick Reference

| 场景 | 命令 |
| --- | --- |
| 用户上传了 cookies 文件 | `arena --cookies "<文件>" ask "<用户原话>"` |
| 已有会话（之前导入过 cookies） | `arena ask "<用户原话>"` |
| 想拿结构化结果 | 加 `--json`（`{ok, identity, data, meta}`，`data` 含 `answer`/`url`/`chat_id`/`elapsed_seconds`） |
| 只要回答那一句 | 加 `--jq '.data.answer'`（不必再自己解析 JSON） |
| 任务很慢、不想阻塞 | `arena ask --no-wait "…"` → `arena jobs result last`（见下） |
| 发到聊天软件、怕太长 | 加 `--max-chars 3500`（超长自动截断并附会话链接） |
| 在已有会话里接着问（多轮） | `arena ask --chat last "<用户原话>"` · 也可 `--chat <chat_id>` 或 `--chat 1`（chat list 的序号） |
| 想边跑边看进度 | 加 `--stream`（增量打到 stderr，stdout 仍只有最终回答） |
| 先看会发生什么、不执行 | 任何写操作加 `--dry-run` |
| 会话还能用吗 | `arena check` |
| 一眼看全状态（不用开浏览器） | `arena status` |
| 最近聊了些什么 | `arena chat list` / `arena chat last` |
| 看 Agent 产出了哪些文件 | `arena workspace list`（默认最近会话，`--chat <id>` 指定） |
| 看某个文件的内容 | `arena workspace show <path>`（stdout 就是内容） |
| 把产出文件拿回来 | `arena workspace download <path> --out ./out/` |
| 打包整个工作区 | `arena workspace download --all --out ws.zip` |
| 内网 / 需要代理 | `--proxy http://127.0.0.1:7890`（或 `arena config set proxy …`） |
| 换一套登录态（多账号） | `arena profile create work --from <cookies> && arena profile use work` |
| 查某命令的参数/风险等级 | `arena schema ask` / `arena schema`（agent 自省用） |
| 环境体检 | `arena doctor` |
| 没有 `arena` 命令、也没 cookies | `python3 ${HERMES_SKILL_DIR}/scripts/arena_agent.py login --email <邮箱> --headed --wait 300` |

若 `arena` 不在 PATH 里，把上表的 `arena X` 换成 `python3 ${HERMES_SKILL_DIR}/scripts/arena_ask.py X`（仅 `ask`），
其它命令用 `python3 ${HERMES_SKILL_DIR}/scripts/arena_agent.py X`，或先跑一次 `${HERMES_SKILL_DIR}/install.sh`。

若配置里设过 `skills.config.arena.state_file` / `arena.timeout`，把它们作为 `--state-file` / `--timeout` 传进去。

## Procedure

1. **收集输入**：用户要交给 Arena 的原始文字。原样传，不要改写、不要自己删减（超长且用户同意时才拆分）。
2. **选入口**：
   - 用户这次上传了 cookies 文件 → 用 `--cookies "<绝对路径>"`，它等于「导入会话 + 提问」一步到位；
   - 之前已经导入过 → 直接跑，不加 `--cookies`。
3. **执行**：上面的命令给 300~900 秒预算（Arena 侧任务常常要几十秒到几分钟）。**不要并发**，一次只发一条。
   - 你的工具调用有时限（常见 60~120 秒）时，用后台任务把「发出去」和「取结果」拆成两步，不要一次阻塞到底：

     ```bash
     arena ask --no-wait --json "<用户原话>"      # → data.job_id（立刻返回）
     arena jobs status <job_id>                  # 看还在跑 / 已完成
     arena jobs result <job_id>                  # 取回答（= stdout 回答正文）
     ```

     `job_id` 也可以写 `last`：`arena jobs result last`。日志在 `arena jobs logs <job_id>`。
4. **交付**：stdout 就是回答正文，**原样转给用户**。
   - 多轮：用户接着追问时用 `arena ask --chat last "…"`（`--chat <chat_id>` / `--chat <序号>` 也可），
     这样 Arena 侧保留上下文，不用把上一轮内容再抄一遍；
   - 想让用户看到进度：加 `--stream`，中间过程走 **stderr**（不会污染 stdout 的回答正文，
     `--json` 同时用也没问题）。工具本身不支持"逐 token 回传"，这是轮询页面的增量输出。
   - 长回答（> 3500 字）：先给结论摘要 + `url` 会话链接，问用户要不要全文；
   - 带 `--json` 时把 `answer` 给用户，`url` 留在回复里方便回溯。
5. **失败处理**：命令会打印 `❌ <CODE>: <建议>` 并以非 0 退出。按下面的表处理，不要重复盲目重试。

## Pitfalls

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `❌ LOGIN_REQUIRED` | 会话无效/过期（匿名提问在 Arena 是发不出去的） | 请用户**导出 cookies 文件**再跑 `--cookies`；没有 cookies 才走 `login` |
| `❌ BLOCKED` | 撞上 Cloudflare 人机校验 | 让用户导出新鲜 cookies（含 `cf_clearance`）；或 `--headed` 由人过一次；降低频率 |
| `❌ TIMEOUT` | 超过等待上限还在跑 | 加 `--timeout 1800`，或把 `url` 交给用户自己看 |
| `❌ RATE_LIMITED` | Arena 限流（`create-chat` 返回 429，本账号请求过多/额度用尽） | **别重试**：告诉用户"这个账号被临时限流了，等几分钟再试"；要立刻继续就换一份 cookies（`arena profile create work --from <cookies> --yes`） |
| `❌ UI_CHANGED` + 用 `--chat` 时 | 会话页底部的「Was this task successful?」反馈卡顶掉了输入框 | 已自动关闭（Esc / ×）；仍失败就 `--headed` 看一眼 |
| `❌ UI_CHANGED` | 页面改版，选择器失效 | 见 `references/troubleshooting.md`，改 `~/.arena-agent/config.json` |
| `❌ SEND_FAILED` | 消息没提交 | 重试一次；再失败用 `--headed` 观察 |
| `❌ BAD_INPUT` | cookies 文件格式不认识 / 没给内容 | 支持 Netscape `cookies.txt`、EditThisCookie JSON、Playwright storage_state |

### 结构化错误与风险等级（和 lark-cli 一致的契约）

- 加 `--json`（或 `--jq`）时，stdout 是一个信封：成功 `{"ok": true, "identity": "<profile>", "data": {…}, "meta": {…}}`，
  失败 `{"ok": false, "identity": …, "error": {"type", "subtype", "message", "hint"}}` 且退出码非 0。
  **先读 `error.subtype` 决定下一步，`error.hint` 就是要执行的命令**。
- 每个命令有风险等级：`read`（不动状态）/ `write` / `high-risk-write`（覆盖已有登录态、删 profile）。
  `high-risk-write` 不带 `--yes` 会被拒绝（退出码 1，`confirmation_required`，hint 里给两条路）——
  **先问用户**，用户同意后再加 `--yes`，不要自己加。
- 不确定某个命令怎么调：`arena schema <命令>` 看参数、类型、默认值、风险等级，比翻 README 快。
- 用户要的成品在 Arena 工作区里（Agent 说"已生成 xxx.md"）时，别自己复述内容：
  `arena workspace list` 找到路径 → `arena workspace download <path> --out <本地>` 拿回来再交付。
  只有纯文本内容才用 `arena workspace show`。覆盖本地文件要 `--yes`（先问用户）。

其它注意：
- **绝不要**把 `state.json` 或 cookies 值回显到对话里，那是账号凭证（脚本本身也只打印 cookie 名字）。
- 用户没给 cookies 又催结果时，先说明「需要一次登录态」，别反复试。
- 只在用户本人授权下操作其账号；串行、低频使用。

## Verification

```bash
# 会话可用性（不打扰任何人）
arena check

# 端到端冒烟：应当返回 ok:true 且 answer 是 PONG
arena ask --json "Reply with exactly one word: PONG"
```

成功标志：`arena_ask.py` 退出码 `0`，stdout 非空且不含 `Was this task successful?`/`Copy` 这类页面 UI 文案。
另有 `arena status`（离线秒回）可先确认登录态，避免白等一次浏览器启动。

退出码：`0` 成功 · `1` 一般错误/需要确认 · `2` 未登录 · `3` 发送失败 · `4` 超时 · `5` 页面改版 ·
`6` 人机校验 · **`7` 被限流（429）** · `64` 用法错误。
