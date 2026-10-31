# 维护手册：选择器、错误码、DOM 结构

页面改版时**只改配置**，不改代码：`cp config.example.json ~/.arena-agent/config.json` 后按需修改，
或给 `arena_ask.py` / `arena_agent.py` 传 `--config <文件>`。

## arena.ai/agent 的真实结构（实测 2026-10）

| 元素 | 选择器 | 说明 |
| --- | --- | --- |
| 输入框 | `div.tiptap.ProseMirror[contenteditable="true"]` | Tiptap/ProseMirror，**不是** textarea；改 `innerText` 无效，必须派发 `paste` 事件 |
| 发送按钮 | `button[aria-label="Send message"]` | 输入为空时 disabled |
| 停止/生成中 | `button[aria-label*="Stop" i]` | 出现即代表还在流式输出 |
| 对话回合 | `[data-agent-transcript-message]` | 用户、助手各一个 |
| 用户回合标记 | `[data-user-message-body-row]` | 只在用户回合里出现 |
| 回答正文 | `[data-agent-transcript-message]:not(:has([data-user-message-body-row])) .prose` | 时间戳、`Was this task successful?` 等噪音在 `.prose` 之外，天然被排除 |
| Terms 弹窗 | `[role="dialog"]:has-text("Terms of Use")` + 按钮 `Agree` | 首次访问必点 |
| 登录弹窗 | `[role="dialog"]:has-text("Log In or Create Account")` | 点发送才出现 |
| 登录邮箱框 | `[role="dialog"] input[type="email"]` | 按钮文案 `Continue with email` |
| 未登录落地页 | 正文含 `Experience the frontier` | 判断"没登录"的可靠信号 |
| 已登录标记 | `a[href="/history/search"]`、`button[aria-label*="New chat" i]` | 侧边栏历史入口 |

## 登录态怎么存

- 主存储：`~/.arena-agent/prof`（浏览器用户目录，最稳）
- 可移植快照：`~/.arena-agent/state.json`（cookies + localStorage，权限 600）
- 登录 cookie 是 **httpOnly**，`document.cookie` 读不到 → 必须用 Playwright 的 `storage_state`
- 关键凭据 cookie 名：`arena-auth-prod-v1.1` / `arena-auth-prod-v1.0`（配置项 `auth_cookie_patterns`）

## 等待完成的判定

"文本连续 `stable_seconds`（默认 2.5s）不变" **且** "没有 Stop 按钮" → 认为生成结束；
硬上限 `wait_timeout`（默认 900s）。只 sleep 固定时间会在长任务上误判。

## 错误码

| 码 | 退出码 | 处理 |
| --- | --- | --- |
| `LOGIN_REQUIRED` | 2 | 导入/更新 cookies，或 `login` |
| `LOGIN_FAILED` / `LOGIN_TIMEOUT` | 2 | 换真实邮箱域、加长 `--wait`、直接用 cookies |
| `SEND_FAILED` | 3 | 重试一次；仍失败 `--headed` 看页面 |
| `TIMEOUT` | 4 | `--timeout` 加长，或把 `url` 交给用户 |
| `UI_CHANGED` | 5 | 更新本文档/配置里的选择器 |
| `RATE_LIMITED` | 7 | `POST /nextjs-api/stream/create-chat` 返回 **429**：等几分钟再试，或换一份 cookies / 换 `--profile` |
| `BLOCKED` | 6 | 人机校验：新鲜 cookies / `--headed` 人工过 / 换网络 |
| `BAD_INPUT` | 1 | prompt 为空或 cookies 文件格式不认识 |

## 发送链路（2026-10 实测）

1. 落地页 `/agent` 是 Next.js SPA：`domcontentloaded` 时编辑器已在 DOM，但 React 事件还没挂上
   → 这时点发送会被吞掉。所以 `open_agent()` 之后必须 `settle()`（等 `networkidle` + 400ms）。
2. 真正发送时页面会 `POST /nextjs-api/stream/create-chat`；成功后 URL 变成 `/agent/<chat_id>`。
3. 被限流时该接口返回 **429**，页面会把乐观渲染的消息整条**撤回**、退回落地页（不弹任何错误提示）。
   所以「消息没了 + 落地页」必须判定为失败，绝不能把整页文本差分当成回答（曾经抽出过 `"Agent"` 这种控件文案）。
   现在的做法：监听 `create-chat` 的响应状态 + 检测「对话区消失」，报 `RATE_LIMITED`（退出码 7）。

## 多轮对话（`--chat`）

`arena ask --chat <chat_id|URL|last|序号>` 会先 `goto /agent/<id>`，再发新消息。

**坑**：一个已经跑完的会话，底部不是输入框，而是「**Was this task successful?**」反馈卡
（`Yes / No / Keep working` + 右上角 `×` / `Esc`）。它把 composer 顶掉了，所以 `wait_editor()`
会一直找不到输入框 → 报 `UI_CHANGED`。处理：`dismiss_feedback()` —— 认 `feedback_text` 文案，
优先点 `button[aria-label*="Close"]`，退路是按 `Esc`，然后重新等输入框。

另外 `last` / 序号（`arena chat list` 里的 `#1`）由 `commands/ask.resolve_chat()` 换成真 chat_id，
`Session.open_chat()` 只认 chat_id / URL，别直接把 `last` 传进去（会拼成 `/agent/last`）。

## 流式输出（`--stream`）

引擎每轮轮询都会 `extract_answer()`，`Session.ask(on_update=...)` 在文本变长时回调，
`commands/ask.Streamer` 把**增量**写到 stderr，stdout 仍然只有最终回答（`--json` 也不受影响）。
注意 `open_session()` 非 verbose 时会把 `sys.stderr` 重定向进 StringIO 吞掉 Playwright 噪音，
所以 Streamer 必须写 `sys.__stderr__`。轮询间隔在流式时降到 0.5s。

## 工作区（Workspace 面板）

`/agent/<chat_id>` 右侧那张卡片。实测**两种 UI 变体**：

1. **经典列表**（常见）：行是 `div[role="button"][aria-label="<文件名> file"]`，层级靠
   `style="padding-left: 8/24/40px"`（8 = 根，每级 +16），每行内嵌
   `<a download aria-label="Download <文件名>" href="/api/chat/workspace/cas/user/<hash>">`。
   折叠文件夹 `button[aria-label="Collapse|Expand <名字> folder"]`，**子项不在 DOM 里**，要点开才有。
2. **文件树**（大工作区/新版本）：`aria-label="Open file"` + `Expand` + `Copy`，没有按文件的
   `a[download]`，行名靠文本，路径也拿不准。

所以 `arena/engine.py` 的策略是：先按 (1) 读，读不出行但面板统计里有文件时，**回落到
页面右上角「下载工作区」的 zip**（`a[aria-label="Download workspace"]` →
`/api/chat/<chat>/workspace/<uuid>/download`）：`workspace_zip_entries()` 列目录，
`workspace_zip_read()` 取内容。`arena workspace list --json` 的 `data.source`
会标 `dom` 还是 `archive`。

坑：
- 面板头部有时先渲染骨架（`animate-pulse` 占位），stats 早于行出现 → 要轮询等。
- 统计文本可能是 `47B/128.0MB` + `2/10K files`，也可能是 `1.7K/10K files`（千位缩写）；
  `workspace.parse_stats()` 用 `re.search` 而不是整行匹配。
- 大工作区（29MB / 1.7K files）页面 `networkidle` 永远等不到 → 打开时用
  `domcontentloaded` + `wait_loaded()`，不要 `wait_editor()`（看文件用不着输入框）。

## 代理

- 浏览器：`launch_persistent_context(proxy=...)`，地址支持 `http/https/socks5`，`user:pass@` 会拆成
  `username/password`。**必须**同时加 `--proxy-bypass-list=<-loopback>`，否则 Chrome 默认给
  localhost 开直连白名单（测试脚本跑 mock 时会以为代理没生效）。
- `arena doctor` 的网络检查走 `urllib` + `ProxyHandler`，会标「（经代理）」。
- 输出/日志里用 `mask_proxy()` 把密码打码。
- 代理连不通 → 导航抛 `PWError` → 转成 `BROWSER_ERROR`（退出码 1），hint 提示先 `curl -x` 验证代理。
- 测试：`tests/proxy_server.py` 是个几十行的正向代理，断言「请求真的从代理过」而不是只看配置有没有写进去。

## 自测

```bash
python3 tests/run_tests.py      # 引擎 + CLI（本地 mock，12 组，含 429 限流）
python3 tests/run_js_tests.py   # snippets/*.js（纯 JS 路线）
python3 scripts/verify_skill.py # SKILL.md 是否符合 Hermes 技能规范
```
