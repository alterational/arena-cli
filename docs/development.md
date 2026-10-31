# 开发与维护

## 目录结构

```
arena-cli/
├── SKILL.md                  # Hermes 技能定义（frontmatter name: arena-cli → /arena-cli）
├── install.sh                # 装进 ~/.hermes/skills/（--force / --link）
├── README.md                 # 给用户看的（短）
├── docs/                     # 给开发/维护看的（这份）
├── config.example.json       # 选择器/行为参数默认值，放到 ~/.arena-cli/config.json 生效
├── requirements.txt
├── pyproject.toml            # pip install -e . → 得到 `arena` 命令
├── bin/arena                 # 免安装启动器（python -m arena）
├── arena/                    # CLI 包
│   ├── cli.py                #   命令分发 / 全局 flag / 退出码
│   ├── config.py             #   配置优先级、profile、旧数据目录迁移
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
    ├── proxy_server.py       # 本地代理，验证 --proxy 真的生效
    ├── run_tests.py          # 引擎 + CLI（11 组）
    ├── run_js_tests.py       # snippets/*.js 纯 JS 路线
    ├── run_entry_tests.py    # 技能入口 + install.sh + 数据目录迁移（8 组）
    ├── run_cli_tests.py      # arena CLI 命令体系（15 组：信封、风险、dry-run、jq、jobs、
    │                         #   schema、workspace、proxy）
    └── run_all.sh            # 一键全跑（5 阶段）
```

## 自测（不需要真账号）

```bash
tests/run_all.sh
```

`mock_server.py` 复刻了 arena.ai/agent 的真实 DOM（Tiptap 编辑器、`data-agent-transcript-message`
回合、Workspace 面板、429 场景），所以整套测试离线可跑。覆盖的关键行为：

未登录必须报 `LOGIN_REQUIRED`（不许假成功）→ 登录并落盘 cookies → **换一个全新浏览器 profile、
只带 state.json** 仍能直接提问（证明 cookies 真的记住了）→ 多行中文 → 长文本 + `<div>`/引号等
特殊字符不被吃掉 → `check` 判定 → **上传 Netscape cookies.txt 一条命令直接提问** →
真实上传文件解析（含「输出不含 cookie 值」检查）→ 坏文件报 `BAD_INPUT` →
`--no-wait` 后台任务 → `--proxy` 真的走代理（且坏代理要明确失败）→ 工作区 list/show/download →
旧数据目录迁移（改名 / 合并 / 只迁一次 / `ARENA_HOME` 优先）。

## 页面结构（改版时看这里）

`arena.ai/agent` 的真实结构、选择器、等待完成的判定、发送链路的坑，都记在
[references/troubleshooting.md](../references/troubleshooting.md)，实测日期写在每节标题里。
最常踩的两个坑：

1. **直接改 `innerText`** 不会被 React/ProseMirror 接管 → 发送按钮仍然禁用。正确做法是派发 `paste` 事件。
2. **等待结束**不能只 sleep：用「没有 Stop 按钮 + 文本连续 N 秒不变」双条件（`stable_seconds`，默认 2.5s）。

改选择器有两条路：临时用 `arena config set selectors '{…}'`（不用改代码），
或改 `arena/engine.py` 里的常量并补一条 mock DOM —— 后者要跑 `tests/run_all.sh` 确认没回归。

## 发布

```bash
# 1. 改版本：pyproject.toml + arena/__init__.py（两处一致）
# 2. 全量自测 + 规范自检
tests/run_all.sh && python3 scripts/verify_skill.py
# 3. 提交推送
git commit -am "feat: …，vX.Y.Z" && git push origin main
```

破坏性变更（技能名、数据目录、退出码含义）一定要在提交信息里写 `BREAKING CHANGE`，
并在 [migration.md](migration.md) 加一节升级说明。
